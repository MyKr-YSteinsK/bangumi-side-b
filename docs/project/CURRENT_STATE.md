# Current State

## Current

- App/product version: `0.8.5`.
- Branch: `main`; upstream: `origin/main`.
- Lifecycle stage: Production.
- Current milestone: Production maintenance checkpoint (Plan-47).
- Migration status: adoption is complete at the checkpoint baseline.
- Migration adoption audit start: `4c458a7da6a23563f3a01306b604c52cb546981c`.
  This is historical context, not the current adopted baseline.
- Migration Checkpoint adopted baseline: `7e7c7671dd9620a38c61a5d1f1aed29fd94331dc`
  (`test: guard canonical requirement owners`).
- Last verified: 2026-08-29 local repository state, bounded Plan-47 live
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
  only and is not an automatic-exclusion issue.
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

## Plan-45 decision and automatic-exclusion lifecycle evidence

- The 31 existing Japanese decisions were parsed and retained: 9
  `ACCEPTED_JAPANESE` and 22 `REJECTED_NON_JAPANESE`, with unique valid IDs and
  no silent reversal. `184017` is now an explicit manual exclusion with the
  rationale `Bangumi Wiki 动画测试用沙盘`; its stale SQLite facts and
  `JAPANESE_REGION_CONFLICT` review row were removed through the existing
  recoverable blacklist lifecycle.
- A one-time fresh canonical audit covered all 369 current automatic
  exclusions. The measured categories were 240 information-insufficient, 100
  low-rating, and 29 outcome-dominated exclusions. No stale/restorable,
  unavailable/deleted, hard-scope, or unresolved high-impact IDs were found;
  therefore no affected-quarter live refresh was required.
- `646464` remains automatically excluded: its canonical rating total is 37,
  but platform `其他` leaves the media format unresolved and the admission
  result remains `SEARCH_ONLY_MEDIA_UNRESOLVED`. Rating growth alone does not
  override a missing in-scope media identity.
- The identity gate found `604330` and `604331` to be legitimate independent
  in-scope theatrical Movie subjects released on the same date; their own
  canonical rating totals are 0, so each remains a valid low-rating automatic
  exclusion. `582501` is a separate WEB aggregate/out-of-scope subject and is
  not used to restore or replace the two theatrical subjects. No alias model
  was introduced.
- The lifecycle now re-evaluates an old automatic exclusion when its subject is
  rediscovered in an actively refreshed quarter, while manual exclusions,
  scope rejection, and unresolved inclusion-changing REVIEW remain binding.
  Configuration removal and SQLite/review convergence are transactional with
  rollback coverage; repeated refreshes are idempotent. The offline lifecycle
  corpus and focused regression passed.

## Plan-47 country and theatrical-scope convergence evidence

- The 2025-01 control audit separated two defects. Eleven known foreign works
  had remained `UNRESOLVED` and then entered
  `outcome_dominated_low_rating`; this was not an automatic
  `ACCEPTED_JAPANESE` false positive. Exact public `法国` evidence now rejects
  `556595` and `624369`; the other nine evidence-missing controls use the
  user-approved Japanese-scope decisions. `533466` retains its separately
  approved non-Japanese decision. Broad `欧美` remains unresolved. The current
  local fact corpus has zero rows for all four supported country Infobox keys,
  so generic `地区` was neither broadened nor removed without evidence.
- Canonical `platform=剧场版` was the direct media source for `537745`,
  `551459`, and `611704`; Browse fallback was not the root cause. Exact
  Infobox `其他=游乐设施电影` and
  `其他=プラネタリウム上映作品` now hard-reject `537745` and `611704`.
  `551459` exposes no reusable structured special-venue marker, so it is the
  isolated manual exclusion. No title, summary, URL heuristic, media override
  family, schema change, or migration was introduced.
- The single authorized `2025-01 --refresh-existing` completed with facts and
  covers complete: 228 discovered, 57 accepted TV, 9 accepted Movie, 36
  rejected non-Japanese, 53 blacklisted, 4 outcome-dominated, 0 new automatic
  exclusions, and 0 errors. Sixty-five old automatic IDs were reconsidered;
  13 hard-rejected IDs were transactionally reported as `auto_reconciled`, 0
  were restored, and the separately migrated `551459` counted as one manual
  exclusion. The 48 warnings were 47 bounded continuing-not-confirmed notices
  and one retained earlier premiere for `404753`.
- The resulting configuration contains 2 manual exclusions, 414 automatic
  exclusions, and 42 Japanese decisions (10 Japanese / 32 non-Japanese).
  None of `483865`, `529580`, `531939`, `536356`, `536370`, `536405`,
  `556595`, `556742`, `561637`, `624369`, `640936`, `537745`, `551459`, or
  `611704` remains automatic. The valid low-rating controls `504666`,
  `505378`, `517532`, `523821`, and `529199` remain automatic exclusions;
  `504666` also has an explicit Japanese-inclusive decision so later rating
  growth cannot turn missing country evidence into a false rejection.
- The automatic incremental build completed with 11 written, 530 reused, and
  0 deleted artifacts; `2025-01` and its affected `2025-04` continuation
  projection were rebuilt with no build errors or warnings. The generated
  `2025-01` data contains exactly 57 TV premieres and 9 Movie premieres and
  none of the above control IDs. Target and global REVIEW both report zero;
  unified audit passes with 513 subjects and seven publishable quarters.
- A recoverable pre-live private snapshot was stored outside the repository;
  its private absolute path is intentionally not tracked. No Pages publication
  or `gh-pages` mutation was performed.
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
- The final global review queue is empty after the authorized Plan-47 refresh;
  future review work still requires fresh bounded evidence and explicit
  operator adjudication.

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

For Plan-45, review the 369-ID audit summary, confirm `184017` is absent from
generated output and REVIEW, and spot-check the retained exclusions listed in
the TASK_RESULT. No restored ID required an affected-quarter page check. These
are user sanity checks; the bounded canonical, SQLite, config, and static-build
assertions have already passed.

For Plan-47, open the generated local `2025-01` page and spot-check expected
Japanese titles. Confirm `483865`, `537745`, `551459`, and `611704` are absent;
optionally confirm one valid Japanese low-rating control such as `505378` is
also absent for the intended low-rating reason. These are user sanity checks;
the report, SQLite, configuration, generated JSON, review, and audit assertions
have already passed.

## Active work

- No product runtime, schema, or release work is active after the Plan-47
  maintenance change. The lifecycle remains Production; normal maintenance,
  bug fixes, and deliberate feature work still require formal Plans and
  risk-appropriate validation.

## Workflow / delivery facts that are currently material

- Plan-42 used only the authorized bounded live Bangumi refresh for
  `2025-10..2026-01`; Plan-44 used only the separately authorized
  `2025-04..2025-07` refresh and the 69-ID read-only detail evidence. No other
  live refresh, `release publish`, or `gh-pages`
  mutation was performed.
- Plan-45 used only the authorized read-only canonical audit of the 369
  automatic IDs, the exact `646464` probe, and the
  `604330`/`604331`/`582501` identity evidence. No Plan-45 live sync or
  affected-quarter refresh was necessary because no stale/restorable ID was
  proven.
- Plan-47 used only the bounded control detail probes and the single authorized
  live `2025-01 --refresh-existing`. The run encountered Bangumi throttling but
  completed through built-in retry/backoff with zero sync errors. No second
  refresh, `release publish`, Pages publication, or `gh-pages` mutation was
  performed.
- Application version is `0.8.5`. Source commits and Pages publication remain
  separate delivery events; this maintenance change does not imply a Pages
  publication.
