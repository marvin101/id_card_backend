# CampusID production smoke checklist

Use this checklist for a staging deployment first, then repeat it for the production candidate. Record the backend and Flutter commit IDs, operator, UTC time, deployed URLs, starting Alembic revision, target revision, and evidence links outside this repository. Never record secrets.

## Before deployment

- [ ] Back up PostgreSQL and verify the documented restore procedure and owner.
- [ ] Confirm the database revision with `python -m alembic current` and the single target head with `python -m alembic heads`.
- [ ] Review every pending migration through `b4f8c2a91d73`, including token backfill, disclosure defaults, and optional card-template back designs, and run `python -m alembic upgrade head` as an explicit release step.
- [ ] Configure every variable listed in `.env.example`; use a strong production-only `SECRET_KEY` and the exact approved frontend origins in `CORS_ORIGINS`.
- [ ] Set `PUBLIC_APP_URL` to the canonical Vercel production origin and confirm `PUBLIC_VERIFICATION_GET_RATE_LIMIT_REQUESTS` is reviewed.
- [ ] Confirm Render's trusted proxy-hop count and that login, registration, public-form, public-design, and public-verification rate limiting remain enabled or have reviewed edge equivalents.
- [ ] Confirm the Supabase Storage bucket policy, backup implications, and cleanup procedure for student photos and temporary bulk-import objects.
- [ ] Replace or explicitly approve every Flutter launch placeholder, including the support address.
- [ ] Run backend tests and compilation, Flutter tests and analysis, and both repositories' `git diff --check`.
- [ ] Build Flutter Web with the approved API origin and copy `vercel.json` into `build/web` before deploying that directory.

## Backend and migration smoke

- [ ] `GET /health` returns `200` without authentication.
- [ ] `GET /health/check` returns `200`; a controlled database outage returns generic `503` without internal details.
- [ ] `python -m alembic current` reports `b4f8c2a91d73` after migration.
- [ ] Direct Supabase Data API access to application tables remains denied by RLS, including `bulk_photo_imports`.
- [ ] Expected `400`, `401`, `403`, `404`, `409`, `413`, `422`, and `429` responses contain useful safe messages; an induced server failure returns generic `500`/`502` without provider or SQL details.

## Product journey

- [ ] Log in, select the intended school, refresh the browser, and verify the same authorized context is restored.
- [ ] Switch between two assigned schools and verify students, sessions, classes/sections, users, template, Cards, export scope, and public-share settings all reload with no stale data.
- [ ] Update the school profile; create an academic session, class, and section; and manage an ordinary user role within the selected school.
- [ ] Create and edit a student with custom fields and a photo; verify search/filter, grid edit, verification, history, and inactive/deleted behavior.
- [ ] Preview and commit a spreadsheet import; preview and commit a bulk-photo ZIP; verify failure cleanup and school/user scoping.
- [ ] Open Designer, add and edit a back side, save, reload, and compare both persisted documents exactly. Remove the back explicitly and confirm the template returns to front-only mode.
- [ ] Open the same template in two sessions; save session one, confirm session two receives `409` with local edits intact, then reload latest.
- [ ] Open Cards and verify the Front/Back preview toggle for a duplex template. Confirm the current PDF workflow remains explicitly front-only. Export filtered, selected-only, and Print Basket PDFs, including missing-photo warnings and both portrait and landscape templates. Verify one-card pages plus A4/Letter portrait/landscape sheets, margins, spacing, optional crop marks, exact card size, and calculated sheet count.
- [ ] Confirm failed export does not change print lifecycle. After a successful print action, verify print count and audit history update only for the confirmed scope.
- [ ] Enable public sharing, open the link anonymously, and confirm the browser requests no student endpoint. Disable it and confirm generic `404`; regenerate and confirm only the new token works; repeat with an inactive school.
- [ ] Confirm anonymous template mutation returns `401` and a non-admin share-management request returns `403`.
- [ ] Add the recommended Verification link QR source, save the template, and confirm Cards/PDF output contains a short `/verify/<token>` URL rather than embedded PII.
- [ ] Enable public verification with an intentionally limited field set; scan a card and confirm only those fields, school identity, and lifecycle status appear.
- [ ] Disable one student's link and confirm generic `404`; re-enable it, regenerate the link, confirm the old QR fails, and reprint the card with the new link.
- [ ] Disable school-wide verification and confirm every student link becomes unavailable without changing the saved tokens.
- [ ] Confirm an anonymous verification request has no bearer header, a non-admin cannot manage disclosure/link settings, and sensitive fields cannot be selected.
- [ ] Let a token expire, refresh a protected route, and verify local auth/school state clears without a redirect loop. Log in again, then log out and confirm protected history is inaccessible.

## Hosting and rollback

- [ ] Refresh `/design`, `/cards`, `/public/designs/<token>`, and `/verify/<token>` on Vercel; none returns a hosting `404`.
- [ ] Verify authenticated, anonymous public-design, public-form, and upload CORS requests from the approved frontend origin; verify an unapproved origin receives no CORS allow header.
- [ ] Confirm Render uses `uvicorn app.main:app --host 0.0.0.0 --port $PORT --no-access-log`, the readiness check targets `/health/check`, and upstream logs do not retain public capability URLs without reviewed controls.
- [ ] Record the last known-good backend/frontend artifacts and the database recovery decision point.
- [ ] If rollback is needed, roll back application artifacts independently of the database; do not downgrade production data without a reviewed recovery plan.
