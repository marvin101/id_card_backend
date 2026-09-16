# CampusID P1 gap-closure report — 2026-09-16

1. **Backend branch/HEAD:** `main` at `626ff01d277f954f5407559bc0a917140ef49139`.
2. **Flutter branch/HEAD:** `main` at `648c7e8fe7843c6d612e11c9479c537d26b9a60e`.
3. **Session-timeout root cause:** Flutter had only a 30-minute bearer access token and the backend had no renewal/session record, so access expiry forced sign-in during long work.
4. **Old session behavior:** login returned one access token; expiry cleared client auth and interrupted the current workflow.
5. **New behavior:** login creates a durable server session and returns purpose-separated access and rotating refresh credentials. The absolute session window is fixed at login; rotation does not extend it. Refresh replay revokes the session.
6. **Access-token lifetime:** configurable `ACCESS_TOKEN_EXPIRE_MINUTES`, default 30 minutes.
7. **Refresh/session lifetime:** configurable `REFRESH_TOKEN_EXPIRE_MINUTES`, default 720 minutes (12 hours), minimum 60.
8. **Endpoints:** `POST /auth/refresh` accepts only a refresh token and rotates both credentials; `POST /auth/logout` revokes the live session. Both use the refresh rate-limit bucket. Access tokens cannot refresh; refresh tokens cannot authenticate normal routes.
9. **Flutter automatic refresh:** renews one minute before access expiry and can also renew after one authenticated 401, without replacing the route/provider/form tree.
10. **Concurrency:** one shared in-flight refresh per `ApiService`; concurrent calls await it and each original request retries at most once.
11. **Logout:** local credentials are cleared immediately, server revocation is attempted with a five-second bound, and a guarded final preference cleanup prevents a late renewal write.
12. **Inactive-user behavior:** login is denied; refresh is denied; session-backed access checks current user state. Missing users and expired/revoked sessions are denied. Deactivation/password reset revoke every durable session.
13. **School lifecycle:** complete for normal administration: create, profile edit, activate/deactivate, inactive visibility, scoped selection, logo upload/removal. Platform Admin controls creation/activation. No hard-delete endpoint exists.
14. **User lifecycle:** complete for normal administration: global search/create/edit/activate/deactivate/password reset/platform promotion/demotion plus existing add/change/remove school roles and multi-school membership. Self-deactivation/self-role changes and last-active-platform-admin removal are blocked.
15. **Remaining Swagger/manual-DB dependencies:** none for normal ongoing school/account administration. Initial platform-admin bootstrap/recovery remains an exceptional provider-side runbook gap. Applying the new migration remains an operator release action.
16. **Student smoke:** local disposable route/test journeys pass create/edit, Pending → Needs Correction → Verified, print/reprint metadata/audit, custom fields, Grid behavior, school isolation and role denials. PDF generation has existing Flutter coverage. No live staging photo journey was run.
17. **Teacher smoke:** local disposable tests cover CRUD/edit, verification lifecycle, card/PDF data, print/reprint and audit for Teacher. No real staging media operation was run.
18. **Staff smoke:** same local lifecycle coverage as Teacher. No real staging media operation was run.
19. **Authorization matrix:** Platform Admin has global school/account authority; School Admin has assigned-school administration and identity lifecycle; Card Operator has assigned-school data/import/Grid/card/PDF/Print Basket and mark-printed authority but cannot activate/deactivate or verify/correct identities or read administrator history; Teacher/Staff retain assigned-school read-only supporting scope; anonymous access is token-scoped. Inactive-school public submissions and Card Operator activation denial are covered.
20. **Production read-only smoke:** 18/18 passed: backend health/readiness/OpenAPI/CORS/auth denial/public invalid-token behavior and Vercel root/dashboard/design/cards/teachers/staff/public route fallbacks. The first cold health request took 43.692 seconds; readiness then took 0.456 seconds. This proves route/HTTP behavior, not authenticated UI workflows.
21. **Storage/media smoke:** the production bucket was inventoried read-only and all nine objects were downloaded into an encrypted local recovery snapshot. The explicitly approved production cleanup removed all 9 reviewed objects and verified they are unavailable. This was cleanup, not an upload/replace/delete lifecycle smoke; that disposable staging journey remains open.
22. **Remaining P1:** authorized migration/deployment ordering; approved privacy/terms; real disposable multi-role UI and media smoke; actual Render `--no-access-log` verification/change; first-platform-admin bootstrap/recovery runbook.
23. **Final severity list:** P0: none confirmed. P1: the five gates in item 22. P2: personnel archive/restore history UX, audit-history paging, legacy unbounded compatibility endpoints, cross-tab refresh coordination, import/PDF peak-memory profiling, provider backup/restore drill. P3: proven dead-code/platform-directory cleanup. Deferred product modules remain out of scope.
24. **Exact files changed:**

   Backend:
   - `.env.example`
   - `CHANGELOG.md`
   - `OPERATIONS.md`
   - `README.md`
   - `RELEASE_CHECKLIST.md`
   - `app/api/auth.py`
   - `app/api/schools.py`
   - `app/api/student_imports.py`
   - `app/api/users.py`
   - `app/core/auth_sessions.py`
   - `app/core/config.py`
   - `app/core/custom_fields.py`
   - `app/core/rate_limit.py`
   - `app/core/security.py`
   - `app/main.py`
   - `app/models/__init__.py`
   - `app/models/auth_session.py`
   - `app/schemas/auth.py`
   - `app/schemas/school.py`
   - `docs/P1_GAP_CLOSURE_2026-09-16.md`
   - `docs/PRODUCTION_CHECKLIST.md`
   - `docs/READINESS_AUDIT_2026-09-16.md`
   - `docs/p1-closure-readonly-smoke-results.json`
   - `docs/production-cleanup-result-20260916.json`
   - `migrations/versions/e8f5b21c0d93_add_auth_sessions.py`
   - `tests/test_endpoint_integration.py`
   - `tests/test_public_designs.py`
   - `tests/test_student_custom_fields.py`
   - `tests/test_workday_sessions.py`

   Flutter:
   - `lib/app_routes.dart`
   - `lib/config/launch_config.dart`
   - `lib/main.dart`
   - `lib/providers/auth_provider.dart`
   - `lib/providers/school_profile_provider.dart`
   - `lib/screens/dashboard_screen.dart`
   - `lib/screens/platform_administration_screen.dart`
   - `lib/screens/public_information_screens.dart`
   - `lib/screens/school_profile_screen.dart`
   - `lib/screens/student_screen.dart`
   - `lib/services/api_service.dart`
   - `lib/services/session_http_client.dart`
   - `test/authenticated_navigation_test.dart`
   - `test/platform_administration_test.dart`
   - `test/session_refresh_test.dart`
   - `test/student_grid_test.dart`
