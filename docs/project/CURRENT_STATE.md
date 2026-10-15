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
- Last runtime/data verification recorded here: 2026-08-29 bounded Plan-47
  live validation; real-device acceptance remains the earlier user-reported
  Plan-39 evidence below.
- Plan-50 Rev2 began from source baseline
  `806cf96541db29142804a363a3d8b27e79e19b20` (`main` / `origin/main`); the
  remote source HEAD matched, and the tracked worktree changes were limited to
  the five scoped Plan-50 files. At that pre-delivery check, local `gh-pages`
  was `ddec0cfdfff490dec4175ed0dbcf44aca1ba02d2` and remote `gh-pages` was
  `b35e51ece216e3d8cba8cb3f2a32ec64c887513c`; neither was changed. This was
  not runtime, data, production, or real-device acceptance. Final source
  delivery evidence is recorded in this task's TASK_RESULT.

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
- At the Plan-39 observation, the deployed `gh-pages` head was
  `493ed9f57defad499eea700ec878f9fdf5f2b83b`,
  with release message `release: 2026.08.24.2 [source 4c458a7]`. Settings
  reported app version `0.8.1`, PWA `supported`, Service Worker `ready ·
  controlling`, and app update `current`. At that checkpoint, `main` was newer
  only through docs/tests canonical-state work. These are historical deployment
  observations, not the current remote HEAD or a reason to publish.
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

- Plan-49 Rev1 made a bounded Quarter/Archive search-response change: ordinary
  search keeps the existing query pipeline and row identities while avoiding
  result FLIP, broad animation cancellation, and result-geometry reads. Sort,
  media collection, and Grid/List local motion remain. No schema, facts,
  controlled configuration, PWA/cache contract, release, or formal `dist/site`
  changed. This does not approve or advance the A–F candidate program; the
  lifecycle remains Production, and later work still requires formal Plans and
  risk-appropriate validation.
- Plan-50 Rev2 closed the Rev1 partial result at the revised minimum-evidence
  boundary. Gates A and C reuse the code-matched Rev1 results (380 non-browser,
  111 unified Chromium, and 32 Chromium/WebKit tests); after the final focused
  view-mode change, five targeted Chromium/WebKit cases passed. The patch
  removes the fixed quarter-navigation wait, bounds motion candidates to
  viewport-adjacent result groups, tracks its own active animation handles, and
  updates Grid/List mode without rerendering unchanged result data. Existing
  503-row reduced-motion measurements on the pre-focus-fix code sampled the
  small/large × Quarter/Archive × motion-preference combinations 30 times; the
  large Quarter / `reduce` / 4× CPU view switch measured about 2.4 s median,
  4.3 s P95, and 4.7 s maximum, with zero geometry reads, animation scans, or
  animation starts. A low-instrumentation replay measured 3.43 s and 2.69 s
  for Grid→List and List→Grid. A follow-up stage probe on the matching source
  attributed about 3.17 s to the explicit `button.focus()` call; dataset,
  selector, and ARIA updates together took under 1 ms. Both view-mode handlers
  now rely on browser-native activation focus and omit that forced focus call.
  The targeted suite verified focus, view state, and row identity. Under Rev2's
  minimum evidence standard, Gate B passed on the structural counts, visible
  candidate checks, and bounded diagnosis; performance was not remeasured
  after the narrow focus fix, so no exact final latency or improvement
  percentile is claimed. The full large pre-change matrix remains incomplete.
  Real Safari/standalone, production, CI, and Pages were not tested. The
  Production lifecycle and A–F approval state do not change.
