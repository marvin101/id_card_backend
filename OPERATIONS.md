# CampusID Operations

This runbook describes the current production design and the checks operators must complete before launch or deployment. It contains variable names only; never copy real credentials into this file, source control, client builds, tickets, or logs.

## Production architecture

```text
Flutter Web on Vercel
        |
        | HTTPS / JSON with bearer access token
        v
FastAPI on Render
        |
        +--> Supabase PostgreSQL (persistent application data)
        |
        +--> Supabase Storage `student-photos` bucket (student photos)
```

Render's filesystem is ephemeral and must not be treated as persistent application storage. The local `uploads/` directory and `/media` mount support the application layout but are not the production photo store.

## Render environment

The names below come from `app/core/config.py` and `.env.example`. Required values must be stored in Render's environment configuration, not in the Flutter application.

| Variable | Operational purpose |
| --- | --- |
| `DB_HOST` | PostgreSQL host |
| `DB_PORT` | PostgreSQL port; defaults to `5432` |
| `DB_NAME` | PostgreSQL database name |
| `DB_USER` | PostgreSQL user |
| `DB_PASSWORD` | PostgreSQL password |
| `SECRET_KEY` | JWT signing secret |
| `CREDENTIAL_SIGNING_KEY` | Stable production-only signing secret for public student credentials |
| `ALGORITHM` | JWT algorithm; defaults to `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access-token lifetime; defaults to `30` |
| `SUPABASE_URL` | Supabase project URL used by Storage |
| `SUPABASE_SECRET_KEY` | Private server-side Supabase key used by Storage |
| `CORS_ORIGINS` | Comma-separated allowed frontend origins |
| `AUTH_RATE_LIMIT_ENABLED` | Enables process-local authentication and public-route throttling |
| `AUTH_RATE_LIMIT_WINDOW_SECONDS` | Rate-limit window |
| `LOGIN_RATE_LIMIT_REQUESTS` | Login requests allowed per client and window |
| `REGISTRATION_RATE_LIMIT_REQUESTS` | Registration requests allowed per client and window |
| `PUBLIC_FORM_GET_RATE_LIMIT_REQUESTS` | Public-form reads allowed per client and window |
| `PUBLIC_FORM_SUBMIT_RATE_LIMIT_REQUESTS` | Public-form submissions allowed per client and window |
| `PUBLIC_DESIGN_GET_RATE_LIMIT_REQUESTS` | Public-design reads allowed per client and window |
| `PUBLIC_VERIFICATION_GET_RATE_LIMIT_REQUESTS` | Public student-verification reads allowed per client and window |
| `PUBLIC_FORM_MAX_REQUEST_BYTES` | Maximum anonymous public-form request size |
| `PUBLIC_APP_URL` | Canonical Flutter origin placed in student-verification QR links |
| `AUTH_RATE_LIMIT_TRUSTED_PROXY_HOPS` | Controlled reverse-proxy hops used to resolve client addresses |

Before deployment, confirm every required value is present, `SECRET_KEY` and `CREDENTIAL_SIGNING_KEY` are separate strong production-only values, the Vercel production origin is allowed by `CORS_ORIGINS`, and the trusted proxy-hop count matches Render's actual topology. Do not expose signing keys, `SUPABASE_SECRET_KEY`, or any database credential to Flutter Web.

## Health and readiness

- `GET /health` is a process/liveness check. It does not query PostgreSQL.
- `GET /health/check` is a database-readiness check. It returns HTTP `503` with a generic response when the database check fails.

Use liveness to determine whether the FastAPI process responds. Use readiness before sending production traffic and after deployments or database maintenance.

## Authentication and sessions

CampusID uses bearer access tokens. Their lifetime is controlled by `ACCESS_TOKEN_EXPIRE_MINUTES`. There is no refresh-token infrastructure; after expiration, clients must clear the session and the user must authenticate again.

Changing `SECRET_KEY` invalidates all login JWTs signed with the previous key. Plan that rotation as a forced sign-in event and verify authentication immediately afterward. Changing `CREDENTIAL_SIGNING_KEY` invalidates every signed credential already printed on a card; rotate it only through an explicit card-reissuance incident procedure. When the dedicated key is omitted, credentials fall back to `SECRET_KEY`, so a login-key rotation also invalidates them.

## Logging

Public-form and public-design tokens, legacy student-verification tokens, and signed student credentials are revocable capabilities carried in URL paths. Run Uvicorn with `--no-access-log` so application access logs do not persist those tokens; application and server error logging remains enabled. Review Render or any upstream proxy logging separately and configure path redaction or suitably restricted retention before launch. Do not log bearer tokens, passwords, raw uploads, student record bodies, or capability URLs.

## Authentication rate limiting

The application protects login, registration, public forms, public-design previews, and student verification with in-memory, process-local limiters. Review the enabled flag, window, endpoint limits, and `AUTH_RATE_LIMIT_TRUSTED_PROXY_HOPS` before each production deployment.

## Student-verification incident response

If one card or URL is exposed unexpectedly, disable that student's link first,
then regenerate it and reprint the card after the incident is reviewed. If the
scope is uncertain, disable school-wide public verification to revoke all links
immediately without deleting their tokens. Do not rotate either signing key for
a single-link incident; regenerate the affected credential instead. Record the
affected school/student identifiers and actions in the
approved incident system without copying the public capability URL.

Because limiter state is not shared, scaling to multiple workers or instances multiplies the effective allowance. Before horizontal scaling, use an appropriate shared or edge control, then decide whether the application limiter should remain enabled to avoid an unintended double limit.

## Database migrations

CampusID uses a SQLAlchemy metadata naming convention aligned with PostgreSQL's
existing implicit constraint names. All CHECK constraints must also have an
explicit stable name. Alembic 1.19.2's named CHECK detector is enabled in
`migrations/env.py`; it compares names only and cannot detect a changed CHECK
expression under an unchanged name. Every generated migration therefore still
requires manual inspection.

1. Review every migration and its downgrade implications before deployment.
2. Run `python -m alembic heads` and confirm the repository has the intended single head.
3. Compare the production revision with the intended revision before applying anything.
4. Apply production migrations as an explicit deployment operation with an identified operator and recovery plan.
5. Never casually edit or rewrite an Alembic migration that may already have been applied. Add a corrective revision instead.

Irreversible data changes require extra review. A code rollback does not automatically reverse a database migration.

## PostgreSQL security patch verification

The PostgreSQL security baseline released on 2026-08-13 is 18.6, 17.11, 16.15,
15.19, or 14.24, depending on the project's major version. Before deployment
and after Supabase maintenance, run this read-only check from an environment
configured with the production database settings:

```powershell
python -m app.core.postgres_patch_level
```

An exit code of `0` means the server meets the reviewed baseline; exit code `2`
means the version is below it or outside the reviewed supported-major set.
Record only the returned PostgreSQL version and result, never credentials.

After an affected server update, inspect GIN-index table statistics and run
`ANALYZE` where `reltuples` is clearly invalid. If the project uses
`btree_gist` indexes over float/bit data or extremely deep `ltree` values,
follow PostgreSQL's release-specific reindex guidance. Supabase controls the
database binaries, so an outdated result must be escalated through the
Supabase project maintenance/upgrade controls rather than worked around in
application code.

## Supabase controls and recovery

Supabase PostgreSQL is the persistent application database. Supabase Storage holds student photos. Database row-level security remains deny-by-default for direct client table access; the FastAPI authorization layer remains the supported application access path.

Backup availability, restore methods, and retention depend on the actual Supabase project, plan, and configuration. Verify them directly before launch; this repository does not establish a backup guarantee.

Launch ownership must explicitly cover:

- the tested database backup and recovery procedure;
- the effect of database recovery on Storage object references and the separate recovery implications for student photos;
- the person or role authorized and responsible for initiating and validating recovery.

## Secret rotation

Use a controlled maintenance plan and never print old or new values.

- `DB_PASSWORD`: rotate in PostgreSQL/Supabase and Render in a coordinated order, then verify readiness and representative database operations.
- `SECRET_KEY`: replace in Render, redeploy/restart, expect all existing JWT sessions to become invalid, then verify login and authenticated requests.
- `SUPABASE_SECRET_KEY`: rotate through the provider, update Render, then verify photo upload, display, and deletion behavior.
- Review the Settings class before each rotation exercise for any newly introduced secrets.

Record who performed the change, when it occurred, and the verification result in the organization's approved operations record without recording the secret itself.

## Platform Administrator bootstrap and recovery

The current application has no safe automated Platform Administrator bootstrap or recovery workflow and intentionally exposes no bootstrap endpoint. The existing migration preserves authority for users who already had the legacy `is_platform_admin` flag; it does not create an initial administrator.

Before launch, define and test a restricted, auditable provider-side procedure for creating the first Platform Administrator and recovering administrator access. Specify authorization, identity verification, execution, review, and rollback responsibilities. Until that runbook is approved and tested, Platform Administrator bootstrap/recovery is a release operations blocker.
