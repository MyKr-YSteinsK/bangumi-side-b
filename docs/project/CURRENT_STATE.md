# Current State

## Current

- App/product version: `0.8.3`.
- Branch: `main`; upstream: `origin/main`.
- Lifecycle stage: Production.
- Current milestone: Production lifecycle checkpoint (Plan-41).
- Migration status: adoption is complete at the checkpoint baseline.
- Migration adoption audit start: `4c458a7da6a23563f3a01306b604c52cb546981c`.
  This is historical context, not the current adopted baseline.
- Migration Checkpoint adopted baseline: `7e7c7671dd9620a38c61a5d1f1aed29fd94331dc`
  (`test: guard canonical requirement owners`).
- Last verified: 2026-08-27 local repository state, bounded Plan-44
  validation, and user-reported real-device acceptance.

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
- The subsequent user report says the requested real iPhone/iPad Safari and
  standalone PWA checks were completed with no problems observed. This closes
  the real-device acceptance gap for the lifecycle checkpoint; device model,
  iOS version, and Safari build were not supplied and are intentionally not
  inferred.
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

## Plan-42 maintenance evidence

- The quarter-boundary admission rule now defaults to the natural calendar
  quarter. A 1–2 episode TV run remains in that quarter; a next-quarter
  exception requires bounded regular main-episode dates, strong target-quarter
  evidence, and no structured conflict. `TV_QUARTER_BOUNDARY` remains REVIEW
  only and is not an automatic permanent-exclusion issue.
- The authorized live refresh completed for `2025-10..2026-01` with complete
  facts and no errors. The former mistaken auto-blacklist IDs `506120` and
  `565802` are absent from automatic exclusions, are stored as TV premieres in
  natural quarter `2025-10`, and have no duplicate `2026-01` premiere.
- `bgmb review 2025 10`, `bgmb review 2026 1`, and `bgmb audit` completed with
  zero unresolved subjects; the affected `2025-10` and `2026-01` outputs were
  rebuilt successfully. No Pages publication was performed.

## Plan-44 review convergence evidence

- Phase A measured the fresh 69-row corpus without changing policy first.
  Thirty-three rows had provenance in the bounded 2025-04 / 2025-07 target
  scope; the remaining 36 rows belonged to other historical provenance.
  Four rows were the known Search lookback Movie spillovers. Twenty-four of
  the 29 target-provenance Japanese ambiguity rows had reliable
  `rating_count < 30` evidence; the remaining cases were not shortcut by
  weak evidence. All 69 authorized read-only canonical detail probes
  completed successfully.
- Exact structured evidence remains the only Japanese signal. The measured
  corpus supplied exact public region tokens and verified country-field
  examples; the implementation now covers the verified Infobox keys
  `制片国家/地区`, `国家/地区`, `制片国家`, `地区`, the `Japan` alias, and
  the documented `・` separator. Two target conflict cases are accepted as
  Japanese-inclusive co-productions under the new same-source-compatible
  rule; independent Japan/non-Japan sources remain REVIEW.
- The bounded `2025-04..2025-07 --refresh-existing` run completed with facts
  and covers complete and zero sync errors. The first post-policy refresh
  aggregated 125 accepted TV, 17 accepted Movie, 40 rejected non-Japanese,
  156 blacklisted, 14 irrelevant candidates, and 29
  `outcome_dominated_low_rating` exclusions plus 2 deterministic
  `low_rating_count` exclusions. Follow-up refreshes reconciled stale
  review-only subjects; the final idempotent refresh added no duplicate
  exclusions and rebuilt the unified static site successfully.
- `bgmb review 2025 4` and `bgmb review 2025 7` both report zero unresolved
  subjects. The four old Movie date-mismatch rows no longer produce REVIEW or
  `assign` guidance. The global `bgmb review` contains 32 unresolved subjects:
  27 classification-unknown, 2 structured-evidence conflicts, and 3
  region conflicts. Seven remain inclusion-changing cases in the bounded
  target; the other 25 belong to other quarters and were intentionally not
  refreshed by this Plan. The three target rows that had become deterministically
  non-Japanese were removed from their stale review-only records.
- The unified `bgmb audit` passed with no pending quarter state and six
  publishable managed quarters. The final database contains 472 subjects and
  no duplicate premiere rows were observed.
  The application schema remains the existing strict schema-2 contract; no
  SQLite migration, PWA runtime change, Pages publication, or `gh-pages`
  mutation was made.
- The protected `config/bangumi.toml` diff retains the user's existing
  exclusions and the 31 new Plan-44 automatic exclusions: 29
  `outcome_dominated_low_rating` and 2 deterministic `low_rating_count`
  entries. No existing exclusion was blindly removed; the generated
  workspace/report/config state remains local and is not folded into the
  source commit.
## Known limitations / risks

- Plan-39 was acceptance and documentation only; it did not change product
  behavior, configuration, generated-site output, or version.
- Plan-42's bounded refresh encountered transient Bangumi API retries but
  completed without sync errors. Other quarters were not refreshed by that
  operation.
- Browser automation and online Pages smoke cannot prove iPhone/iPad Safari
  touch behavior, standalone safe-area geometry, or installed-client lifecycle;
  current closure of those checks is based on the explicit user report above.
- Application-update candidate: `NOT EXERCISED` during Plan-39 because no safe
  natural candidate existed. This was an allowed non-failure state; no publish
  was performed to manufacture one.
- Date-stamped Bangumi API evidence remains periodic maintenance and an
  external-dependency risk, not a Production promotion blocker.
- The global review queue still includes 25 unresolved rows from quarters
  outside the bounded Plan-44 refresh. They remain genuine future/other-quarter
  operator work and were not changed without an authorized refresh.

## Pending USER CHECK

Plan-39's real-device Safari and standalone PWA checks were explicitly
reported completed with no problems observed. For Plan-42, manually inspect
the generated `2025-10` page for IDs `506120` and `565802`, confirm neither is
on the `2026-01` premiere page, and spot-check 2–3 newly auto-excluded works;
there were no newly auto-excluded works in that run, so that last spot-check
is not applicable. For Plan-44, open the generated `2025-04` and `2025-07`
pages, spot-check several known Japanese works and obvious non-Japanese works
from the former REVIEW corpus, and confirm the remaining global REVIEW is
small and understandable. These are user sanity checks; the bounded
SQLite/report/static-file assertions have already passed.

## Active work

- No product runtime, schema, or release work is active after the Plan-44
  maintenance change. The lifecycle remains Production; normal maintenance,
  bug fixes, and deliberate feature work still require formal Plans and
  risk-appropriate validation.

## Workflow / delivery facts that are currently material

- Plan-42 used only the authorized bounded live Bangumi refresh for
  `2025-10..2026-01`; Plan-44 used only the separately authorized
  `2025-04..2025-07` refresh and the 69-ID read-only detail evidence. No other
  live refresh, `release publish`, or `gh-pages`
  mutation was performed.
- Application version is `0.8.3`. Source commits and Pages publication remain
  separate delivery events; this maintenance change does not imply a Pages
  publication.
