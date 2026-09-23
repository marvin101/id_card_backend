# CampusID 1.0 readiness audit — 2026-09-16

## Decision and scope

Not ready to declare 1.0. No confirmed P0 was found in this scoped audit; unresolved P1 lifecycle and release-evidence gaps remain. Four clear backend defects were fixed locally. This is an evidence-based audit, not proof that every production workflow succeeds.

Backend: `main`, `b56e14d91f7adf978d5005696d0d51465b533b3d`.
Flutter: `main`, `648c7e8fe7843c6d612e11c9479c537d26b9a60e`.
Versions remain backend **0.10.0**, Flutter **0.10.0+10**; single Alembic head **d7e4a10b9c82**. No migrations were applied. No commit, push, merge, rebase, reset, cherry-pick, branch, tag, release, deployment, production migration or history rewrite was performed. Existing user-triggered Render deployment was observed only. All changes remain uncommitted. Deferred personnel credentials, collaboration, Photo Studio, white-label/lanyards and AI OCR remain deferred.

## Lifecycle results

| Area | Implemented and reviewed | Remaining limitation |
|---|---|---|
| School | Platform-admin creation API; school-admin profile/name/contact/address/principal/logo editing; active-school membership selection and scoped authorization | Creation requires API/Swagger; activation/deactivation/reactivation requires controlled DB work; no lifecycle API/UI; no explicit logo-removal path |
| User | Registration/pending membership; administrator role grants/changes/revocations; school assignment; scoped elevated-role restrictions; inactive-user login/JWT rejection | Account edit, activation/deactivation, password reset and platform-admin bootstrap lack complete API/UI; no all-user directory for assignment to another school |
| Student | CRUD, custom fields, photos, import, bulk photos, Grid, pending/correction/verified transitions, signed verification, printed tracking/history | Administrator activation restored safely through existing PUT; no complete archived directory. Full physical DB/media/UI smoke remains pending |
| Teacher | Type-scoped personnel CRUD/custom fields/photos/import/Grid/bulk photos; correction/verification/printed/history; Designer bindings/PDF/basket | No explicit inactive restore route/archived directory; public credentials intentionally deferred |
| Staff | Same tested personnel flow with separate staff discriminator and school/type boundaries | Same archive and staging-evidence limitations; public credentials intentionally deferred |

PDF generation uses Flutter `lib/services/pdf_service.dart`; it does not itself call lifecycle mutation APIs. Explicit mark-printed actions update counts/timestamps separately. Print Basket partitions school and identity type; existing widget tests cover context reset. Local HTTP regression tests exercised correction → verification → print → reprint for all three identity kinds, with real route permission helpers and disposable fake persistence. They do not establish PostgreSQL constraint or Supabase provider behavior.

## Authorization matrix

A = administrator management; D = card data editing/production; R = assigned-school read; T = token-scoped public operation; — = denied. Platform administrator acts across schools; every school-bound operation first requires an active school. School administrators/operators require membership. Teacher/Staff account roles are distinct from personnel record types.

| Area | Platform Admin | School Admin | Card Operator | Teacher | Staff | Anonymous |
|---|---|---|---|---|---|---|
| School profile/read | A | A own school | R | R | R | — |
| School creation | A API | — | — | — | — | — |
| User memberships/roles | A | A ordinary roles | — | — | — | Registration only |
| Student/personnel records, photos | D/A | D/A | D | — | — | — |
| Custom field definitions | A | A | Use only | — | — | — |
| Grid/import/bulk photos | D | D | D | — | — | — |
| Designer/template save/public sharing | A | A | — | — | — | — |
| Template GET | R | R | R | R API | R API | T public Design |
| PDF/Print Basket | D | D | D | — UI | — UI | — |
| Correction/verification/mark printed/history | A | A | — | — | — | — |
| Student deactivation/reactivation/inactive edit | A | A | — | — | — | — |
| Public Form administration | A | A | — | — | — | — |
| Public Form fetch/submit | T | T | T | T | T | T active form/school |
| Public Design fetch | T | T | T | T | T | T active token/school |
| Student public verification | T | T | T | T | T | T valid credential |

