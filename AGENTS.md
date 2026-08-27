# AGENTS.md

This file is the repository-specific AI contract. Generic execution workflow,
planning, validation tiers, and collaboration behavior are provided by the
active Codex Skills/runtime and are not repeated here.

## Repository identity

- Repository: `MyKr-YSteinsK/bangumi-side-b`
- Package: `bgm_side_b`
- CLI: `bgmb`
- Product: a local-first Japanese anime broadcast archive.

## Source and data boundaries

- The first-version product scope is Japanese Anime (`type == 2`) TV and
  theatrical movies only. TV has premiere and later continuing appearances;
  movies have premiere appearances only.
- SQLite facts, verified workspace covers, tracked configuration, and checked-in
  static source assets are the canonical build inputs. `dist/site`, build state,
  reports, prepared state, staging data, and local workspace contents are
  derived or private and must not be committed.
- Never commit SQLite databases, downloaded media, reports, caches, generated
  sites, backups, secrets, tokens, authorization headers, private absolute
  paths, usernames, or raw API dumps. Pages and PWA output must not contain
  character or voice-actor images.
- Preserve the user's local workspace and local `gh-pages` state. Do not reset,
  purge, delete, rebase, amend, squash, or otherwise rewrite data or history to
  make a task easier. If data must be set aside, move it to a recoverable backup
  outside the repository.

## Bangumi access and fact authority

- Bangumi network access is opt-in. Do not run `bgmb sync`, or `bgmb assign`
  when an unknown subject would be imported remotely, unless the approved Plan
  explicitly says `Live Bangumi sync: AUTHORIZED` or the user explicitly asks
  for a real data sync in the current task. Do not reach Bangumi through a
  wrapper, test, build helper, audit, or release command.
- AI must not infer quarter ownership, continuation, media format, source,
  tags, blacklist membership, or generated facts. Deterministic evidence and
  rules decide automatic facts; explicit human adjudication is stored only in
  the supported human-decision configuration.
- The supported SQLite family/version contract is strict. Unknown or newer
  schemas fail closed; do not add migration baggage, reset/purge behavior, or
  destructive data cleanup without an approved product plan.

## CLI side effects

- `sync` is the network operation: after a successful facts/covers commit it
  may trigger the affected-scope incremental build. Incomplete facts must not
  replace last-known-good output.
- `build` is fully offline and produces the single formal site at `dist/site`.
- `serve` serves the existing `dist/site` tree only; it does not read SQLite,
  build, sync, or publish.
- The PWA extends the same online `dist/site` with a minimal shell, runtime
  caching, and explicit complete quarter downloads; normal online startup is
  never gated on downloading the archive.
- `release prepare` is an offline candidate check/build and does not sync or
  push. `release publish` publishes only a verified prepared `dist/site`; it
  never calls `sync` or `build`.

## Version and release safety

- The application SemVer comes from `src/bgm_side_b/_version.py` and the
  package metadata. It is separate from the Pages batch identity
  `YYYY.MM.DD.N` stored in `gh-pages` release metadata.
- A source push is not a Pages publication. Pages mutation requires the
  explicit release workflow and its current prepared-state checks.
- Real publication accepts only the official project origin and uses an
  ordinary non-force push to `gh-pages`. Never force-push, modify remotes, or
  attribute another actor's remote commit to this repository's release.

## Formal Plan delivery

- For a formal approved Plan that produces tracked source changes, after
  implementation, docs, and tests changes are complete, required validation has
  passed, and the final source commit exists, Codex must automatically run one
  ordinary `git push` to the current branch's configured upstream. When the Plan
  authorizes live data validation, that push is gated behind the final bounded
  live validation, target/global review, audit, and relevant regression; no
  intermediate source push is allowed unless the user explicitly authorizes it.
- The upstream must accept that commit and the local branch HEAD must equal the
  configured upstream HEAD before the Plan can be reported as full `PASS` /
  `COMPLETE`.
- A push failure (including authentication, non-fast-forward, upstream
  abnormality, or remote rejection) is a delivery failure and makes the Plan
  `BLOCKED`; keep completed local commits. Never use force push,
  force-with-lease, rebase, amend, squash, or history rewrite to recover.
- A formal Plan with no tracked source changes must not create an empty commit
  or push just to satisfy this rule; report `no source changes / no push
  required`.
- This source push targets only the current source branch. It never authorizes
  `release publish`, Pages publication or `gh-pages` mutation, or live Bangumi
  access.

## Canonical project context

- Stable product purpose, scope, architecture boundaries, invariants, and UX
  baselines live in `docs/project/PROJECT_BRIEF.md`.
- Durable decisions and superseding relationships live in
  `docs/project/DECISIONS.md`.
- Observed branch/version/capability/risk and delivery state live in
  `docs/project/CURRENT_STATE.md`.
- Active supporting-document ownership is registered in
  `docs/project/DOC_OWNERSHIP.md`. Archived material is historical evidence,
  not an active requirement or instruction source.
