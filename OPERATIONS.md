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
| `ALGORITHM` | JWT algorithm; defaults to `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access-token lifetime; defaults to `30` |
| `SUPABASE_URL` | Supabase project URL used by Storage |
| `SUPABASE_SECRET_KEY` | Private server-side Supabase key used by Storage |
| `CORS_ORIGINS` | Comma-separated allowed frontend origins |
| `AUTH_RATE_LIMIT_ENABLED` | Enables process-local public-auth throttling |
| `AUTH_RATE_LIMIT_WINDOW_SECONDS` | Rate-limit window |
| `LOGIN_RATE_LIMIT_REQUESTS` | Login requests allowed per client and window |
| `REGISTRATION_RATE_LIMIT_REQUESTS` | Registration requests allowed per client and window |
| `AUTH_RATE_LIMIT_TRUSTED_PROXY_HOPS` | Controlled reverse-proxy hops used to resolve client addresses |

Before deployment, confirm every required value is present, `SECRET_KEY` is a strong production-only value, the Vercel production origin is allowed by `CORS_ORIGINS`, and the trusted proxy-hop count matches Render's actual topology. Do not expose `SUPABASE_SECRET_KEY` or any database credential to Flutter Web.

## Health and readiness

- `GET /health` is a process/liveness check. It does not query PostgreSQL.
- `GET /health/check` is a database-readiness check. It returns HTTP `503` with a generic response when the database check fails.

Use liveness to determine whether the FastAPI process responds. Use readiness before sending production traffic and after deployments or database maintenance.

## Authentication and sessions

CampusID uses bearer access tokens. Their lifetime is controlled by `ACCESS_TOKEN_EXPIRE_MINUTES`. There is no refresh-token infrastructure; after expiration, clients must clear the session and the user must authenticate again.

Changing `SECRET_KEY` invalidates all JWTs signed with the previous key. Plan that rotation as a forced sign-in event and verify authentication immediately afterward.

## Authentication rate limiting

The application protects login and registration with an in-memory, process-local limiter. Review the enabled flag, window, login limit, registration limit, and `AUTH_RATE_LIMIT_TRUSTED_PROXY_HOPS` before each production deployment.

Because limiter state is not shared, scaling to multiple workers or instances multiplies the effective allowance. Before horizontal scaling, use an appropriate shared or edge control, then decide whether the application limiter should remain enabled to avoid an unintended double limit.

## Database migrations

1. Review every migration and its downgrade implications before deployment.
2. Run `python -m alembic heads` and confirm the repository has the intended single head.
3. Compare the production revision with the intended revision before applying anything.
4. Apply production migrations as an explicit deployment operation with an identified operator and recovery plan.
5. Never casually edit or rewrite an Alembic migration that may already have been applied. Add a corrective revision instead.

Irreversible data changes require extra review. A code rollback does not automatically reverse a database migration.

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
