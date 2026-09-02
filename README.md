# CampusID — FastAPI Backend

CampusID's FastAPI service is the security and data boundary for school-scoped identity-card workflows. It provides authentication, authorization, academic and student records, Public Forms, bulk ingestion, Card Designer templates, audit history, and storage integration for the Flutter client.

## Current release

- **CampusID v0.7.0** — the Excel Grid release.
- Includes the production-smoke-tested grid API, Public Forms from the 0.6.x milestone, student lifecycle and audit controls, bulk imports, dynamic student fields, and the current card/PDF workflow.
- Remains pre-1.0 while Designer v2, digital identity, advanced print production, and other roadmap modules are still in development.

## Architecture

```text
Flutter Web
   (Vercel)
       |
       | HTTPS / JSON + JWT bearer token
       v
FastAPI API
   (Render)
       |
       +-------------------------+
       |                         |
       v                         v
Supabase PostgreSQL       Supabase Storage
schools, users, students,  school logos, student photos,
forms, templates, audits   and temporary bulk-photo objects
```

The backend accesses PostgreSQL through SQLAlchemy and Alembic. Supabase Storage holds persistent media; Render's local filesystem is not production persistence.

## Core capabilities

- JWT authentication with Argon2 password hashing and configurable token expiry
- Platform Admin, School Admin, Card Operator, Teacher, and Staff authorization
- Active, pending, revoked, and multi-school access assignments
- Registration against an active school with a pending access-request workflow
- School profile and logo management
- Academic sessions, classes, and sections
- School-scoped student CRUD, search, filtering, and photo management
- Dynamic per-school student fields
- Excel student import with template, upload, preview, and commit stages
- Bulk student photo upload, matching preview, promotion, and cleanup
- Pending / Needs Correction / Verified lifecycle, print/reprint tracking, and audit history
- Public Forms with school branding, configured fields, optional or required photo, and anonymous submission
- Excel Grid paging, filtering, search, inline bulk updates, conflict detection, and structured validation errors
- Per-school Card Designer template storage
- CORS, request-size controls, authentication/Public Form throttling, and health endpoints

## Authorization model

FastAPI authorizes every protected request. Frontend visibility is only a usability layer.

| Role | Backend scope |
| --- | --- |
| Platform Admin | Platform-wide access to active schools, elevated role assignment, school administration, and all application workflows |
| School Admin | Assigned school(s); school structure/profile, ordinary Teacher/Staff assignments, students, lifecycle/audit, printing, templates, Public Forms, and imports |
| Card Operator | Assigned school(s); student card data, photos, imports, grid editing, and printing; no school configuration or student deletion |
| Teacher / Staff | Assigned-school supporting access; currently excluded from student/card-data operations |

School-scope rules:

- Ordinary users require an assignment to the requested school; multiple assignments remain independently scoped.
- Pending or revoked access does not authorize school data.
- Platform Admins bypass school membership checks.
- School Admins may manage ordinary Teacher/Staff roles only. Platform Admin authority is required for elevated assignments.
- Student deletion, school configuration, and template writes require School Admin or Platform Admin authority.
- The legacy school role value `admin` is accepted as a compatibility alias where the school-admin path explicitly allows it.

## API groups

| Prefix | Responsibility |
| --- | --- |
| `/auth` | Login and bearer-token issuance |
| `/users` | Registration, current profile, users, access requests, and school assignments |
| `/schools` | School discovery, administration, profile, and logo operations |
| `/schools/{school_uuid}/academic-sessions` | Academic sessions |
| `/schools/{school_uuid}/classes` | Classes |
| `/schools/{school_uuid}/classes/{class_uuid}/sections` | Sections |
| `/schools/{school_uuid}/students` | Student CRUD, filters, photos, verification, print tracking, batch lifecycle actions, and history |
| `/schools/{school_uuid}/student-fields` | Dynamic student-field definitions and ordering |
| `/schools/{school_uuid}/students/imports` | Excel template, upload, preview, and commit |
| `/schools/{school_uuid}/student-photos/bulk` | Bulk-photo upload, preview, and commit |
| `/schools/{school_uuid}/public-form` | Authenticated Public Form configuration and link regeneration |
| `/public/forms/{token}` | Anonymous Public Form read; submissions use `/public/forms/{token}/submissions` |
| `/schools/{school_uuid}/students/grid` | Bounded grid read and atomic bulk patch |
| `/schools/{school_uuid}/card-template` | Per-school Card Designer template |

