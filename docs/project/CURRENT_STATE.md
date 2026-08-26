# Current State

## Current

- App/product version: `0.8.1`.
- Branch: `main`; upstream: `origin/main`.
- Lifecycle stage: Stabilization.
- Current milestone: post-migration canonical-state cleanup and Migration
  Checkpoint follow-up.
- Migration status: adoption is complete at the checkpoint baseline; this task
  is docs/tests-only maintenance of canonical state and durable rationale.
- Migration adoption audit start: `4c458a7da6a23563f3a01306b604c52cb546981c`.
  This is historical context, not the current adopted baseline.
- Migration Checkpoint adopted baseline: `7e7c7671dd9620a38c61a5d1f1aed29fd94331dc`
  (`test: guard canonical requirement owners`).
- Last verified: 2026-08-26 local repository state and active canonical docs.

The exact resulting HEAD of this docs/tests-only cleanup is authoritative in
its TASK_RESULT rather than being copied into this file, avoiding a circular
requirement for a file to contain its own commit SHA.

## Major capabilities

- Canonical Project State and a single active supporting-document ownership
  registry are established.
- The former aggregate baseline and stale sync/reset procedures are preserved
  under `docs/archive/` and are not active requirements or operator guides.
- The repository contains the SQLite-backed deterministic admission/sync
  pipeline, offline incremental static build, existing-tree serve command, PWA
  runtime, and prepared-state release workflow described by the specialist
  owners.
- The current frontend and user guide implement full-width mobile detail,
  draft/apply mobile filtering, continuous Quarter browsing, paginated Archive
  browsing, and `Side B` PWA identity.

## Current technical shape

- SQLite facts, verified covers, tracked configuration, and checked-in static
  assets remain canonical inputs; runtime pages consume only generated
  same-origin resources.
- Local workspace, SQLite, covers, reports, derived site, build state, and
  prepared state remain private or derived and are intentionally not serialized
  here.
- The named migration inputs `07-SUPPORTING_DOCS_MANIFEST.md` and
  `08-LEGACY_ASSET_DISPOSITION.md` were absent from the supplied transition
  directory. The available read-only `LEGACY_ASSET_MANIFEST.md` supplied the
  equivalent classifications; no active repository owner depends on the absent
  files.

## Known limitations / risks

- This cleanup changes canonical wording and guards only; it does not change
  the existing admission, auto-exclusion, configuration, or runtime rules.
- The migration checkpoint must confirm that the taxonomy and durable rationale
  remain consistent with the specialist documents before normal feature or
  refactor planning resumes.

## Pending USER CHECK

These remain normal post-migration acceptance work and are not closed here:

1. Real iPhone/iPad Safari verification of rapid double-tap versus normal pinch
   zoom, scrolling, and focus behavior.
2. Standalone PWA real-device verification of full-screen detail, background
   freeze, left-edge return, safe area, and installed lifecycle.
3. Live GitHub Pages browser smoke against the deployed site and source
   identity.
4. Periodic revalidation of date-stamped Bangumi API evidence.

## Active work

- No product runtime, schema, configuration, generated-site, or release work is
  active. The next gate is the Migration Checkpoint review of this docs/tests-
  only canonical-state cleanup.

## Workflow / delivery facts that are currently material

- No live Bangumi access, unknown-subject remote import, `release publish`, or
  `gh-pages` mutation is part of this cleanup.
- Application version remains `0.8.1`; source commits and Pages publication
  remain separate delivery events.