Sources: backend `app/core/school_access.py`, `app/api/{schools,users,students,personnel,card_templates,public_forms}.py`; Flutter `lib/providers/auth_provider.dart`, `lib/main.dart`, `lib/screens/card_designer_route_screen.dart`. Designer `canDesignCards` is administrator-gated and matches the save endpoint. Teacher/Staff template reads allowed by API do not imply Designer mutation authority. Existing tests cover IDOR, inactive accounts/schools, role transitions and cross-school/type isolation. The new student guards close an operator activation bypass and inactive-record editing path. No new account self-protection semantics were invented.

## Smoke evidence and limits

- Backend baseline: 369 tests passed. Final: **392 passed, 2 subtests passed**; one existing Starlette 422 deprecation warning and a filesystem pytest-cache warning. `compileall app` passed; `alembic heads` returned the single intended head; both repositories passed `git diff --check`.
- Flutter `pub get` passed; `flutter analyze` reported no issues; **294 Flutter tests passed**. PDF font/Unicode warnings are retained in test output. Flutter has no source or lockfile changes.
- Added **23** focused readiness tests in `tests/test_readiness_smoke.py`. Existing `tests/test_public_forms.py` fixture now includes its school for the central active-state guard. Coverage includes administrator/operator activation, null activation validation, inactive edits/submissions, all identity lifecycle print counts/audits, unauthorized transitions, XLSX coordinates/column/row/cell limits and ambiguous photo admission matching.
- Production read-only script: **18/18 checks passed**, recorded in `docs/readiness-smoke-results.json`. GET `/health` and `/health/check` succeeded; OpenAPI reports 0.10.0 and the real verification route; trusted Vercel CORS preflight succeeds, unapproved origin returns 400 without allow-origin; unauthenticated `/users/me` returns 401; invalid public tokens return safe 404; verification failure has `no-store`.
- Vercel root and eight deep routes returned HTML 200, demonstrating SPA refresh fallback. This alone does not establish rendering, role checks, back/forward behavior or absence of redirect loops.
- Browser: existing authenticated dashboard and school context rendered, including administrator navigation. Students navigation changed the URL; subsequent DOM/screenshot calls failed and debugger detached. Full list/edit/public-page/responsive/logout/multi-role UI smoke is **not completed**. No production record mutations or new login attempt were made.
- Render observed deployment `dep-dakv00i1r09s73f5ld1g`, source b56e14d, normal `uvicorn app.main:app --host 0.0.0.0 --port $PORT`: command 06:56:12, server started/application complete/bind 06:56:28, service live 06:56:36 (GMT+5:30). Approximately 16 seconds to bind and 24 seconds to live; reported deployment duration 33.1 seconds. Import completion was not separately logged. Existing logs subsequently showed login OPTIONS/POST, `/users/me` and `/schools` 200. Root 404 is expected, not a startup failure. Health GET in read-safe probe was about 0.486s; readiness 0.322s, without controlled load/cold-start conditions.
- Storage: lazy SDK/client initialization and replacement/removal/cleanup contracts covered by existing tests. **First real storage operation, provider permissions, object cleanup and backup restoration were not exercised**: no disposable live credentials/media authorization supplied. No production uploads, deletions, secret rotation or bucket changes.

## Prioritized findings

Each entry states area/files, evidence, impact/risk, action and status. Security severity and release priority are different scales.

### P0

No confirmed P0. This is not a claim that the product has no undiscovered blockers.

### P1 — before 1.0

