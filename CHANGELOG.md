# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

- Release 0.1.1 repairs first-install PWA snapshot validation by excluding
  deployment placeholders, adds safe per-file failure diagnostics and recovery
  controls, and covers complete browser installation and offline navigation.

- Classify 2026-04 Japan TV automatically and deterministically: structured
  country facts first, exact configured region tags as fallback, then a narrow
  official-TV quarterly default with auditable evidence.
- Fail sync when candidates yield no admitted Japan TV, and warn on an unusually
  low admission rate; retain existing data generation on that failure.
- Reject empty release data in audit, build, and publish/dry-run, including
  empty markers, facts snapshots, detail pages, and quarter cards.
- Mark partial or interrupted sync data as unverified so Pages publish and
  dry-run refuse it until a complete sync advances the data generation.
- Add migration 3 to retain Bangumi subject type for offline build and audit
  classification.

- Narrow the release, sync path, and static archive to verified Japan TV in
  2026-04; remove continuation, role, voice, and character-media output paths.
- Add a read-only reduced-release data audit and documented recoverable backup
  workflow; no automated user-data purge is available.

- Stabilize complete PWA snapshots, build-bound publication facts, and deterministic TV continuation refreshes.
- Add unified, safe CLI progress reporting for sync, build, and publish, including
  plain/verbose/quiet modes, heartbeats, visible retries, and relative reports.

### Added

- Installable Python package and `bgmb` command.
- Deterministic quarter, title, tag, and source rules with TOML configuration.
- Transactional SQLite migrations, backups, and subject repositories.
- Anonymous Bangumi v0 client, quarterly discovery, and minimal fixtures.
- Subject-only `sync`, incremental rating refresh, blacklist cleanup, and safe
  JSON sync and tag-audit reports.
- Migration 2 for episodes, continuation evidence, reusable characters/persons,
  typed sync states, and generic verified media records.
- Main-story episode synchronisation, TV continuation quarters, exact configured
  main-character relations, and all listed voice actors.
- Verified subject-cover and main-character image caching with Pillow decoding,
  SHA-256, atomic replacement, orphan cleanup, and `--force-images`.
- Typed incremental orchestration, partial Ctrl+C reports, typed failure entries,
  and detail-authoritative rating refresh.
- Offline `bgmb build` for deterministic local and Pages static archives with
  shared Jinja templates, native CSS/JavaScript, safe relative paths, staged
  output replacement, content-hashed assets, and safe build reports.
- Quarterly archive filtering, sorting, safe embedded quick-drawer data,
  independent detail pages, main episodes, and subject-scoped cast display.
- Local-only verified character images; Pages WebP covers with a hard character
  media exclusion.
- Chromium regression coverage for `file://`, Pages subpaths, history state,
  and static-resource boundaries.
- Pages PWA shell, deterministic icons, verified full Cache Storage snapshots,
  pause/resume, manual updates, redownload, and clear controls.
- Deterministic release metadata and manual transactional `gh-pages` publishing
  with dry-run and local bare-remote coverage.
