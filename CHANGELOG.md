# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

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
