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
or other archive facts. Conflicts and insufficient evidence remain REVIEW until
an explicit human decision is recorded in the supported configuration.

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
or cross-document View Transition is not the default foundation; an old Plan
number is not a standing requirement owner.

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
