# CampusID Operations

This runbook describes the current production design and the checks operators must complete before launch or deployment. It contains variable names only; never copy real credentials into this file, source control, client builds, tickets, or logs.

## Production architecture

```text
Flutter Web on Vercel
        |
        | HTTPS / JSON with bearer access token
        v
FastAPI Web Service on Render
        |                         ^
        |                         | transactional email jobs
        +--> Supabase PostgreSQL -+
        |
        +--> Supabase Storage `student-photos` bucket (student photos)

Independent Render Background Worker
        |
        +--> claims email jobs from Supabase PostgreSQL
        +--> sends email through the configured SMTP provider
```

The Render Web Service handles HTTP requests and writes password-reset email jobs to the PostgreSQL-backed transactional outbox. The independent Background Worker polls that outbox, claims jobs, and sends email. The worker is not an HTTP service and does not need a public URL or health-check path.

Render's filesystem is ephemeral and must not be treated as persistent application storage. The local `uploads/` directory and `/media` mount support the application layout but are not the production photo store.

## Render services and commands

Create and operate two separate Render services from the backend repository:

| Service | Render type | Start command |
| --- | --- | --- |
| CampusID API | Web Service | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --no-access-log` |
| CampusID Email Worker | Background Worker | `python -m app.workers.email_outbox` |

Use the same deployed commit/revision for both services. The API service receives web traffic; the worker runs continuously and polls the outbox. Do not run the worker as a second Uvicorn process or expose it as a public web service.

Configure the worker's shutdown/graceful-termination behavior in Render so it can stop cleanly during deploys. A worker that is stopped while processing a job may leave it in `processing`; the lease mechanism allows a later worker to reclaim an expired job. Email delivery is at-least-once: if the SMTP server accepts a message but the worker stops before recording success, a retry can send a duplicate. Do not describe this system as exactly-once delivery.

## Render environment

The names below come from `app/core/config.py` and `.env.example`. Required values must be stored in Render's environment configuration, not in the Flutter application.

Configure the applicable server-side values on **both** the API Web Service and the Background Worker. The two services must use the same database, outbox encryption key, SMTP configuration, and application URL. Other application settings should also be kept aligned unless a setting is intentionally service-specific.

| Variable | Operational purpose |
| --- | --- |
| `DB_HOST` | PostgreSQL host |
| `DB_PORT` | PostgreSQL port; defaults to `5432` |
| `DB_NAME` | PostgreSQL database name |
| `DB_USER` | PostgreSQL user |
| `DB_PASSWORD` | PostgreSQL password |
| `DB_SSLMODE` | PostgreSQL TLS mode; production should use the provider-required secure mode |
| `SECRET_KEY` | JWT signing secret |
| `CREDENTIAL_SIGNING_KEY` | Stable production-only signing secret for public student credentials |
| `ALGORITHM` | JWT algorithm; defaults to `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access-token lifetime; defaults to `30` |
| `SUPABASE_URL` | Supabase project URL used by Storage |
| `SUPABASE_SECRET_KEY` | Private server-side Supabase key used by Storage |
| `CORS_ORIGINS` | Comma-separated allowed frontend origins; used by the API |
| `AUTH_RATE_LIMIT_ENABLED` | Enables process-local authentication and public-route throttling |
| `AUTH_RATE_LIMIT_WINDOW_SECONDS` | Rate-limit window |
| `LOGIN_RATE_LIMIT_REQUESTS` | Login requests allowed per client and window |
| `REGISTRATION_RATE_LIMIT_REQUESTS` | Registration requests allowed per client and window |
| `PUBLIC_FORM_GET_RATE_LIMIT_REQUESTS` | Public-form reads allowed per client and window |
| `PUBLIC_FORM_SUBMIT_RATE_LIMIT_REQUESTS` | Public-form submissions allowed per client and window |
| `PUBLIC_DESIGN_GET_RATE_LIMIT_REQUESTS` | Public-design reads allowed per client and window |
| `PUBLIC_VERIFICATION_GET_RATE_LIMIT_REQUESTS` | Public student-verification reads allowed per client and window |
| `PUBLIC_FORM_MAX_REQUEST_BYTES` | Maximum anonymous public-form request size |
| `PUBLIC_APP_URL` | Canonical Flutter origin used in student-verification QR links and password-reset links |
| `AUTH_RATE_LIMIT_TRUSTED_PROXY_HOPS` | Controlled reverse-proxy hops used to resolve client addresses |
| `REFRESH_TOKEN_EXPIRE_MINUTES` | Absolute refresh/session lifetime in minutes; default 720 |
| `REFRESH_RATE_LIMIT_REQUESTS` | Refresh/logout requests allowed per client and limiter window |
| `SMTP_HOST` | SMTP server hostname; defaults to Brevo |
| `SMTP_PORT` | SMTP server port; defaults to `587` |
| `SMTP_USERNAME` | SMTP authentication username |
| `SMTP_PASSWORD` | SMTP authentication password |
| `SMTP_FROM_EMAIL` | Sender email address |
| `SMTP_FROM_NAME` | Sender display name |
| `SMTP_USE_STARTTLS` | Enables STARTTLS; keep `true` for the configured port-587 production connection |
| `SMTP_TIMEOUT_SECONDS` | SMTP connection timeout; defaults to `15` |
| `EMAIL_OUTBOX_ENCRYPTION_KEY` | Dedicated Fernet key used to encrypt queued email payloads |
| `EMAIL_OUTBOX_POLL_SECONDS` | Worker polling interval; defaults to `3` |
| `EMAIL_OUTBOX_LEASE_SECONDS` | Processing lease duration; defaults to `120` |
| `EMAIL_OUTBOX_MAX_ATTEMPTS` | Maximum delivery attempts; defaults to `8` |
| `EMAIL_VERIFICATION_RATE_LIMIT_WINDOW_SECONDS` | Email-verification limiter window |
| `EMAIL_VERIFICATION_ISSUE_RATE_LIMIT_REQUESTS` | Email-verification issuance limit |
| `EMAIL_VERIFICATION_VERIFY_RATE_LIMIT_REQUESTS` | Email-verification confirmation limit |
| `PASSWORD_RESET_RATE_LIMIT_WINDOW_SECONDS` | Password-reset limiter window |
| `PASSWORD_RESET_REQUEST_RATE_LIMIT_REQUESTS` | Password-reset request limit |
| `PASSWORD_RESET_CONFIRM_RATE_LIMIT_REQUESTS` | Password-reset confirmation limit |
| `MFA_ENCRYPTION_KEY` | Dedicated MFA secret-encryption key if required by the deployed MFA configuration |

