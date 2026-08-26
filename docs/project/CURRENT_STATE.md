# Current State

## Current

- App/product version: `0.8.1`.
- Branch: `main`; upstream: `origin/main`.
- Lifecycle stage: Stabilization.
- Current milestone: post-migration stabilization acceptance (Plan-39).
- Migration status: adoption is complete at the checkpoint baseline.
- Migration adoption audit start: `4c458a7da6a23563f3a01306b604c52cb546981c`.
  This is historical context, not the current adopted baseline.
- Migration Checkpoint adopted baseline: `7e7c7671dd9620a38c61a5d1f1aed29fd94331dc`
  (`test: guard canonical requirement owners`).
- Last verified: 2026-08-26 local repository state, focused browser evidence,
  and read-only GitHub Pages smoke.

The exact resulting HEAD of state-only documentation updates is authoritative
in its TASK_RESULT rather than being copied into this file, avoiding a circular
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

## Plan-39 acceptance evidence

- Focused Chromium browser evidence passed for mobile detail/filter/scroll and
  local motion boundaries; focused PWA evidence passed for shell control,
  quarter download integrity, queue pause/resume/cancel/reopen, Settings
  progress, and app/data update separation. These are browser evidence, not
  iPhone/iPad or installed-PWA evidence.
- Read-only GitHub Pages smoke passed for the public root redirect, the
  `2026-07` Quarter page, a detail hash (`#bgm-552533`), Archive, and Settings;
  no warning or error console entries were observed in those routes.
- The deployed `gh-pages` head is `493ed9f57defad499eea700ec878f9fdf5f2b83b`,
  with release message `release: 2026.08.24.2 [source 4c458a7]`. Settings
  reported app version `0.8.1`, PWA `supported`, Service Worker `ready ·
  controlling`, and app update `current`. The current `main` head is newer
  only through docs/tests canonical-state work, so this is not a runtime
  deployment drift or a reason to publish.
- The live manifest and `sw.js` returned HTTP 200. The inspected page resource
  references and fetched runtime assets were same-origin; no SQLite, Bangumi,
  or third-party business API marker was found in the runtime assets. The
  literal SQLite matches in Settings were historical changelog text only.

## Known limitations / risks

- Plan-39 is acceptance and documentation only; it does not change product
  behavior, configuration, generated-site output, or version.
- Browser automation and online Pages smoke cannot prove iPhone/iPad Safari
  touch behavior, standalone safe-area geometry, or installed-client lifecycle.

## Pending USER CHECK

These remain explicit real-device acceptance work and are not closed here:

1. Real iPhone/iPad Safari verification of rapid double-tap versus normal pinch
   zoom, scrolling, and focus behavior.
2. Standalone PWA real-device verification of full-screen detail, background
   freeze, left-edge return, safe area, and the complete offline quarter queue
   lifecycle.
3. Installed-app update lifecycle: `NOT EXERCISED` because no safe natural
   update candidate was available; do not manufacture one by publishing.
4. Periodic revalidation of date-stamped Bangumi API evidence.

## Active work

- No product runtime, schema, configuration, generated-site, or release work is
  active. Plan-39 automated and live Pages evidence is recorded; the next gate
  is the explicit real-device USER CHECK above.

## Workflow / delivery facts that are currently material

- No live Bangumi access, unknown-subject remote import, `release publish`, or
  `gh-pages` mutation is part of Plan-39.
- Application version remains `0.8.1`; source commits and Pages publication
  remain separate delivery events.