- Plan-51 Rev1 makes the separately authorized, bounded mobile first-screen
  layout exception: repeated header/archive decoration and explanatory copy
  leave the mobile flow, while the visible h1, current scope, navigation, and
  browse controls remain. Quarter combines media and search in one row;
  Archive uses a compact intro and inline year selector. All tested controls,
  including Grid/List, retain at least 44px touch targets. In the same loaded
  synthetic fixture at scroll 0, Chromium 149 / 390×844 measured the first
  Quarter row at 504.1px before and 317.4px after (186.8px higher), and the
  default Archive year view at 745.8px before and 442.7px after (303.1px higher).
  Visible result keys/order did not change. Measured 1440×900 geometry and
  order matched the baseline exactly; the 390px Settings header also matched.
  Seven targeted cases have passing evidence across Chromium/WebKit, 390/360px
  layouts, focus, search, detail/filter restoration, continuous Quarter rows,
  and Archive pagination. Real Safari/standalone, online production, and CI
  results were not verified. An inherited native-popover issue was reproduced
  with both old and new CSS: clicking the open menu trigger again can reopen
  it; Escape closes it in both engines. No runtime JS, PWA, facts, configuration,
  private formal site, or release behavior changed. This does not complete or
  approve the broader B phase; the lifecycle remains Production.

## Plan-52 bounded image-delivery evidence (2026-10-14)

- Plan-52 Rev1 started from `50eaad37be1a8a1c334303f03a00008df00a3ccc`
  on `main` / `origin/main`, with a clean tracked worktree. App version remains
  `0.8.5`. Quarter/Archive browse covers now use the single deterministic 640px,
  WebP quality-78 profile described by the static-build owner; original facts,
  canonical covers, and detail/Lightbox originals are preserved.
- In a same-input local Chromium 149 synthetic comparison, Quarter and Archive
  at 390×844 (DPR 2) and 1440×900 (DPR 1) each requested 286,314 cover bytes
  before and 55,240 after (80.71% less). Three distinct images included two
  800×1200 posters and one 200×300 cover, alongside a missing-cover record.
  Row keys/order and measured geometry matched. These are bounded laboratory
  results, not real-cover, full-site, production-network or real-device savings.
- Twelve targeted builder/image cases passed, including no-op, changed covers,
  retained-quarter closure/conflicts, true manifest SHA/size, alpha/orientation,
  bounded decoding and failure preservation. Four Quarter/Archive cases passed
  across Chromium and Playwright WebKit for previews, originals, row reuse and
  bounded fallback. One Chromium PWA scenario passed for missing-preview update
  failure, old active offline preview/original Lightbox, resumed completion and
  cross-quarter reference-safe GC. Facts `data_revision` stayed unchanged while
  package `revision` changed. SW/PWA implementation and integrity semantics were
  not changed. Ruff, JS syntax and diff checks passed.
- Synthetic evidence remains ignored/private. No live Bangumi, real canonical
  data/config/cover mutation, formal site build, release or Pages mutation was
  performed. Safari devices, installed PWA, online production and CI outcomes
  were not verified. Source delivery belongs in the TASK_RESULT. This bounded
  result does not approve other B/A–F work or advance the Production lifecycle.

## Plan-53 bounded Archive loading-layout evidence (2026-10-14)

- Plan-53 Rev1 began from `main` / `origin/main` at
  `89cc7567df12e6d5bbdee658b23b7a8a23c18086`, with a clean tracked worktree.
  The generated Archive HTML now starts on YEAR without inventing a year before
  the index arrives. Its toolbar is present while the first index/catalog load is
  pending; result controls remain inert and a non-record placeholder reserves
  the initial results area. Scope changes preserve latest-intent cancellation,
  including QUARTER selection while an annual catalog is in flight.
- Reusing the same synthetic SQLite, cover, generated-site fixture and Chromium
  149 setup, a loopback gate delayed `archive-index.json` and `catalog/2026.json`
  by 750ms each. Non-user-input CLS changed from 0.11245 to 0 at 390×844 and
  from 0.07468 to 0.00038 at 1440×900. After the change, toolbar/control y
  positions stayed at 211.9/273.3px on mobile and 481.0/550.4px on desktop
  through both waits; the first result row appeared at 442.7/706.6px. Scroll
  width matched the viewport at both sizes. These are local synthetic lab
  results, not online, CrUX, Safari or real-device evidence.
