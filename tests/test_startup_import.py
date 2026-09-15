import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core import file_storage
from app.core.database import get_db
from app.main import app


def test_fresh_import_has_no_external_connections_or_supabase_sdk():
    script = """
import socket
import psycopg

def forbidden(*args, **kwargs):
    raise AssertionError('external connection during import')

socket.socket.connect = forbidden
socket.create_connection = forbidden
psycopg.connect = forbidden
import app.main
import sys
from app.core import file_storage
assert 'supabase' not in sys.modules
assert file_storage._supabase_client is None
assert app.main.app.openapi()['info']['version'] == '0.10.0'
"""
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_storage_client_is_lazy_and_reused_under_concurrent_use(monkeypatch):
    calls = []
    client = SimpleNamespace(storage=object())

    def create_client(url, key):
        calls.append((url, key))
        return client

    monkeypatch.setattr(file_storage, '_supabase_client', None)
    monkeypatch.setitem(sys.modules, 'supabase', SimpleNamespace(create_client=create_client))
    assert file_storage.get_storage_public_url(None) is None
    assert calls == []
    with ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(lambda _: file_storage.get_supabase_client(), range(16)))
    assert all(value is client for value in clients)
    assert len(calls) == 1


def test_failed_client_creation_can_be_retried(monkeypatch):
    calls = []
    client = object()

    def create_client(*args):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('unavailable')
        return client

    monkeypatch.setattr(file_storage, '_supabase_client', None)
    monkeypatch.setitem(sys.modules, 'supabase', SimpleNamespace(create_client=create_client))
    import pytest
    with pytest.raises(RuntimeError):
        file_storage.get_supabase_client()
    assert file_storage._supabase_client is None
    assert file_storage.get_supabase_client() is client


def test_health_never_uses_database_or_storage(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('liveness must not access dependencies')

    monkeypatch.setattr(file_storage, 'get_supabase_client', forbidden)
    app.dependency_overrides[get_db] = forbidden
    try:
        with TestClient(app) as client:
            assert client.get('/health').status_code == 200
            assert client.get('/').status_code == 404
        assert app.openapi()['info']['version'] == '0.10.0'
    finally:
        app.dependency_overrides.clear()