Use `/docs` or `/openapi.json` for exact methods, query parameters, request bodies, and response schemas.

## Student lifecycle and audit

`verification_status` is one of **Pending**, **Needs Correction**, or **Verified**. Verification metadata and correction notes are managed through dedicated lifecycle operations rather than ordinary student edits.

Printing is a separate dimension: a student may have print timestamps, the responsible user, and a print count regardless of the verification label. Individual and batch endpoints record printing/reprinting without collapsing it into verification state.

Student history records meaningful field, lifecycle, photo, import, print, and Public Form events. Audit entries are school scoped, and update paths record changes only when values actually change.

## Public Forms

- Authenticated administrators configure school branding, enabled fields, custom fields, photo policy, form state, and success text.
- Anonymous GET and POST operations are addressed by a cryptographically generated, non-enumerable token.
- A submission creates a **Pending** student in the form's school.
- The public payload cannot set verification, correction, print, audit, or other internal fields.
- The backend validates school-owned academic choices, selected custom fields, required fields, admission-number duplication, file type/size, and request size.
- Photos are uploaded through the backend only and stored through the managed storage layer.
- The audit source is recorded as `Submitted through Public Form`.
- Token reads and submissions have separate configurable rate limits.

## Bulk import and storage

The Excel workflow provides a generated template, accepts an upload, returns a validation preview, and commits accepted rows only after confirmation. It validates academic relationships, required fields, duplicates, and configured custom fields.

Bulk-photo uploads are staged as temporary objects in Supabase Storage. PostgreSQL manifests contain metadata only—never base64 content or raw image bytes. Preview matches staged files to school-scoped students; commit promotes accepted images to managed student-photo paths and updates records. Failure, expiry, and commit paths clean temporary or replaced objects as appropriate.

## Excel Grid API

`GET /schools/{school_uuid}/students/grid` supports offset paging, search, and academic session/class/section filters. The page limit defaults to 100 and is bounded to 200. Responses include active custom-field definitions and school-owned academic lookup data.

`PATCH /schools/{school_uuid}/students/grid` accepts multiple row edits and:

- permits only a defined whitelist of student fields plus active, school-owned custom fields;
- validates academic dropdown relationships and uniqueness constraints;
- compares each supplied `expected_updated_at` with the current row to detect optimistic-concurrency conflicts;
- returns structured errors containing `student_uuid`, `field`, and `message`;
- validates the whole request before committing, so a failed row prevents all rows from saving;
- rolls back on conflicts or database errors; and
- updates timestamps and writes audit events only for values that actually changed.

## Technology

- Python 3.11+
- FastAPI and Uvicorn
- Pydantic Settings
- SQLAlchemy 2 and Psycopg 3
- PostgreSQL and Alembic
- PyJWT and Argon2
- Supabase Python client and Supabase Storage
- Pillow, multipart uploads, and standard-library XLSX/ZIP/XML processing
- pytest and HTTPX for focused tests

## Project structure

```text
app/
  api/                 FastAPI route modules
  core/                config, database, security, authorization, storage, imports, audit
  models/              SQLAlchemy models
  schemas/             request and response models
  main.py              application creation, middleware, routers, health endpoints
  version.py           authoritative backend product version
migrations/
  versions/            Alembic revisions
tests/                 focused backend test suite
uploads/               local-development uploads; not production persistence
.env.example           environment-variable template
alembic.ini            Alembic configuration
requirements.txt       runtime dependencies
requirements-dev.txt   test/development dependencies
```

Only `app/`, `migrations/`, and supporting backend files are part of the deployed service; generated or legacy cross-platform directories are not backend runtime code.

## Prerequisites

- Python 3.11 or newer
- PostgreSQL database
- Supabase project with a private server-side Storage key

## Local setup

