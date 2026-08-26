# Current State

## Adoption snapshot

Observed on 2026-08-26 before migration edits:

- Branch: `main`.
- Starting HEAD: `4c458a7da6a23563f3a01306b604c52cb546981c`.
- Upstream: `origin/main`; starting worktree was clean and already matched its
  upstream.
- Application version: `0.8.1`, sourced by `src/bgm_side_b/_version.py` and
  referenced by `pyproject.toml`.
- A local `gh-pages` branch exists and is protected from this adoption task;
  no Pages mutation is part of the migration.

## Current capabilities

- The repository contains the SQLite-backed sync/admission pipeline, static
  offline/incremental build, existing-tree serve command, PWA runtime, and
  prepared-state release workflow described by the active specialist docs.
- The current frontend and user guide implement full-width mobile detail,
  draft/apply mobile filtering, continuous Quarter browsing, paginated Archive
  browsing, and `Side B` PWA identity.
- The local workspace, SQLite, covers, reports, derived site, and prepared
  state remain private/derived and are intentionally not serialized here.

## Adoption status

- The migration is documentation/instruction ownership work only; no product
  runtime, schema, data, release, or generated-site behavior is being changed.
- The named migration inputs `07-SUPPORTING_DOCS_MANIFEST.md` and
  `08-LEGACY_ASSET_DISPOSITION.md` were not present in the supplied migration
  directory. The available read-only `LEGACY_ASSET_MANIFEST.md` contains the
  equivalent ownership classifications and approved actions and was used as
  transition evidence. No active repository owner depends on the missing files.
- The old aggregate baseline and stale sync/reset procedures are being
  retired from active navigation and preserved as historical archive material.

## Risks and pending USER CHECK

The following remain human follow-up checks and are not closed by this
adoption:

1. Real iPhone/iPad Safari verification of rapid double-tap versus normal pinch
   zoom, scrolling, and focus behavior.
2. Standalone PWA real-device verification of full-screen detail, background
   freeze, left-edge return, safe area, and installed lifecycle.
3. Live GitHub Pages browser smoke against the deployed site and source
   identity.
4. Periodic revalidation of date-stamped Bangumi API evidence.

## Delivery facts

- No live Bangumi access and no `gh-pages`/Pages publication are authorized or
  performed by this migration.
- The resulting commit hashes and source push result are reported in the
  migration TASK_RESULT; this state file records the adoption facts and stable
  delivery boundaries rather than copying private generated state.
