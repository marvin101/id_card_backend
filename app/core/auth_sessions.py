"""Durable sessions: absolute expiry, hashed rotating credentials, row locking."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from hmac import compare_digest
from uuid import UUID, uuid4

import jwt
from fastapi import HTTPException
from sqlalchemy import delete, select, update
from app.core.config import settings
from app.core.security import create_access_token, INVALID_CREDENTIALS_DETAIL
from app.models.auth_session import AuthSession
from app.models.users import User


def invalid_session():
    return HTTPException(401, INVALID_CREDENTIALS_DETAIL, headers={"WWW-Authenticate": "Bearer"})


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def decode_refresh_token(token):
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm],
                             options={"require": ["sub", "exp", "typ", "sid", "jti"]})
        if payload["typ"] != "refresh":
            raise ValueError()
        UUID(payload["sid"])
        UUID(payload["sub"])
        return payload
    except (jwt.PyJWTError, ValueError, TypeError, AttributeError):
        raise invalid_session() from None


def require_live_session(db, payload, user, *, lock=False):
    try:
        sid = UUID(payload["sid"])
    except (KeyError, ValueError, TypeError, AttributeError):
        raise invalid_session() from None
    query = select(AuthSession).where(AuthSession.id == sid, AuthSession.user_id == user.id)
    if lock:
        query = query.with_for_update()
    session = db.execute(query).scalar_one_or_none()
    if session is None or session.revoked_at is not None or _utc(session.expires_at) <= datetime.now(timezone.utc):
        raise invalid_session()
    return session


def issue_tokens(session, user):
    now = datetime.now(timezone.utc)
    expires_at = _utc(session.expires_at)
    refresh = jwt.encode({"sub": str(user.uuid), "sid": str(session.id), "jti": str(uuid4()),
                          "typ": "refresh", "exp": expires_at, "iat": now},
                         settings.secret_key, algorithm=settings.algorithm)
    session.refresh_digest = sha256(refresh.encode()).hexdigest()
    access_delta = min(timedelta(minutes=settings.access_token_expire_minutes), expires_at - now)
    return {"access_token": create_access_token(str(user.uuid), access_delta, session.id),
            "refresh_token": refresh, "token_type": "bearer",
            "expires_in": max(0, int(access_delta.total_seconds())),
            "refresh_expires_in": max(0, int((expires_at-now).total_seconds()))}


def start_session(db, user):
    db.execute(
        delete(AuthSession).where(
            AuthSession.expires_at <= datetime.now(timezone.utc)
        ).execution_options(synchronize_session=False)
    )
    session = AuthSession(id=uuid4(), user_id=user.id,
                          expires_at=datetime.now(timezone.utc)+timedelta(minutes=settings.refresh_token_expire_minutes),
                          revoked_at=None)
    tokens = issue_tokens(session, user)
    db.add(session)
    return tokens


def refresh_session(db, token):
    payload = decode_refresh_token(token)
    # Serialize with account deactivation/password changes, then session rotation.
    user = db.execute(select(User).where(User.uuid == UUID(payload["sub"])).with_for_update()).scalar_one_or_none()
    if user is None or not user.is_active:
        raise invalid_session()
    session = require_live_session(db, payload, user, lock=True)
    if not compare_digest(session.refresh_digest, sha256(token.encode()).hexdigest()):
        # A valid but stale rotating credential indicates replay. The Flutter
        # client serializes refreshes, so revoke the complete session.
        session.revoked_at = datetime.now(timezone.utc)
        db.commit()
        raise invalid_session()
    result = issue_tokens(session, user)
    db.commit()
    return result


def revoke_user_sessions(db, user_id, *, except_session_id: UUID | None = None):
    query = update(AuthSession).where(
        AuthSession.user_id == user_id,
        AuthSession.revoked_at.is_(None),
    )
    if except_session_id is not None:
        query = query.where(AuthSession.id != except_session_id)
    db.execute(query.values(revoked_at=datetime.now(timezone.utc)))
