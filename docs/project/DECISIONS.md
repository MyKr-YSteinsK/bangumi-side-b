# Durable Decisions

This file records durable product and repository decisions, not a changelog or
an implementation map. Specialist contracts own their detailed procedures.

## D-001 — Narrow first-version archive scope

The product covers Japanese Anime TV and theatrical movies. TV premiere and
continuing appearances are distinct; movies do not gain continuing entries.
This keeps admission, quarter projection, and public browsing explainable.
Older plans or docs that describe WEB/OVA/OAD, roles, people, or a single-TV-
only quarter are historical and do not expand the current scope.

## D-002 — Deterministic facts with explicit human adjudication

Rules and structured evidence decide automatic admission and generated facts.
AI cannot fill missing country, quarter, continuation, source, tag, blacklist,
or other archive facts. Evidence gaps follow the issue taxonomy in D-012 rather
than a universal REVIEW shortcut; factual conflicts remain REVIEW until an
explicit human decision is recorded in the supported configuration.

## D-003 — Canonical detail after candidate discovery

Browse and Search are discovery evidence only. Persisted title, dates, format,
episode count, tags, source, and image facts must be confirmed by canonical
subject detail and the relevant deterministic rules.

## D-004 — Appearance-level quarter model

The archive stores quarter appearances rather than assuming one subject belongs
to one quarter. A TV subject can retain a premiere and later continuing
appearances; a movie has one premiere appearance. This supersedes the old
single-quarter wording in the retired aggregate baseline.

## D-005 — One reproducible static site

SQLite and verified local covers are inputs; `dist/site` is the only formal
generated site. Build is offline and incremental, while serve is only an HTTP
view of that tree. Runtime code never needs SQLite or a business-data backend.

## D-006 — PWA extends the online site

The PWA is an optional layer over the same online static site. It uses a
minimal shell, visited-resource runtime caching, and explicit complete-quarter
offline downloads with verified replacement. It is not a monolithic archive
snapshot and does not block normal online startup.

## D-007 — Current mobile workspace supersedes the old rail

The accepted mobile implementation is full-screen/full-width single-column
detail with no narrow context rail. Mobile filters are draft/apply and cancel
unapplied drafts on close/back. This explicitly supersedes the older baseline
rail and realtime-filter wording; the current implementation and user guide
are the behavioral authorities.

## D-008 — Product identity is `Side B`

The PWA short name is `Side B`. The old `BGM B` baseline value is superseded
and must not be reintroduced as a current identity requirement.

## D-009 — Local motion foundation

Browsing continuity uses local result motion and reduced-motion handling. Root
or cross-document View Transition is not the default foundation. Historical
 real iOS Safari and standalone PWA testing exposed a full-screen black flash failure
mode, so feature availability alone is not a reason to restore it. Reconsider
only when new real-device evidence shows that failure mode is gone and the
benefit justifies the added risk; an old Plan number is not a standing
requirement owner.

## D-010 — Separate application and publication identities

Application SemVer comes from the source version contract and concrete
changelog entries. Pages batch identity comes from the release commit on
`gh-pages`. A source push is not a Pages publication, and publish must use the
explicit prepared release workflow.

## D-011 — Historical evidence is not active ownership

The migration package, old Plans/Handoffs, old context exports, and retired
aggregate documents may explain history but cannot override the repository's
Project State or specialist owners. The foreign old Codex context export is
quarantined as RED:REPEAT material and is never Bangumi evidence.

## D-012 — Information gaps and factual conflicts stay distinct

Missing or insufficient information is not one universal disposition. Specific
missing evidence, such as country evidence, follows its specialist contract
and can enter REVIEW. The current rule-bound `information-insufficient` issue
family may instead trigger an automatic exclusion cache; a `factual conflict`
continues as REVIEW and requires human adjudication. Manual exclusion uses
`excluded_subject_ids`; automatic exclusion uses `auto_excluded_subject_ids`
and is re-evaluable when a subject is rediscovered in an actively refreshed
quarter. They are distinct states. This prevents a
deterministic information-quality rule from being silently broadened into a
conflict classifier or vice versa.

## D-013 — Independent repository boundary

Bangumi Side B remains an independent repository and is not folded back into
MyKr-ops. The project does not pre-build a generic plugin or integration
framework. A future integration requires an explicit user decision and a
deliberately defined package/API boundary; proximity of repositories alone is
not authorization or a design.

## D-014 — Application updates and quarter-data updates are different

Application/package updates change the shell, CSS, JavaScript, or lifecycle and
use the application update notice plus explicit user refresh. Quarter-data
updates change structured archive data and follow the explicit complete-quarter
download/replacement lifecycle. A package update must not be presented as a
quarter-data update, and automatic maintenance must not expand the user's
offline quarter scope without an explicit download choice.