Before deployment, confirm every required value is present, `SECRET_KEY` and `CREDENTIAL_SIGNING_KEY` are separate strong production-only values, the Vercel production origin is allowed by `CORS_ORIGINS`, and the trusted proxy-hop count matches Render's actual topology. Do not expose signing keys, `SUPABASE_SECRET_KEY`, SMTP credentials, encryption keys, or any database credential to Flutter Web.

### Outbox encryption key

Generate a new Fernet key locally using the project's Python environment:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Do not paste the key into chat, commit it, or place it in documentation. Set the generated value as `EMAIL_OUTBOX_ENCRYPTION_KEY` in the Render API service and the Render Background Worker. The value must match exactly in both services.

This key is not interchangeable with `SECRET_KEY`, `CREDENTIAL_SIGNING_KEY`, `MFA_ENCRYPTION_KEY`, or SMTP credentials. Back it up in an approved secrets manager. Losing or changing it can make existing encrypted outbox payloads unreadable; key rotation requires a planned migration/re-encryption procedure. Do not rotate it casually while encrypted jobs exist.

The application settings default this key to an empty string, so the deployment must explicitly provide it. Confirm the deployed worker and API are configured before enabling password-reset email traffic. A missing or invalid key is a configuration failure, not an SMTP outage; do not allow queued jobs to be repeatedly consumed as ordinary delivery failures.

### SMTP configuration

For the current Brevo SMTP configuration, set the correct SMTP username and password from the provider and keep `SMTP_USE_STARTTLS=true` with port `587`. Do not disable STARTTLS to work around a connection problem. Verify the sender address/domain with the SMTP provider before sending production messages.

The API service needs SMTP settings only if application code sends email directly; the current transactional outbox worker is the component that performs delivery. Keeping SMTP settings aligned across both services simplifies configuration and future maintenance. Never log SMTP credentials or raw reset tokens.

## Health and readiness

- `GET /health` is a process/liveness check. It does not query PostgreSQL.
- `GET /health/check` is a database-readiness check. It returns HTTP `503` with a generic response when the database check fails.
- The Background Worker has no HTTP health endpoint. Monitor its Render process state and logs, and verify that pending outbox jobs are being processed.

Use liveness to determine whether the FastAPI process responds. Use readiness before sending production traffic and after deployments or database maintenance. For the worker, verify it starts without configuration errors and remains running.

## Authentication and sessions

