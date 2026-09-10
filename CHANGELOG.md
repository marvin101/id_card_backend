# Changelog

All notable changes to CampusID are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions before 0.7.0 below are a reconstructed milestone history from repository history and the implemented product state; they do not imply that matching Git tags or formal releases existed.

## [Unreleased]

### Added

- Added optional back-side Designer documents to card templates and public
  design previews, with independent v2 validation and matching front/back
  canvas dimensions for duplex alignment.

### Changed

- Legacy clients that omit `back_design` preserve an existing back side, while
  an explicit `null` removes it.

## [0.8.0] - 2026-09-10

### Added

- Added validation for versioned Designer v2 documents while preserving legacy v1 templates.
- Added orientation-consistency and physical-size bounds for custom Designer v2 canvases.
- Added type-specific Designer v2 data/style validation, deterministic layer indices,
  and editor-aligned numeric bounds.
- Added a validated Designer v2 `qr_code` element contract for static, system-field,
  and custom-field content with print-safe geometry and styling limits.
- Added scoped multi-field QR payloads with unique bindings and structured JSON
  or labeled-text output.
- Added revocable per-student public verification links, school-level disclosure
  controls, generic anonymous failure responses, and independent read throttling.
- Added a QR-only `verification_url` binding so cards can encode a capability
  link without embedding student PII.

### Security

- Public verification is disabled by default, limited to an explicit safe-field
  allowlist, rate-limited independently, non-cacheable, and returns one generic
  not-found response for invalid, disabled, revoked, or inactive records.

## [0.7.0] - 2026-09-02

### Added

- Excel Grid GET/PATCH API with bounded paging, search, academic filters, lookup data, and dynamic custom fields.
- Whitelisted bulk row editing with academic validation, structured row/cell errors, optimistic `updated_at` conflict detection, and atomic save behavior.
- Change-aware audit recording for grid updates.

### Changed

- Adopted a shared CampusID Semantic Versioning policy across backend and Flutter.
- Set FastAPI metadata from the authoritative backend version module.

## [0.6.0]

### Added

- School-branded Public Form configuration and non-enumerable anonymous links.
- Anonymous pending-student submissions with configured system/custom fields, photo policy, duplicate protection, rate limits, and audit source tracking.

## [0.5.0]

### Added

- Pending, Needs Correction, and Verified student lifecycle.
- Student audit history, verification actions, and printed/reprint tracking.

## [0.4.0]

### Added

- Excel student template, upload, preview, validation, and commit workflow.
- Bulk student-photo staging, matching preview, commit, promotion, and storage cleanup.

## [0.3.0]

### Added

- Dynamic school-scoped student fields.
- School profile and logo operations.

## [0.2.0]

### Added

- Core student/card management, photo operations, card templates, and data APIs used for PDF workflows.

## [0.1.0]

### Added

- Authentication, school/user records, role-based authorization, multi-school access, and academic-structure foundations.