1. **Student authorization — FIXED locally.** `app/api/students.py`, `tests/test_readiness_smoke.py`: operator-accessible PUT previously accepted `is_active`, bypassing administrator-only deletion; inactive records could also be edited. Impact: unauthorized within-school activation changes. Low security severity but important lifecycle authority. Explicit activation/inactive editing now requires administrator; null is 422. Regression tests assert rejection without mutation and administrator deactivate/restore audit. Review and deploy in a separately authorized phase.
2. **Inactive public submissions — FIXED locally.** `app/api/public_forms.py`, `tests/test_public_forms.py`, `tests/test_readiness_smoke.py`: POST formerly checked form activity/expiry but not school activity while GET did. Old known token could create a pending record/photo for an inactive school. Low security severity. Central lookup now rejects both before payload/storage/writes; regression tests cover GET/POST.
3. **Sparse XLSX resource exhaustion — FIXED locally.** `app/core/student_imports.py`, `tests/test_readiness_smoke.py`: small expanded worksheet with far-right cell coordinate could allocate a huge sparse list despite archive/row limits. Medium security severity; authenticated operator could consume shared worker memory. Parser now validates coordinates and bounds columns (256), rows (5000 data rows) and materialized cells (500,000) before allocation. Invalid inputs fail 422/413; normal import tests pass.
4. **School lifecycle completeness — NOT FIXED.** `app/api/schools.py`, `app/schemas/school.py`, Flutter `lib/services/api_service.dart`, `lib/screens/school_profile_screen.dart`: profile edits exist but activation API/schema and creation UI are absent. Impact: normal administration still needs Swagger/DB. Risk: availability/operational errors. Next phase must define reversible archive semantics, authorization and operator UI; avoid hard delete.
5. **User account lifecycle completeness — NOT FIXED.** `app/api/users.py`, `app/schemas/user.py`, Flutter `lib/screens/school_user_assignment_screen.dart`, `lib/services/api_service.dart`: memberships work, account edit/activation/reset/global lookup/bootstrap remain incomplete. Impact: account administration requires DB or external bootstrap; second-school assignment may require known UUID via API. Risk: administrator lockout or incorrect authority if rushed. Specify current-user/last-admin protections and reversible workflow before implementation.
6. **End-to-end release evidence — NOT FIXED.** `scripts/readiness_readonly_smoke.py`, `tests/test_readiness_smoke.py`, `RELEASE_CHECKLIST.md`, `docs/PRODUCTION_CHECKLIST.md`: HTTP fixtures and read-safe probes cannot prove actual PostgreSQL/Supabase/media or full multi-role UI workflows. Impact/risk: integration defects may survive release. Execute disposable staging checklist below, including real first media operation and restore drill.
7. **Launch information placeholders — NOT FIXED.** Flutter `lib/config/launch_config.dart`, `lib/screens/public_information_screens.dart`: example support address and replacement prompts remain. Impact: users lack final support/privacy/terms information. Risk: launch support and disclosure readiness. Product owner supplies approved organization/contact/retention/terms content; no invented business details.

### P2 — should fix before 1.0

1. **Ambiguous bulk student photos — FIXED locally.** `app/api/bulk_student_photos.py`, `tests/test_readiness_smoke.py`: case-sensitive admission uniqueness permits ABC/abc, but casefold dict previously silently chose a record. Impact: wrong within-school photo association. Reject ambiguous lookup with 409 before importing; preserve unique normalized matching. No schema migration.
2. **Large lists/history and validation costs — NOT FIXED.** `app/api/students.py`, `app/api/student_grid.py`, `app/api/personnel_grid.py`, `app/core/student_imports.py`, `app/api/personnel_imports.py`, Flutter `lib/screens/students_screen.dart`, `lib/services/api_service.dart`, `lib/services/pdf_service.dart`: unpaged student list/history reads, whole-school identity duplicate-validation sets, per-row custom definition queries, buffered bulk PDFs. Impact: latency/memory under scale; N+1-shaped import work. No measured production load. Use existing paged APIs in UI, batch definition reads and load-test realistic large schools; preserve search/selection semantics.
3. **Capability logging/cache consistency — PARTLY FIXED docs.** `OPERATIONS.md`, `app/api/public_forms.py`, `app/api/card_templates.py`, `app/api/public_verification.py`: normal observed Render command retains access logs; public capability URLs can appear there. Public form responses lack explicit no-store and public Design failure lacks the success cache policy; verification has no-store. Impact: retention/cache exposure potential, not a demonstrated credential disclosure. Startup guidance now consistently recommends `--no-access-log`; actual production settings unchanged. Review upstream log retention and consistent token-endpoint headers in authorized operations phase.
4. **Temporary artifact retention — NOT FIXED.** `app/core/bulk_student_photos.py`, `app/api/bulk_personnel_photos.py` and import preview/temp workflows: bulk-photo imports perform on-access expired-manifest cleanup, but no periodic cleanup/provider orphan sweep was verified; spreadsheet preview expiry rejects stale artifacts. Impact: abandoned artifacts can persist when no further school import triggers on-access cleanup. Define retention and idempotent cleanup, test crash/retry behavior.
5. **Archive UX/personnel restoration/logo removal — NOT FIXED.** `app/api/personnel.py`, `app/api/schools.py`, Flutter `lib/screens/personnel_screen.dart`, `lib/screens/school_profile_screen.dart`: incomplete restore/archive listing and explicit logo removal; archived history access is limited by active lookup. Impact: operators need controlled manual support. Define restoration/history policies before adding low-level toggles.

