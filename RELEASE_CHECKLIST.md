# CampusID Release Checklist

Current production path:

```text
Flutter Web
    -> FastAPI on Render
    -> Supabase PostgreSQL + Supabase Storage
```

Record the release owner, candidate Git commit IDs, migration revision, verification date, and results in the team's release record. Do not record secrets.

## Backend candidate

- [ ] GitHub Backend CI passes for the release candidate.
- [ ] `python -m pytest` passes.
- [ ] `python -m compileall app` passes.
- [ ] `python -m alembic heads` shows the reviewed intended single head; the deployed/current revision and target head have been compared.
- [ ] Every pending migration has been manually reviewed before intentional production application, including data-loss and downgrade implications.
- [ ] All Settings variables required by Render are configured: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `CORS_ORIGINS`, `AUTH_RATE_LIMIT_ENABLED`, `AUTH_RATE_LIMIT_WINDOW_SECONDS`, `LOGIN_RATE_LIMIT_REQUESTS`, `REGISTRATION_RATE_LIMIT_REQUESTS`, and `AUTH_RATE_LIMIT_TRUSTED_PROXY_HOPS`.
- [ ] `SECRET_KEY` is a strong, production-only value and is not present in source, logs, tickets, or the Flutter build.
- [ ] `ACCESS_TOKEN_EXPIRE_MINUTES` has been reviewed for the launch policy.
- [ ] Login/registration rate-limit enablement, window, and request limits have been reviewed.
- [ ] `AUTH_RATE_LIMIT_TRUSTED_PROXY_HOPS` matches the actual Render proxy chain; do not trust additional hops.
- [ ] Production CORS origins contain only approved frontend origins.
- [ ] `GET /health` reports healthy process liveness.
- [ ] `GET /health/check` confirms database readiness; a simulated/known database failure returns `503` without leaking details.
- [ ] Supabase RLS remains deny-by-default for direct client table access, and the Supabase security-advisor state has been reviewed.
- [ ] The Platform Administrator bootstrap/recovery runbook described in `OPERATIONS.md` is approved and tested.
- [ ] Supabase database backup/recovery availability and retention have been verified for the actual project/plan/configuration.
- [ ] Database recovery, Storage recovery implications, and recovery ownership are documented and exercised as appropriate.

## Flutter candidate

- [ ] `flutter pub get` completes.
- [ ] `flutter analyze` passes.
- [ ] `flutter test` passes.
- [ ] Launch placeholders on Privacy, Terms, and Contact & Support have been replaced or explicitly approved for launch.
- [ ] Build the production web bundle locally:

  ```powershell
  flutter build web --release --dart-define=API_BASE_URL=https://id-card-backend-vcz5.onrender.com
  ```

- [ ] Inspect the release build and confirm no backend, database, Supabase, or JWT secrets are embedded.

## Frontend deployment

The current intended process is a local Flutter release build followed by deployment of the generated static bundle with the Vercel CLI. Do not substitute a Vercel Git build without a separately reviewed architecture change.

```text
local Flutter release build
    -> deploy build/web with Vercel CLI
```

- [ ] From `idcard_flutter`, complete the production build above.
- [ ] Change to `build/web` and deploy that exact output with the Vercel CLI.
- [ ] Record the deployed frontend version/commit and Vercel deployment identifier.

## Post-deploy smoke tests

- [ ] Landing page loads at the production frontend URL.
- [ ] Footer links open Privacy, Terms, and Contact & Support; back/home navigation works.
- [ ] Direct/deep Flutter Web routes work after browser refresh, including `/privacy`, `/terms`, `/support`, `/sign-in`, and `/register` as applicable to the hosting route mode.
- [ ] Registration school list loads.
- [ ] Registration submits the selected `school_uuid` successfully and creates the expected pending access request.
- [ ] Login works and an authorized user reaches the correct landing experience.
- [ ] Platform Admin flow works, including school administration and elevated role assignment as applicable.
- [ ] School Admin flow works within assigned schools and cannot cross school boundaries.
- [ ] Card Operator flow works where applicable and remains limited to its current permissions.
- [ ] School switching changes context correctly for a multi-school user.
- [ ] Pending, inactive, or revoked school access is denied and access revocation takes effect as intended.
- [ ] Student listing loads for the selected school.
- [ ] Student creation and editing work where the current role allows them.
- [ ] Student photo upload and display work through Supabase Storage.
- [ ] Card Designer loads and saves for authorized roles.
- [ ] Single-card generation works and output is reviewed.
- [ ] Bulk PDF generation works and representative output is reviewed.
- [ ] An expired session clears local auth/school state and returns the user to sign-in with the session-expired message.
- [ ] A `403` authorization response displays/handles denial without incorrectly signing the user out.
- [ ] Backend `GET /health/check` confirms database readiness after deployment.

## Rollback readiness

- [ ] Identify and record the last known-good backend and frontend Git commits before deployment.
- [ ] Retain or identify the corresponding known-good backend deployment and locally built/deployed frontend version.
- [ ] If application rollback is required, restore/deploy those known-good versions and repeat health/readiness and critical smoke tests.
- [ ] Treat database changes separately: do not assume code rollback reverses a migration, and do not run an irreversible downgrade casually.
- [ ] Any database rollback or restore follows the actual recovery capability verified for the Supabase project/plan/configuration and the approved recovery runbook.
- [ ] Confirm data integrity, Storage/photo behavior, authentication, and school authorization after recovery.