- Ten targeted pending/error/empty/latest-intent browser cases passed across
  Chromium 360/390/1440px and Playwright WebKit 390px. Eight existing
  scope-race, YEAR/RANGE, hash and history cases and two Archive mobile
  first-screen cases passed. Two focused builder tests, Ruff, JavaScript syntax
  and diff checks passed; the complete browser and test suites were not run.
- Probe output and screenshots remain under the ignored local
  `workspace/reports/plan53-20261014/` directory. No live Bangumi access,
  canonical user data/config/cover mutation, formal site build, PWA/SW change,
  release, Pages or `gh-pages` mutation occurred. Real Safari/standalone,
  production-network behavior and post-push CI are separate evidence; this
  bounded Plan does not approve broader A–F work or advance the Production
  lifecycle.

## Plan-54 bounded mobile-menu interaction evidence (2026-10-14)

- Plan-54 Rev1 began on `main` / `origin/main` at
  `958e1bb03598c7048e0f19df6d4255038cfe1610`, with a clean tracked worktree.
  The known second-activation issue was reproduced in Chromium 149.0.7827.55
  and Playwright WebKit 26.5. In both, the native Popover emitted `closed`
  between the trigger `pointerdown` and `click`; the click handler then saw a
  closed menu and reopened it. During this sequence WebKit also moved focus to
  `body`, while the old open path then moved focus into the menu.
- The shared handler now records primary-pointer activation intent before
  native light-dismiss, so the corresponding click completes a close instead
  of toggling the menu open again. It synchronizes actual Popover state,
  restores focus to the trigger only when needed on explicit close, and leaves
  focus in its natural order when opening. The existing fallback path was
  exercised by removing the fixture's Popover attribute and verifying its
  `showPopover()` failure recovery, repeated activation, Escape, and ARIA state.
- Targeted Chromium/WebKit tests covered the exact `closed → open → closed`
  event trace, subsequent reopening, Enter/Space, Escape focus return, Archive
  link navigation, and native/fallback state. Nine related browser cases also
  passed for outside close, placement, competing sort/Quarter surfaces, and
  Quarter/Archive shared-header behavior. Only these targeted cases ran; the
  full browser/PWA suites were not run. Real iOS Safari and installed-PWA
  behavior remain unverified.
- `docs/visual-system.md` now specifies the mobile disclosure focus behavior.
  No builder, CSS, PWA/SW, facts, configuration, database, cover, formal site,
  release, Pages, or `gh-pages` change occurred. Generated test fixtures were
  temporary; no diagnostic report or screenshot was added to the repository.
  Source delivery and post-push CI are reported in the TASK_RESULT. This
  localized fix does not approve Phase B/C or the wider A–F program and does
  not advance the Production lifecycle.

## Plan-55 PWA update-detection CI diagnosis (2026-10-15)

- CI Run `38590864009` attempt 1 failed only in Browser release smoke at
  `test_update_detection_separates_data_and_package_revisions`; its observed
  assertion was `dataUpdates=[]` instead of `['2026-07']`. Linux and Windows
  fast jobs passed. The job used Playwright 1.64.0 / Chromium 156.0.8078.4.
  One authorized same-SHA rerun (attempt 2) passed all three jobs; the parent
  source SHA `958e1bb03598c7048e0f19df6d4255038cfe1610` also passed Run
  `38578121897` with the same Playwright / Chromium versions. A prior local
  Chromium 149 run of the focused test passed once.
- Source inspection supports `TEST_ISOLATION` as the likely cause: Settings'
  asynchronous `load` handler restores the queue and starts automatic update
  detection, while the test previously waited only for Service Worker control
  before manually editing the same CacheStorage metadata and calling detection.
  The first failed CI log did not retain `hadError`, active/staging state, queue
  state, or the manifest revision observed by that call, so those runtime
  details were not directly captured and the cause is not claimed as a proven
  production defect.
- The focused test now waits for PWA readiness and startup auto-maintenance to
  complete, confirms the fixture starts with an idle queue and no quarter
  state, and includes classification, `hadError`, active/staging, queue,
  manifest-revision, and manifest-request details in any data-update failure.
  Its package-only, legacy-migration, and data-update assertions remain. The
  PWA runtime and product contract were not changed. Source delivery and CI on
  the resulting commit are reported in the TASK_RESULT.

