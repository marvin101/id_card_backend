"""Read-only external smoke checks. Never logs bodies, credentials or capability URLs."""
import argparse
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def check(base, path, expected, *, method='GET', headers=None, predicate=None):
    started = time.perf_counter()
    request = Request(base.rstrip('/') + path, method=method, headers=headers or {})
    try:
        with urlopen(request, timeout=60) as response:
            status = response.status
            response_headers = response.headers
            body = response.read()
    except HTTPError as error:
        status, response_headers, body = error.code, error.headers, error.read()
    except Exception as error:
        return {'path': path, 'method': method, 'passed': False, 'error_type': type(error).__name__}
    passed = status == expected and (predicate is None or predicate(response_headers, body))
    return {'path': path, 'method': method, 'status': status, 'passed': bool(passed), 'seconds': round(time.perf_counter() - started, 3)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', required=True)
    parser.add_argument('--frontend', required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    origin = args.frontend.rstrip('/')
    checks = [
        check(args.backend, '/health', 200, predicate=lambda h, b: json.loads(b)['status'] == 'ok'),
        check(args.backend, '/health/check', 200, predicate=lambda h, b: json.loads(b)['database'] == 'connected'),
        check(args.backend, '/openapi.json', 200, predicate=lambda h, b: json.loads(b)['info']['version'] == '0.10.0' and '/public/verifications/{token}' in json.loads(b)['paths']),
        check(args.backend, '/auth/login', 200, method='OPTIONS', headers={'Origin': origin, 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type'}, predicate=lambda h, b: h.get('Access-Control-Allow-Origin') == origin),
        check(args.backend, '/auth/login', 400, method='OPTIONS', headers={'Origin': 'https://unapproved.example', 'Access-Control-Request-Method': 'POST'}, predicate=lambda h, b: h.get('Access-Control-Allow-Origin') is None),
        check(args.backend, '/users/me', 401),
        check(args.backend, '/public/forms/readiness-invalid-token', 404, predicate=lambda h, b: json.loads(b).get('detail') == 'Public form not found'),
        check(args.backend, '/public/designs/readiness-invalid-token', 404, predicate=lambda h, b: json.loads(b).get('detail') == 'Public design not found'),
        check(args.backend, '/public/verifications/readiness-invalid-token', 404, predicate=lambda h, b: h.get('Cache-Control') == 'no-store'),
    ]
    for path in ['/', '/dashboard', '/design', '/cards', '/teachers', '/staff', '/public/forms/readiness-invalid-token', '/public/designs/readiness-invalid-token', '/verify/readiness-invalid-token']:
        checks.append(check(args.frontend, path, 200, predicate=lambda h, b: b'<html' in b.lower()))
    result = json.dumps({'checks': checks, 'passed': all(c['passed'] for c in checks)}, indent=2)
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(result + '\n', encoding='utf-8')
    print(result)
    return 0 if all(c['passed'] for c in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