```powershell
git clone https://github.com/marvin101/id_card_backend.git
cd id_card_backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Complete `.env` locally. Never commit it or expose its values in logs, issues, frontend builds, or documentation.

## Configuration

The authoritative settings are in `app/core/config.py`. Environment names currently read by the application are:

| Variable | Purpose |
| --- | --- |
| `DB_HOST` | PostgreSQL host |
| `DB_PORT` | PostgreSQL port |
| `DB_NAME` | Database name |
| `DB_USER` | Database user |
| `DB_PASSWORD` | Database password |
| `SECRET_KEY` | JWT signing secret |
| `ALGORITHM` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Bearer-token lifetime |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SECRET_KEY` | Private server-side Supabase key |
| `CORS_ORIGINS` | Comma-separated allowed frontend origins |
| `AUTH_RATE_LIMIT_ENABLED` | Enables process-local authentication and Public Form throttling |
| `AUTH_RATE_LIMIT_WINDOW_SECONDS` | Sliding-window duration |
| `LOGIN_RATE_LIMIT_REQUESTS` | Login requests allowed per client/window |
| `REGISTRATION_RATE_LIMIT_REQUESTS` | Registration requests allowed per client/window |
| `PUBLIC_FORM_GET_RATE_LIMIT_REQUESTS` | Public Form reads allowed per client/window |
| `PUBLIC_FORM_SUBMIT_RATE_LIMIT_REQUESTS` | Public Form submissions allowed per client/window |
| `PUBLIC_FORM_MAX_REQUEST_BYTES` | Maximum anonymous submission request size |
| `AUTH_RATE_LIMIT_TRUSTED_PROXY_HOPS` | Trusted reverse-proxy hops used to resolve the client address |

These names are case-insensitive through Pydantic Settings. Keep all values server-side.

## Database migrations

Apply the current schema:

```powershell
python -m alembic upgrade head
```

Create and inspect a migration when models intentionally change:

```powershell
python -m alembic revision --autogenerate -m "describe_change"
```

Production environments must be upgraded before deploying application code that depends on new tables or columns. Review generated SQL and backups/rollback plans before applying production migrations.

## Run locally

```powershell
python -m uvicorn app.main:app --reload
```

## Health and API docs

- Liveness: `http://127.0.0.1:8000/health`
- Database readiness: `http://127.0.0.1:8000/health/check`
- Swagger UI: `http://127.0.0.1:8000/docs`
- OpenAPI schema: `http://127.0.0.1:8000/openapi.json`

`/health` does not query PostgreSQL. `/health/check` returns HTTP 503 with a generic disconnected response when the database is unavailable.

## Testing

The repository includes focused pytest coverage for authorization matrices, endpoint integration, authentication configuration/rate limiting, school access requests and profiles, dynamic student fields, Excel imports, bulk-photo storage, student photos, lifecycle/audit behavior, Public Forms, and the Excel Grid.

```powershell
python -m pytest
python -m compileall app
python -m alembic heads
```

Tests use isolated fixtures and do not replace production smoke testing across each role and school boundary.

## Production deployment

The production service runs on Render. Configure environment values in the Render dashboard and use:

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Current production API: `https://id-card-backend-vcz5.onrender.com`

Before deployment:

1. Review and apply required Alembic migrations to the target database.
2. Configure secrets and all required environment variables in Render.
3. Confirm the Vercel production origin is allowed by CORS.
4. Confirm Supabase Storage credentials and bucket policies support server-side logo/photo operations.
5. Verify health, authentication, school scoping, Public Forms, imports, uploads, grid saves, templates, and PDF-facing data after deployment.

The built-in limiter is process local. For the normal Render proxy topology, `AUTH_RATE_LIMIT_TRUSTED_PROXY_HOPS=1` may be appropriate, but the deployed proxy chain must be verified. Multi-worker or multi-instance deployments need equivalent edge enforcement or a shared limiter.

## Versioning

CampusID follows Semantic Versioning: `MAJOR.MINOR.PATCH`. Backend and Flutter currently share one product version. The API's authoritative version is `app/version.py`, and FastAPI exposes it in OpenAPI metadata.

The current release is `0.7.0`: `0.6.x` represented Public Forms and `0.7.0` adds the Excel Grid milestone. Pre-1.0 minor releases may still introduce substantial product changes.

## Roadmap

- Designer v2
- QR/barcode and digital identity
- Advanced print production and Print Basket
- Teacher and non-teaching staff workflows
- School collaboration
- Photo Studio
- White-label and lanyard workflows
- AI OCR (deferred)

## Related client

Flutter client: `https://github.com/marvin101/idcard_flutter`