## Plan-56 bounded browsing-context evidence (2026-10-15)

- Plan-56 Rev1 investigated the current `main` / `origin/main` source HEAD
  `44b9ad82fbd021d455c8a758780f7839b80c2422`. Existing browser tests cover
  Quarter detail hash/history and mobile scroll restoration, explicit mobile
  filter draft cancellation/application, Archive YEAR/RANGE history and
  pagination, Grid/List continuity, and stale-scope request ordering.
- Two local synthetic-site Chromium `149.0.7827.55` probes at 390×844 covered
  Quarter and Archive. After opening the mobile filter and changing an
  unapplied draft, browser Back over a same-document history entry restored the
  prior URL but left the filter workspace open and the draft selection checked
  on both pages. Requests stayed on loopback. This is browser-history evidence;
  native OS Back, Safari/WebKit, installed PWA, and real-device behavior were
  not verified.
- The observed behavior conflicts with the accepted D-007 decision and mobile
  filter baseline in `docs/visual-system.md`, which require close/back to cancel
  unapplied changes. It supports one `BOUNDED_FIX_CANDIDATE`: route mobile
  filter Back through the existing close/cancel behavior while preserving
  applied filters and detail/scope history. No implementation, C-phase
  approval, or broader A–F approval follows. No `R-010` / `R-011` entry was
  found in the tracked repository contracts; this candidate is grounded in
  D-007 and the current visual-system contract.
- Two targeted active-document / current-state guard tests passed; browser and
  full test suites and CI were not rerun. The generated sites used synthetic
  SQLite fixtures in temporary directories; no canonical data, covers, tracked
  configuration, formal generated site, PWA cache, release, Pages, or
  `gh-pages` state was changed. Production remains the current lifecycle.

## Plan-57 bounded mobile filter history evidence (2026-10-15)

- Plan-57 Rev1 implemented the existing D-007 contract on Quarter and Archive:
  a same-document Back traversal closes an open mobile filter and discards its
  unapplied draft before URL/scope restoration. Applied filters remain in
  state. The Back path does not add or rewrite history entries or force the
  explicit-close focus/list-scroll restoration. Archive route options flow
  through asynchronous scope restoration; the existing latest-intent guard is
  retained. Work began on `main` / `origin/main` at
  `1584004adc5a1f3ac6fac5ec30728c92e0a62d02` with a clean tracked worktree.
- Four targeted mobile cases passed at 390×844: Quarter and Archive in
  Playwright Chromium `149.0.7827.55` and Playwright WebKit `26.5`. They used a
  synthetic same-document history entry to exercise browser traversal and
  checked URL/state, `aria-expanded`, discarded draft, applied results, and no
  programmatic history/focus/scroll calls on filter Back. A separate Chromium
  case passed for a real Archive YEAR/RANGE Back/Forward traversal while a
  filter draft was open. Four related existing filter, detail-history, scope
  race, and range-history cases also passed. Ruff, JavaScript syntax, and diff
  checks passed; the full browser/PWA suites were not run.
- `docs/project/DECISIONS.md` D-007, `docs/visual-system.md`, and
  `docs/USER_GUIDE.md` already describe mobile draft/apply cancellation on
  close/back, so those product owners required no contract edits. Real Safari,
  installed PWA, OS Back gestures, online production, and CI were not verified.
  This bounded fix does not approve broader A–F work or advance the Production
  lifecycle; source delivery is recorded in the TASK_RESULT.

## 2026-10-15 Plan-58 Rev2 integration and opportunity audit

- The audit used source baseline
  `7847babfb62cd77f5655abf40370504a5f7c9f52` on `main` / `origin/main`,
  with a clean worktree and matching actual remote HEAD. Baseline CI run
  `38685617421` completed successfully in all three default jobs. This adds
  a later CI observation to the Plan-57 pre-delivery evidence above.
