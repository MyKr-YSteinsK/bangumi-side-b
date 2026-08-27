# Project Brief

## Purpose

Bangumi Side B is a local-first Japanese anime broadcast archive. It turns
verified Bangumi facts into a reproducible static site that can be browsed
online and extended with explicit quarter-level offline downloads.

## Product scope

- Japanese Anime subjects (`type == 2`) in the TV and theatrical movie formats.
- TV appearances distinguish `premiere` from later `continuing` appearances;
  movies have `premiere` appearances only.
- Managed archive facts cover the verified quarters represented by the local
  fact store. Missing data is omitted or reported, never invented.
- The first version does not include WEB, OVA, OAD, episodes as separate
  products, characters, staff, voice actors, or their images.

## Architecture boundary

- SQLite facts, verified local covers, tracked configuration, and checked-in
  static assets are canonical inputs.
- `build` is offline and produces the one formal generated output,
  `dist/site`. Build state, reports, and release preparation state are derived
  local state and can be regenerated.
- Runtime pages are static HTML, CSS, and native JavaScript. They read only
  same-origin generated resources; they do not read SQLite or request Bangumi
  or third-party business data.
- PWA support extends the same online site with a minimal shell, runtime cache,
  and explicit complete-quarter downloads. Normal online startup is not gated
  on downloading the archive.

## Invariants

- Admission and archive facts use deterministic evidence and rules. AI does
  not decide quarter ownership, continuation, format, source, tags, blacklist,
  or generated facts. Evidence gaps have no single disposition: specific facts
  such as country evidence follow their specialist contract and may enter
  REVIEW, while the current rule-bound `information-insufficient` issue family
  may trigger an automatic exclusion cache that is re-evaluated when a subject
  is rediscovered in an actively refreshed quarter. A `factual conflict`
  remains REVIEW for explicit human adjudication; manual exclusion and
  automatic exclusion are distinct states.
- Browse/Search is candidate discovery. Canonical subject detail is the
  authority for persisted subject facts.
- Source vocabulary and display tags use exact configured mappings and
  allowlists; no fuzzy, alias, or AI matching is allowed.
- SQLite uses the strict `bangumi-side-b-archive` family and schema version 2;
  unknown or newer schemas fail closed.
- Successful facts/covers sync may build the affected scope. Build remains
  offline, serve reads the existing tree, and publish never calls sync/build.
- Application version, source commit, Pages batch identity, and Pages
  publication are separate release dimensions.

## Accepted UX baselines

- Mobile detail is a full-screen/full-width single-column workspace with no
  narrow context rail. The background list remains stable while detail is open.
- Mobile filtering uses draft/apply semantics: edits stay in a draft until the
  user applies them; close/back cancels unapplied changes.
- Quarter mobile browsing is continuous; Archive browsing remains paginated.
- The PWA short name is `Side B`.
- Motion is local to the affected result elements. Root or cross-document View
  Transition is not the default motion foundation.

## Non-goals

No product redesign, new media/domain scope, AI fact inference, schema
migration chain, speculative integration, second site output, destructive data
cleanup, live sync during ordinary development, or implicit Pages publication.

Detailed operational, domain, build, PWA, release, and visual rules remain with
the specialist owners in `DOC_OWNERSHIP.md`; this brief is not a replacement
for those contracts.