### P3 — optional/post-1.0

1. **Legacy cleanup candidates — NOT REMOVED.** Flutter static main import graph reaches 103/121 sources including conditional download implementations. Candidate unused chains/files: `lib/core/base_provider.dart`, `lib/models/student.dart`, `lib/providers/student_form_provider.dart`, `lib/repositories/{api_student_repository,sqlite_student_repository,student_repository}.dart`, `lib/data/sqlite/sqlite_student_repository.dart`, `lib/screens/{preview_screen,settings_screen}.dart`, `lib/theme/app_text_styles.dart`, `lib/utils/{app_constants,input_formatters,text_utils}.dart`, `lib/widgets/{action_buttons,card_preview,date_picker,image_picker,text_input}.dart`. Evidence: not reachable from application main import graph, not proof of no tests/tooling consumers. Impact: maintenance confusion; removal risk: hidden references. Verify whole-repository references and remove in narrowly tested cleanup. Active API provider/widgets and Designer v2 remain. Legacy admin role/credential/template compatibility intentionally retained. Backend cross-platform-looking directories were not tracked runtime code; no deletion based on names.

## Security/operations review

Reviewed all 66 backend application Python sources across API/models/schemas/core, config/startup, migrations and tests; Flutter shell/routes/providers/API/models/widgets/Designer/PDF and documentation. Runtime is FastAPI/SQLAlchemy with Supabase PostgreSQL + lazily initialized Storage and Flutter/Vercel. No new secret material was printed or rotated. No tracked backend repository/services runtime abstraction was invented; development/test files and compatibility code retained.

Checks included required settings, CORS allowlist/outer error wrapper, generic readiness failures, authentication/public throttling, trusted-proxy hop configuration, path/filename validation, credential purpose/version/expiry, minimal public disclosure, storage cleanup and migration discipline. In-memory throttles are process-local; capacity/proxy deployment configuration and distributed abuse controls require operations verification. Backup/restore and PostgreSQL patch-level checks remain release gates rather than claimed executed drills. Public media URLs and client-visible PDFs warrant organization retention/access review; no public-bucket policy was changed.

Canonical baseline security scan completed before fixes: scan ID `b09321cd-ee18-476c-8c40-bca8a1a0f918`; three findings (XLSX medium, other two low), coverage complete. Artifact: `C:/Users/smith/AppData/Local/Temp/codex-security-scans-BBvbYl/id_card_backend/b56e14d91f7adf978d5005696d0d51465b533b3d_20260916T013331Z_2rnn31oc/report.md`. Baseline report remains immutable and describes pre-fix code; local regression results above document remediation. Scan tool-reported usage: 11,315,104 input, 10,890,752 cached input, 27,162 output, 1,673 reasoning, 11,342,266 total tokens, four threads; cached totals are not unique incremental review work. Daybreak advisory access was not granted, so private advisory coverage was unavailable; see https://chatgpt.com/cyber.

## Repeatable disposable staging smoke plan