- Six synthetic targeted checks passed locally: Quarter and Archive mobile
  filter Back/Forward in Chromium, Archive pending-range history traversal,
  repeated mobile-menu activation in WebKit, builder preview/original closure
  and reuse, and PWA preview-update failure with active/staging and GC
  protection. The environment was Python 3.14.6, Playwright 1.61.0, Chromium
  149.0.7827.55, and WebKit 26.5. No actionable integration regression was
  reproduced in those paths; this is not full-suite or real-device acceptance.
- The eight-dimension review identified operator-document discrepancies:
  `release prepare` builds `dist/site` and reads Git remotes, while some
  descriptions call the whole command offline or imply it leaves `dist/site`
  unchanged; the user-guide FAQ still requests manual automatic-exclusion
  removal despite the current re-evaluation lifecycle. These are observations
  for a separately authorized documentation task, not changes to release or
  data semantics. Cross-hash history coverage and cached-content waiting on
  a slow network remain test/research opportunities, not reproduced bugs.
- The detailed report remains untracked under
  `workspace/reports/plan58-20261015/`. No runtime, test, CI, configuration,
  product, or specialist-contract changes were made. No live Bangumi access,
  real-data build/audit, release, Pages mutation, production measurement, or
  real Safari/installed-PWA validation was performed. The review does not
  approve candidate implementation, A–F as a whole, or a lifecycle advance;
  final documentation delivery belongs in this task's TASK_RESULT.

## 2026-10-15 Plan-59 Rev1 operator-document corrections

- Starting from `4650d4c2b4ab589701202d29a91339db86c79309`, the publish
  runbook, user guide, and development summary now distinguish prepare's
  local build/state/report writes, temporary dry-run staging, and read-only
  Git remote access. The build step remains offline; prepare does not access
  Bangumi, change the source branch, push, or publish Pages. The user-guide
  FAQ now follows D-019: an authorized active refresh must rediscover the ID
  and evaluate fresh canonical evidence, without requiring manual automatic-ID
  removal or promising restored admission. Durable manual exclusions remain.
- These are documentation corrections only. Release metadata documentation,
  AGENTS, product decisions, implementation, tests, CI, configuration, version,
  and the Production lifecycle are unchanged. Validation uses static source
  comparison, document guards, links, and diff checks; no real sync, build,
  prepare/publish, data/cover mutation, or Pages operation was performed.
  Final verification and source delivery are recorded in this task's TASK_RESULT;
  the other Plan-58 candidates and broader A–F work remain unapproved.

## 2026-10-15 Plan-60 Rev1 mobile filter/detail hash evidence

- Plan-60 Rev1 started from `c51340d28e628747e57bd7f6d25478167f067cd5` on
  `main` / `origin/main`, with a clean worktree and matching actual remote
  source HEAD. Two parameterized synthetic browser cases now cover Quarter and
  Archive: the user applies the `续播` filter, opens subject 101 through its real
  result button, uses browser Back, opens the mobile filter and changes an
  unapplied draft, then uses browser Forward to the UI-created `#bgm-101` entry.
  Archive reaches its `RANGE / 2026—2026` scope through the scope controls.
- The tests verify the detail URL and `history.state`, applied record identity,
  Archive scope, closed filter/ARIA state, and exactly one `popstate` plus one
  `hashchange` per traversal. During Forward with the filter draft open, no
  `pushState`, `replaceState`, `back`, explicit focus, or `scrollTo` call was
  observed. The following Back returns to the same list URL/state, applied rows,
  and saved scroll position; reopening the filter shows the applied selection.
- The three selected new paths passed in Playwright Chromium `149.0.7827.55`
  (Quarter and Archive) and Playwright WebKit `26.5` (Archive); four adjacent
  Chromium filter, Quarter detail/history, and Archive pending-range history
  tests also passed. All sites and requests used synthetic fixtures and local
  loopback only. No contract-violating product behavior was reproduced; only
  regression coverage was added, with no runtime JavaScript or product-contract
  change. The full browser/PWA suites, real Safari/iOS, production, and
  installed-PWA behavior are not established by these results. Production
  remains the lifecycle stage and the A–F program remains unapproved; source
  delivery and post-push CI evidence are recorded in the TASK_RESULT.