## D-015 — Risk-tiered CI is intentional

The default CI remains a fast gate for ordinary pushes. Expensive Chromium or
WebKit full regression belongs to the manual/deep layer because its reliability
and cost profile are different. This is an intentional risk/cost split, not a
missing CI capability; neither literal “no GitHub Actions” wording nor
every-push expensive full regression supersedes it.

## D-016 — Formal Plan source delivery follows validation

When a formal approved Plan produces tracked source changes, its validated
source delivery ends with one ordinary push to the current configured
upstream. This keeps a completion report aligned with actual remote delivery
instead of leaving a local `COMPLETE` state that the upstream does not have.
Source push and Pages publication remain independent events; a push failure is
a delivery failure and cannot be made to pass through force or history rewrite.
Plans with no tracked source changes do not create an empty commit merely to
perform a push.

## D-017 — Natural quarter ownership and bounded TV boundary exception

Premiere ownership defaults to the natural calendar quarter of the canonical
air date. Movies remain strict natural-quarter premieres. A TV premiere dated
1–7 days before the next quarter can move into that next quarter only when
trusted planned-count evidence rules out a 1–2 episode short run and bounded
main-story episode dates prove a regular multi-week run crossing the boundary,
with strong target-quarter evidence and no structured conflict. Community
quarter tags are supporting evidence, not a standalone override; title season
words are never evidence. Unknown continuity stays REVIEW, and
`TV_QUARTER_BOUNDARY` is excluded from the automatic-exclusion
allowlist. Manual quarter assignment remains the supported adjudication path.

## D-018 — Quarter relevance precedes Japanese review and keeps scope decisions separate

Canonical quarter relevance is resolved before asking an unresolved candidate
for Japanese adjudication. Movie search lookback is never a second Movie
quarter; only TV may use the bounded prior-quarter boundary rule. Exact
structured country sources may accept compatible co-production, but an
independent Japan/non-Japan disagreement remains REVIEW. When reliable age and
rating evidence makes both Japanese branches ineligible, synchronization may
record an auditable `outcome_dominated_low_rating` exclusion without changing
the underlying Japanese classification. Any remaining inclusion-changing
ambiguity is handled only through the separate
`config/japanese-overrides.toml` and `bgmb classify` commands; it never changes
media or quarter facts.

## D-019 — Automatic exclusions are re-evaluable cached decisions

Manual `excluded_subject_ids` entries are durable explicit operator decisions.
`auto_excluded_subject_ids` records the current deterministic exclusion result,
not an irreversible fact: when an ID is rediscovered in a quarter that is being
actively synchronized, fresh canonical evidence is evaluated with the old
automatic ID temporarily removed from the admission input. Reliable rating
growth or improved evidence can restore an otherwise admissible subject;
scope rules, manual exclusions, and inclusion-changing REVIEW remain binding.
AI-assisted external research remains an operator workflow and is never runtime
evidence. For a formal Plan authorized to use live data, the final bounded live
validation, review, audit, and relevant regression must finish before the formal
source delivery push.

## D-020 — Split theatrical subjects remain separate from the WEB aggregate

The fresh identity check for Bangumi subjects `604330` and `604331` confirms
that they are the two independently released theatrical Movie parts of
`藤本タツキ17-26`, released on the same date. The official announcement
describes the eight works as a Part-1 / Part-2 theatrical split, and the
publisher release records the same limited theatrical arrangement:
[official announcement](https://fujimototatsuki17-26.com/news/detail/?id=1127948),
[publisher release](https://avex-pictures.co.jp/topic/77380/). Their own
canonical rating totals remain below the automatic low-rating threshold, so
they stay excluded on their own evidence. Subject `582501` is a separate WEB
aggregate and is outside the first-version theatrical Movie scope; its rating
must not be borrowed to restore or replace either theatrical subject. This is
an identity decision, not an alias model.

## D-021 — Exact special-venue evidence limits theatrical Movie admission

Canonical `platform=剧场版` is a Bangumi media category, not sufficient proof
of ordinary theatrical exhibition. Exact Infobox `其他` values
`游乐设施电影` and `プラネタリウム上映作品` are deterministic hard
rejections from the first-version theatrical Movie scope. Titles, summaries,
official-site domains, and URL paths are not runtime evidence; a verified
isolated case without reusable structured evidence uses a manual subject
exclusion instead of a heuristic or a new media-override family. Exact public
region token `法国` is accepted as negative country evidence, while broad
`欧美` remains unresolved and cannot cancel precise Japanese evidence. When a
fresh canonical evaluation turns an old automatic exclusion into a hard
non-Japanese or special-venue rejection, synchronization transactionally
removes the stale automatic entry and reports it as `auto_reconciled` rather
than retaining a misleading low-rating status.