1. Use an isolated staging database/bucket, two disposable schools and distinct Platform Admin, School Admin, Card Operator, Teacher, Staff and inactive-user accounts. Record versions/HEAD/migration state, backup and test-data identifiers. Do not put tokens/passwords/PII in logs or this report. Production is read-safe only until specifically authorized.
2. Login each role, reject bad/inactive credentials, check `/users/me`, switch schools and verify list/basket/context refresh. Attempt known other-school UUID reads/writes; assert denial without mutations. Test role grant/change/revoke and refreshed authority. Document school/user lifecycle gaps; any DB setup/restoration belongs only in disposable staging with a reviewed script.
3. Create student, Teacher and Staff with custom data; add/replace/remove photos. Exercise Grid/import/bulk photos including duplicate admission, ambiguous casefold, bad coordinate, wrong type/school and required-photo failures. Record first actual Supabase initialization/upload and provider object state before/after replacement/removal/retry cleanup.
4. For each identity perform pending → correction → edit → verified; generate individual/front-back and bulk/imposed PDFs and add/remove basket entries. Assert PDF generation alone changes no verification/printed state. Explicitly mark printed twice, check counts/timestamps/history and unauthorized role denials. Test inactive identity/school behavior and administrator student restore.
5. With disposable public tokens test form fetch/valid submit/duplicate 409/required photo/pending initial state; form inactive/expired/inactive school rejection. Test valid/expired/tampered/version/purpose-invalid student credential and inactive identity/school; verify minimal fields and no-store. Personnel public credentials remain out of scope.
6. In UI test each role navigation/disabled actions, student/personnel lists/search/edit/history/Grid/import/Designer/cards/basket, public routes outside shell, responsive layouts, deep refresh, back/forward and logout. Do not treat HTML fallback alone as rendering success.
7. Observe an independently authorized normal Render startup, record command/start/import if visible/bind/live and warm/cold health timings; no diagnostic wrapper unless normal startup fails. Verify first real media operation. Run backup restore in disposable environment and compare row/media manifests, then clean up test school/records/objects under approved reversible procedure.
8. Run backend pytest/compileall/Alembic heads/diff check; Flutter pub get/analyze/test/diff check. Read-only production command from repository root:

```powershell
.venv\Scripts\python.exe scripts/readiness_readonly_smoke.py --backend https://id-card-backend-vcz5.onrender.com --frontend https://campusid.co.in --output docs/readiness-smoke-results.json
```

Record each actual operation/result and unresolved gap before signing release gates. Recommended next phase: implement school/user lifecycle with agreed authority/archive rules, then execute this staging plan. Do not start deferred modules.

## Exact local change/status record

Backend `git status --short` (pytest-cache directory warning is an environment limitation):

```text
 M CHANGELOG.md
 M OPERATIONS.md
 M README.md
 M RELEASE_CHECKLIST.md
 M app/api/bulk_student_photos.py
 M app/api/public_forms.py
 M app/api/students.py
 M app/core/student_imports.py
 M docs/PRODUCTION_CHECKLIST.md
 M tests/test_public_forms.py
?? docs/READINESS_AUDIT_2026-09-16.md
?? docs/readiness-smoke-results.json
?? scripts/
?? tests/test_readiness_smoke.py
```

The sole new script is `scripts/readiness_readonly_smoke.py`. Flutter `git status --short` is empty. README/release/production checklists link this report and the unresolved staging gates; OPERATIONS startup guidance and CHANGELOG document local fixes. No code was deleted; no Flutter source changed.

## P1 gap-closure addendum — 2026-09-16

This addendum supersedes the earlier “not fixed” status for school lifecycle, account lifecycle, and the disruptive 30-minute session. Versions remain backend **0.10.0** and Flutter **0.10.0+10**. The local repository now has one Alembic head, **e8f5b21c0d93**; production was read-only and remains on **d7e4a10b9c82**.

### Closed locally

1. **Workday session interruption.** Access tokens remain 30 minutes by default. Login now creates a hashed, server-owned rotating refresh session with a configurable 720-minute absolute lifetime. Refresh and logout have separate rate limiting. Access and refresh token purposes are enforced; rotation replay revokes the session. Missing/inactive users, expired/revoked sessions, account deactivation and password resets fail closed. Validation responses omit submitted token/password input.
2. **Flutter renewal and draft safety.** The HTTP wrapper renews one minute before expiry, shares one in-flight refresh across concurrent requests, retries the original request once after a successful refresh, and supports multipart replay. A transient network error retains credentials/drafts for retry; invalid refresh state clears auth. Tests exercise five concurrent requests, proactive renewal, one retry, transient failure, and a real unsaved student form.
3. **School lifecycle.** Platform administrators can create/list active and inactive/edit/activate/deactivate schools in the UI. Existing scoped profile fields and logo upload remain, and explicit logo removal was added. Hard deletion is intentionally unavailable.
4. **User lifecycle.** Platform administrators can search/create/edit/activate/deactivate accounts, reset passwords, and promote/demote platform administrators. Existing school-scoped assignment endpoints/UI add, change and remove roles across multiple schools. Self-deactivation/self-role changes and removal of the last active platform administrator return conflict responses. Deactivation/password reset revokes sessions.
5. **Student list loading.** The Flutter directory now requests bounded 100-row pages, debounces server search, and discards stale responses.
6. **Import lookups.** Student custom-field definitions are loaded once per school/type validation batch. Personnel already used batch definitions; the earlier suggestion that it had the same N+1 issue was incorrect.
7. **Launch configuration.** Organization name defaults to CampusID. Support email, privacy notice and terms are build-time settings. `campusid@proton.me` is now the configured support address; approved privacy and terms text remain open.