## 2026-10-15 Plan-61 bounded PWA weak-network cache evidence

- Plan-61 Rev1 used source baseline `d2eec79ee290556c2af5dd1cb00ece740813e2d5`
  on `main` / `origin/main`; the tracked worktree was clean and actual remote
  `main` matched. Baseline CI Run `38703306790` completed successfully in its
  three default jobs. Local and remote `gh-pages` remained at their previously
  observed, differing SHAs; neither was changed.
- A synthetic fixture, loopback server, and controlled Chromium Service Worker
  tested the `2026-07/index.html` navigation path. The worker controlled the
  page under `/bangumi-side-b/`. The quarter state was `COMPLETE`, staging was
  absent, and the content-store page bytes matched the active manifest SHA-256
  and size. With no active quarter metadata or runtime copy, a fast server 503
  remained a 503. With the verified active page present, normal HTTP 200 took
  84.6 ms to visible subject content; two server-side 1.7-second delays took
  1,752.3 ms and 1,769.2 ms; a fast server 503 returned the exact active bytes
  as HTTP 200 in 50.5 ms. Server-side request and response observations
  confirmed each tested navigation reached the controlled loopback handler.
- Outcome: `MEASURABLE_CACHE_WAIT` for this verified active quarter navigation
  through `networkFirst()`. The observed wait is consistent with the existing
  choice to wait for an online response before falling back; the experiment
  used unchanged content and did not measure a freshness benefit or production
  impact. A later valid-hash asset request, visited-only runtime fallback,
  thrown network/offline failure, true timeout, Safari/installed PWA, and
  production conditions were not tested. Do not generalize this result to
  those paths or treat it as authorization to change the PWA contract.
- The existing complete-quarter/offline-navigation and guaranteed-active
  missing-resource/unknown-404 browser tests both passed. The earlier Plan-58
  active/staging and reference-safe-GC protection evidence remains reusable:
  the Service Worker and PWA browser test file are unchanged from that source
  baseline. The synthetic probe and raw measurements remain in the ignored
  local Plan-61 workspace; only this bounded summary is tracked. No runtime,
  PWA contract, cache policy,
  CI, product decision, data, generated site, release, or Pages state changed.
  Production remains the lifecycle stage; no A–F direction is approved.
- Any follow-up that trades online freshness against response latency needs a
  separate scoped Plan and an explicit decision on when verified active content
  may replace a pending network response. Its tests must preserve manifest
  integrity, active/staging and app/data revision boundaries, and reference-safe
  GC. Current source-delivery and remote confirmation evidence belongs in this
  task's TASK_RESULT.

## 2026-10-15 Plan-62 PWA freshness decision evidence

- Plan-62 Rev1 compared the current worker with one disposable isolated
  candidate against source baseline `89d56173a1a64f5de2816ab7712461482067ac0e`.
  In both synthetic loopback sites, the old quarter page was fetched into a
  `COMPLETE` active package, `staging` was null, and its cached bytes matched
  the manifest SHA-256 and size. The server then returned a distinct synthetic
  `NEW` page at the same URL; both navigations were confirmed worker-controlled.
- Current `networkFirst()` showed `NEW` after 122.5 ms for a quick `200`, and
  after 1,751.1 / 1,792.2 ms for two delayed `200` responses. With a fast
  `503`, it returned the verified `OLD` page in 72.8 ms; the no-active cold
  control stayed `503`. A temporary worker copy used a 300 ms experimental
  window only when quarter state was explicitly `COMPLETE`, staging was null,
  and the active page passed content verification. It showed quick `NEW` in
  112.0 ms, but returned `OLD` in 355.0 / 355.6 ms during delayed `200`s.
  Each late `NEW` response reached runtime cache while the active manifest
  stayed unchanged; the open page remained `OLD` without a visible update
  notice or automatic refresh. The candidate also returned verified `OLD` on
  `503` and retained `503` in the no-active cold control.
