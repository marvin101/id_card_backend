# ID-Card Manager — FastAPI Backend

FastAPI service for the ID-Card Manager. It owns authentication, school-scoped authorization, academic data, student/card records, Card Designer templates, and student photo storage integration.

## Architecture

```text
Flutter Web (Vercel)
        |
        | HTTPS / JSON + Bearer token
        v
FastAPI (Render)
    |             |
    v             v
PostgreSQL    Supabase Storage
```

Source repository: `marvin101/id_card_backend`

The application uses PostgreSQL through SQLAlchemy and Alembic. Supabase Storage is used for persistent student photos; Render's local filesystem must not be treated as persistent production storage.

## Current capabilities

- JWT authentication with Argon2 password hashing
- User registration and authenticated profile lookup
- Platform-level and school-level roles
- Active, pending, revoked, and multi-school assignment handling
- School, academic session, class, and section APIs
- Student creation, search, filtering, updates, photo upload, and deletion controls
- Per-school card-template retrieval and administration
- CORS configuration for local Flutter development and the Vercel frontend
- API and database health endpoints

Public registration currently creates a user without granting school access. An administrator must assign and activate access before the user can work in a school. The planned public landing/registration UI is not part of this backend repository.

## Authorization model

Authorization is enforced in FastAPI even when the Flutter UI hides an action.

| Role | Backend scope |
| --- | --- |
| Platform Admin | All active schools; creates schools and manages elevated assignments and all application workflows |
| School Admin | Assigned schools; manages school structure, students/cards, templates, printing data, and ordinary teacher/staff assignments |
| Card Operator | Assigned schools; reads/adds/updates student card data and uploads photos only |
| Teacher / Staff | Assigned-school supporting access; no student/card-data operations under the current policy |

Additional rules:

- School access requires an active assignment for ordinary users.
- Multiple active assignments are supported and remain independently scoped.
- Pending or revoked assignments do not authorize school data.
- Student deletion remains an administrator operation.
- Card-template reads require school access; writes require a School Admin or Platform Admin.
- Elevated roles such as School Admin and Card Operator can be assigned only by a Platform Admin.
- The legacy school role value `admin` is accepted as a compatibility alias for `school_admin` where implemented.

## Technology

- Python and FastAPI
- Pydantic Settings
- SQLAlchemy 2
- PostgreSQL via Psycopg 3
- Alembic migrations
- PyJWT
- Argon2 password hashing
- Supabase Python client
- Pillow and multipart uploads
- Uvicorn

## Active project structure

```text
app/
  api/           route modules
  core/          configuration, database, security, authorization, storage
  models/        SQLAlchemy models
  schemas/       request and response models
  main.py        FastAPI application and router registration
migrations/      Alembic environment and revisions
uploads/         local development upload directory; not production persistence
alembic.ini      migration configuration
requirements.txt pinned Python dependencies
```

Some generated cross-platform client directories may exist in the repository, but the deployed backend application is the FastAPI code under `app/`.

## Prerequisites

- Python 3.11 or newer
- PostgreSQL database
- Supabase project and private server-side storage key

## Local setup

```powershell
git clone https://github.com/marvin101/id_card_backend.git
cd id_card_backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Complete `.env` locally. Never commit it or paste its values into issues, logs, frontend builds, or documentation.

Required configuration names:

| Variable | Purpose |
| --- | --- |
| `DB_HOST` | PostgreSQL host |
| `DB_PORT` | PostgreSQL port, normally `5432` |
| `DB_NAME` | Database name |
| `DB_USER` | Database user |
| `DB_PASSWORD` | Database password |
| `SECRET_KEY` | Long random JWT signing secret |
| `ALGORITHM` | JWT algorithm; current default is `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Bearer access-token lifetime in minutes; defaults to `30` |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SECRET_KEY` | Private server-side Supabase key |
| `CORS_ORIGINS` | Comma-separated allowed frontend origins |
| `AUTH_RATE_LIMIT_ENABLED` | Enables the process-local login and registration limiter; defaults to `true` |
| `AUTH_RATE_LIMIT_WINDOW_SECONDS` | Sliding-window duration; defaults to `60` |
| `LOGIN_RATE_LIMIT_REQUESTS` | Login attempts allowed per client address and window; defaults to `10` |
| `REGISTRATION_RATE_LIMIT_REQUESTS` | Registration attempts allowed per client address and window; defaults to `5` |
| `AUTH_RATE_LIMIT_TRUSTED_PROXY_HOPS` | Number of controlled reverse-proxy hops used to select the client address from the right of `X-Forwarded-For`; defaults to `0` |

`SUPABASE_URL` and `SUPABASE_SECRET_KEY` are required by `app/core/config.py`; add their names to a local `.env` even if an older `.env.example` does not yet list them.

## Database migrations

Apply all migrations before starting a new environment:

```powershell
alembic upgrade head
```

Create a migration only after reviewing model changes:

```powershell
alembic revision --autogenerate -m "describe_change"
```

Always inspect autogenerated migrations before applying or committing them.

## Run locally

```powershell
uvicorn app.main:app --reload
```

Useful endpoints:

- Health: `http://127.0.0.1:8000/health`
- Database health: `http://127.0.0.1:8000/health/check`
- Swagger UI: `http://127.0.0.1:8000/docs`
- OpenAPI schema: `http://127.0.0.1:8000/openapi.json`

`/health` is a liveness endpoint and does not query PostgreSQL.
`/health/check` is a readiness endpoint and returns HTTP `503` with a generic
disconnected response when PostgreSQL cannot be reached.

## API groups

| Prefix | Responsibility |
| --- | --- |
| `/auth` | Login and tokens |
| `/users` | Registration, profiles, users, and school assignments |
| `/schools` | School discovery and administration |
| `/schools/{school_uuid}/academic-sessions` | Academic sessions |
| `/schools/{school_uuid}/classes` | Classes |
| `/schools/{school_uuid}/classes/{class_uuid}/sections` | Sections |
| `/schools/{school_uuid}/students` | Student/card data and photo operations |
| `/schools/{school_uuid}/card-template` | Per-school Card Designer configuration |

Use the generated Swagger documentation for the exact request bodies, query parameters, and responses.

## Verification

For backend changes, at minimum:

```powershell
python -m compileall app
alembic current
```

Then start the service and verify `/health`, `/health/check`, and the affected endpoints. Authorization work should be tested with direct API calls across Platform Admin, School Admin, Card Operator, Teacher/Staff, unassigned, pending, revoked, and other-school cases. Do not rely on Flutter button visibility as proof of backend security.

The repository does not currently include a complete automated backend test suite. Add focused `pytest` coverage when extending authorization or data-sensitive behavior.

## Render deployment

The production service runs on Render with environment values configured in the Render dashboard.

Start command:

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Current production API: `https://id-card-backend-vcz5.onrender.com`

For the normal Render proxy topology, set
`AUTH_RATE_LIMIT_TRUSTED_PROXY_HOPS=1`. Confirm the actual proxy chain before
raising this value. The limiter is intentionally in memory and enforced per
application process. If the service is scaled to multiple workers or instances,
enforce the equivalent policy at Render's edge or replace it with a shared
limiter; it can be disabled with `AUTH_RATE_LIMIT_ENABLED=false` to avoid
double-throttling.

Before deployment:

1. Review and apply database migrations deliberately.
2. Configure all required environment variables in Render without exposing their values.
3. Confirm the Vercel production origin is allowed by CORS.
4. Deploy, then verify `/health`, `/health/check`, authentication, school scoping, uploads, and Card Designer authorization.

## Related client

Flutter source: `https://github.com/marvin101/idcard_flutter`