### Authorization matrix recheck

| Role | Effective scope after local changes |
|---|---|
| Platform Admin | Global school and account lifecycle; all school-scoped administration after selecting a school |
| School Admin | Assigned-school profile/logo, academic structure, ordinary school role assignments, identity CRUD/lifecycle/history, imports/Grid, templates/cards/printing and Public Forms |
| Card Operator | Assigned-school identity data, imports/Grid, cards/PDF/Print Basket, and explicit mark-printed actions; cannot activate/deactivate identities, verify/correct them, or access administrator-only history/settings |
| Teacher / Staff | Read-only supporting access to assigned-school academic structure under the existing policy; no global/school/account lifecycle authority |
| Anonymous | Token-scoped public form, public design, and public verification routes only; no bearer authority |

The earlier audit grouped mark-printed with administrator-only verification/history. That was too broad: established policy permits Card Operators to mark printed while retaining administrator-only activation, verification/correction and audit-history controls. The inactive-school Public Form guard and administrator-only student activation fix remain covered.

### Evidence and limits

Local backend tests cover login, refresh purpose separation, rotation/replay, logout, invalid/expired/missing/inactive cases, live role/school changes, school activation, account safeguards, password revocation, and token-safe error/log behavior. Existing disposable route tests cover Student, Teacher and Staff create/edit → correction → verification → print/reprint → audit behavior, custom fields, Grid permissions, and school isolation. Flutter tests cover Platform Administration, bounded student paging/search and renewal without losing an unsaved student form.

The 18/18 production-safe probes passed and are recorded in `docs/p1-closure-readonly-smoke-results.json`: health/readiness/OpenAPI/CORS and Flutter route fallbacks responded as expected. This did not prove authenticated rendering or workflows. The smoke probes themselves were read-only. A later explicitly approved cleanup removed the reviewed production test data and media while retaining `testadmin`; no production migration was applied.

### Open P1 gates

1. Apply `e8f5b21c0d93` in an authorized release window before deploying session-enabled backend code, then run authenticated multi-role staging smoke.
2. Supply approved privacy and terms content. CampusID and `campusid@proton.me` are configured as the organization and support address.
3. Run real disposable staging UI/media journeys: Student/Teacher/Staff upload/replace/delete, school logo, bulk temporary upload/promotion/cleanup, browser refresh/back-forward, and multi-role sessions. Production storage cleanup removed the reviewed objects, but upload/replace/delete lifecycle behavior was not exercised.
4. Change and verify the actual Render start command includes `--no-access-log`; documentation is aligned, but deployment was prohibited.
5. Document/test first-platform-admin bootstrap and recovery as an exceptional operator procedure. Normal ongoing administration no longer needs Swagger or direct SQL.

### Deferred P2/P3

P2: archived personnel restoration/history UX, audit-history paging, legacy unbounded API compatibility endpoints, cross-tab refresh coordination, PDF/import peak-memory profiling, and a provider backup/restore drill. P3: remove only proven dead legacy platform directories/helpers in a separate cleanup. Deferred Photo Studio, collaboration/review, personnel credentials, white-label/lanyards and AI OCR remain outside this phase.

### Release decision

There is still no confirmed P0. The local implementation is a strong **0.10.x patch candidate**, but it is not ready to call **1.0.0-rc1** until the open P1 deployment, launch-content, access-log and disposable staging/media gates are completed.

### Approved production cleanup result

After explicit approval, production cleanup preserved only the active Platform Admin `testadmin` (`0cc576f8-0d30-4b82-b624-2f803551f93f`). It deleted 12 other users, 55 students, 4 classes, 3 schools, the reviewed dependent rows, and all 9 approved Storage objects. Postconditions verified that the reviewed application tables are empty, only `testadmin` remains, and all 9 objects are unavailable. Post-cleanup `/health` and `/health/check` both returned `ok`. No production migration or deployment occurred. See `docs/production-cleanup-result-20260916.json`.
