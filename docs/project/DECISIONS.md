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
family may instead trigger `automatic permanent exclusion`; a `factual
conflict` continues as REVIEW and requires human adjudication. Manual exclusion
uses `excluded_subject_ids`; automatic permanent exclusion uses
`auto_excluded_subject_ids`. They are distinct states. This prevents a
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