CampusID uses short-lived bearer access tokens and durable, rotating workday sessions. `ACCESS_TOKEN_EXPIRE_MINUTES` controls the access token (30 minutes by default); `REFRESH_TOKEN_EXPIRE_MINUTES` controls the absolute session window (720 minutes by default). `POST /auth/refresh` accepts only a purpose-marked refresh token, rotates it, and returns current user authority in a new access token. A reused refresh token revokes that session. `POST /auth/logout` revokes the session. Inactive/deleted users, expired/revoked sessions, and refresh tokens presented to normal authenticated endpoints fail closed. Password resets and account deactivation revoke every durable session for that account.

The Flutter client refreshes shortly before access expiry and retries one request after an expiry response. Concurrent calls share one refresh operation. A transient network failure keeps the local session and draft state for retry; an invalid, expired, or revoked refresh session clears both tokens. Treat `auth_sessions` as server-owned data: direct client access is denied by RLS. Confirm the production database is at the required migration revision before deploying code that depends on new schema objects.

Changing `SECRET_KEY` invalidates all login JWTs signed with the previous key. Plan that rotation as a forced sign-in event and verify authentication immediately afterward. Changing `CREDENTIAL_SIGNING_KEY` invalidates every signed credential already printed on a card; rotate it only through an explicit card-reissuance incident procedure. When the dedicated key is omitted, credentials fall back to `SECRET_KEY`, so a login-key rotation also invalidates them.

## Logging

Public-form and public-design tokens, legacy student-verification tokens, and signed student credentials are revocable capabilities carried in URL paths. Run Uvicorn with `--no-access-log` so application access logs do not persist those tokens; application and server error logging remains enabled. Review Render or any upstream proxy logging separately and configure path redaction or suitably restricted retention before launch. Do not log bearer tokens, passwords, raw uploads, student record bodies, SMTP credentials, raw reset tokens, or capability URLs.

Worker logs should be reviewed for job identifiers, exception types, retry activity, and worker start/stop events. Do not add email addresses, reset tokens, decrypted payloads, or SMTP credentials to log messages.

## Authentication rate limiting

The application protects login, registration, public forms, public-design previews, and student verification with in-memory, process-local limiters. Review the enabled flag, window, endpoint limits, and `AUTH_RATE_LIMIT_TRUSTED_PROXY_HOPS` before each production deployment.

Because limiter state is not shared, scaling to multiple API instances multiplies the effective allowance. Password-reset and email-verification throttles are also process-local. Before horizontal scaling, use an appropriate shared or edge control, then decide whether the application limiter should remain enabled to avoid an unintended double limit. The Background Worker does not replace API-side rate limiting.

## Transactional email outbox operations

The API commits the password-reset token and its encrypted email-outbox job in the same database transaction. The worker claims due jobs using PostgreSQL row locking, sends the message, and records success or schedules a retry. Delivery attempts use exponential backoff, bounded by the configured maximum attempts. Jobs that exhaust attempts are marked failed; the associated password-reset token is invalidated when the worker can safely identify it.

Operational checks:

1. Confirm the `email_outbox` table exists at the expected production migration revision before deploying either service.
2. Confirm both Render services use the same database and exactly the same `EMAIL_OUTBOX_ENCRYPTION_KEY`.
3. Confirm the worker is running and its logs show it has started.
4. Trigger a password-reset request in a controlled staging environment and verify that a job is queued and an email is received.
5. Confirm successful jobs become `sent` and that their encrypted payload is cleared.
6. Test a controlled SMTP failure in staging and verify that the job is retried, then either succeeds or reaches `failed` after the configured maximum attempts.
7. Alert on a worker process that repeatedly restarts, a growing pending-job backlog, or jobs remaining in `processing` beyond the configured lease.

Do not manually edit outbox rows or reset attempt counters in production as a routine recovery method. Investigate the worker log, SMTP provider status, database connectivity, and configuration first. Preserve auditability when performing any manual repair.

The worker supports a one-shot mode for controlled diagnostics:

```powershell
python -m app.workers.email_outbox --once
```

This command can claim and process one real queued job. Run it only in a controlled environment when that behavior is intended; it is not a harmless health check.

## Student-verification incident response

If one card or URL is exposed unexpectedly, disable that student's link first, then regenerate it and reprint the card after the incident is reviewed. If the scope is uncertain, disable school-wide public verification to revoke all links immediately without deleting their tokens. Do not rotate either signing key for a single-link incident; regenerate the affected credential instead. Record the affected school/student identifiers and actions in the approved incident system without copying the public capability URL.

## Database migrations

CampusID uses a SQLAlchemy metadata naming convention aligned with PostgreSQL's existing implicit constraint names. All CHECK constraints must also have an explicit stable name. Alembic 1.19.2's named CHECK detector is enabled in `migrations/env.py`; it compares names only and cannot detect a changed CHECK expression under an unchanged name. Every generated migration therefore still requires manual inspection.