25. **Tests added/updated:** new backend workday session/admin integration suite; endpoint fake session/delete support; migration-head and custom-field batch regression updates; new Flutter refresh/draft/concurrency and Platform Administration/paged-student suites; navigation/grid adapters updated.
26. **Backend validation:** `415 passed, 2 warnings, 2 subtests passed`; `python -m compileall -f app` passed.
27. **Flutter analyze:** `No issues found`.
28. **Flutter tests:** `301` passed; affected post-format suites rerun with `7` passed.
29. **Alembic:** one local head, `e8f5b21c0d93`; production was observed at `d7e4a10b9c82` and was not migrated.
30. **Diff checks:** `git diff --check` passed in both repositories; line-ending conversion notices are informational.
31. **Exact git status:** both repositories remain on `main` at the heads in items 1–2. Backend has the 22 tracked modifications and 7 untracked files listed in item 24; Flutter has 12 tracked modifications and 4 untracked files listed there. No staged changes were created.
32. **Release recommendation:** suitable for review as a **0.10.x patch candidate**. It still requires the P1 gates in item 22 before **1.0.0-rc1**.
33. **Versions:** unchanged at backend `0.10.0` and Flutter `0.10.0+10`.
34. **Git/deployment confirmation:** no commit, push, merge, rebase, reset, cherry-pick, branch, tag, release, deployment, production migration or history rewrite occurred. All implementation changes remain uncommitted.

## Supabase cleanup status

The approved production cleanup completed successfully. It preserved only `testadmin` (`0cc576f8-0d30-4b82-b624-2f803551f93f`) and deleted 12 other accounts, 55 students, 4 classes, 3 schools, nine Storage objects, and dependent rows in academic sessions, card templates, custom-field definitions/values, personnel/audit history, school-access requests, sections, student audits, user-school access, bulk-photo imports and Public Forms. A verified Windows-DPAPI-encrypted local recovery snapshot exists at `C:\Users\smith\AppData\Local\Temp\campusid-cleanup-recovery-20260916.json.zlib.dpapi`; it contains 17 table exports and all nine object bytes. The exact plan is `C:\Users\smith\AppData\Local\Temp\campusid-full-cleanup-plan-20260916.json`; postconditions are recorded in `docs/production-cleanup-result-20260916.json`. Post-cleanup `/health` and `/health/check` both returned `ok`.