- Result: `DECISION_REQUIRED`. The synthetic Chromium probe measures a local
  latency/freshness tradeoff only; the window is not an approved product
  threshold. Keep `networkFirst()` unless the user explicitly approves
  displaying a verified older quarter page while a successful online response
  is pending, and defines its visible update behavior in a separate Plan.
  Runtime-only pages, versioned assets, real Safari/installed PWA, production
  traffic, and user devices were not tested. Two directly related existing PWA
  browser regressions passed. Raw evidence remains in the ignored local
  workspace; production code, PWA contracts, data, generated site, release,
  Pages, and `gh-pages` were not changed. Production remains the lifecycle
  stage; A–F remains unapproved.

## 2026-10-13 experience-upgrade research status

- The local report `BGMB-总体体验升级与架构优化研究报告.md` under
  `workspace/reports/ui-upgrade-20261013/` proposes A–F candidates: A browsing
  response/rendering, B first-screen/image delivery, C browsing state/context,
  D local snapshot data access, E personal workspace/exploration, and F
  maintainer update efficiency. These candidates have not become an approved
  overall implementation Plan; their ordering, targets, and product tradeoffs
  remain proposals outside separately approved scopes. Plans 49–52 authorize
  only their bounded changes and do not approve the broader program or move
  the Production lifecycle into a new phase.
- The original research report and `measurements.json` were read; their JSON,
  script headers, and artifact inventory were inspected without copying or
  rerunning those original research scripts. They identify source baseline
  `1864b71b9fc98a930dad3889385e4103774fc532` / app `0.8.5`; the research uses
  local Chromium, simulated CPU/network conditions, and synthetic data.
  Research measurements are not production acceptance, real-device evidence,
  formal INP, or proof of future performance targets. Production and
  real-device research verification were not executed.
- Plan-49 also used a separate private synthetic-site before/after browser
  benchmark: Chromium 149, 390×844 viewport, 4× CPU throttle, 30 distinct ID
  queries for each of Quarter and Archive in both motion preferences. It
  recorded synchronous input-handler timing, next-animation-frame latency,
  DOM mutation/node-move counts, geometry reads, and animation scans. These
  are local lab measurements only, not production INP, online acceptance, or
  real-device evidence; benchmark artifacts remain under the ignored local
  `workspace/reports/ui-upgrade-20261013/plan49/` directory.
- Proposed UX, IndexedDB/data access, framework, and cache changes do not
  supersede `PROJECT_BRIEF.md`, `DECISIONS.md`, `docs/visual-system.md`, or
  `docs/pwa.md`. A later approved Plan must identify any changed contract,
  its owner, permissions, and necessary acceptance. Research artifacts remain
  private local evidence and are not source build inputs.

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
- Existing Git history includes implementation commit
  `8101f69ce5c85b0bd0d13b6f94f4286533f35203`, controlled configuration
  persistence commit `72d78868d5eb5791332f527bc0b6ba5863d1ae36`
  (`data: persist reviewed archive decisions`), and later maintenance commit
  `8bae429a8c1f0d7e621a103a04bec96877dcc01b` containing both implementation
  and configuration changes. Historical local-only configuration statements
  above describe those tasks' observation points, not a permanent prohibition
  on authorized tracked configuration delivery. The tracing semantics are
  maintained in `docs/development.md`.
- On 2026-10-13, read-only remote inspection found `gh-pages` at
  `b35e51ece216e3d8cba8cb3f2a32ec64c887513c`, with message
  `release: 2026.08.28.1 [source 72d78868d5eb]`. Its source identity is the
  configuration persistence HEAD, not a separate implementation SHA. GitHub
  Pages reports branch publication from `gh-pages` at `/`; current source
  workflows run CI and contain no Pages deployment. This inspection does not
  prove current served content or runtime health and does not authorize
  publication of newer source.
- This documentation task does not authorize Bangumi access, data/config
  changes, build, release, or `gh-pages` mutation. Its final source delivery
  SHA and ordinary-push/remote confirmation belong in its TASK_RESULT.