1. Review every migration and its downgrade implications before deployment.
2. Run `python -m alembic heads` and confirm the repository has the intended single head.
3. Compare the production revision with the intended revision before applying anything.
4. Apply production migrations as an explicit deployment operation with an identified operator and recovery plan.
5. Never casually edit or rewrite an Alembic migration that may already have been applied. Add a corrective revision instead.

Irreversible data changes require extra review. A code rollback does not automatically reverse a database migration.

## PostgreSQL security patch verification

The PostgreSQL security baseline released on 2026-08-13 is 18.6, 17.11, 16.15, 15.19, or 14.24, depending on the project's major version. Before deployment and after Supabase maintenance, run this read-only check from an environment configured with the production database settings:

```powershell
python -m app.core.postgres_patch_level
```

An exit code of `0` means the server meets the reviewed baseline; exit code `2` means the version is below it or outside the reviewed supported-major set. Record only the returned PostgreSQL version and result, never credentials.

After an affected server update, inspect GIN-index table statistics and run `ANALYZE` where `reltuples` is clearly invalid. If the project uses `btree_gist` indexes over float/bit data or extremely deep `ltree` values, follow PostgreSQL's release-specific reindex guidance. Supabase controls the database binaries, so an outdated result must be escalated through the Supabase project maintenance/upgrade controls rather than worked around in application code.

## Supabase controls and recovery

Supabase PostgreSQL is the persistent application database. Supabase Storage holds student photos. Database row-level security remains deny-by-default for direct client table access; the FastAPI authorization layer remains the supported application access path.

Backup availability, restore methods, and retention depend on the actual Supabase project, plan, and configuration. Verify them directly before launch; this repository does not establish a backup guarantee.

Launch ownership must explicitly cover:

- the tested database backup and recovery procedure;
- the effect of database recovery on Storage object references and the separate recovery implications for student photos;
- the person or role authorized and responsible for initiating and validating recovery.

## Secret rotation

Use a controlled maintenance plan and never print old or new values.

- `DB_PASSWORD`: rotate in PostgreSQL/Supabase and both Render services in a coordinated order, then verify readiness and representative database operations.
- `SECRET_KEY`: replace in the API service, redeploy/restart, expect all existing JWT sessions to become invalid, then verify login and authenticated requests.
- `SUPABASE_SECRET_KEY`: rotate through the provider, update both Render services, then verify photo upload, display, and deletion behavior.
- SMTP credentials: rotate through the SMTP provider, update the Render service configuration, then verify a controlled staging delivery.
- `EMAIL_OUTBOX_ENCRYPTION_KEY`: do not rotate until a key-rotation/re-encryption plan exists for any encrypted pending jobs. A casual replacement can make queued jobs unreadable.
- `MFA_ENCRYPTION_KEY`: rotate only through a tested procedure that accounts for already-encrypted MFA secrets.
- Review the Settings class before each rotation exercise for any newly introduced secrets.

Record who performed the change, when it occurred, and the verification result in the organization's approved operations record without recording the secret itself.

## Platform Administrator bootstrap and recovery

The current application has no safe automated Platform Administrator bootstrap or recovery workflow and intentionally exposes no bootstrap endpoint. The existing migration preserves authority for users who already had the legacy `is_platform_admin` flag; it does not create an initial administrator.

Before launch, define and test a restricted, auditable provider-side procedure for creating the first Platform Administrator and recovering administrator access. Specify authorization, identity verification, execution, review, and rollback responsibilities. Until that runbook is approved and tested, Platform Administrator bootstrap/recovery is a release operations blocker.

## Deployment verification

Before deploying:

1. Confirm the exact backend commit and intended Alembic revision.
2. Compare the production database revision with the target revision; do not assume that a migration is unnecessary.
3. Confirm the Render API and worker services have the required environment values, including matching outbox encryption keys and valid SMTP credentials.
4. Confirm the SMTP sender/domain is authorized and STARTTLS is enabled.
5. Confirm database backup and recovery arrangements and the Platform Administrator bootstrap/recovery procedure.
6. Deploy the API and worker from the same commit.
7. Verify API liveness at `/health` and database readiness at `/health/check`.
8. Verify the worker remains running and processes a controlled staging email job.
9. Run the authorized authentication, password-reset, public verification, and photo/logo upload/delete smoke tests.

Do not treat a successful local test suite as proof of production SMTP delivery, Render worker health, production database migration state, or provider backup readiness. No production migration or deployment should be performed until its target revision and operational approval are confirmed.
