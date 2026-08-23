# AGENTS.md

## Project

- Repository: `MyKr-YSteinsK/bangumi-side-b`
- Local root: repository root
- Package: `bgm_side_b`
- CLI: `bgmb`

## Workflow

Use the `frugal-dev-runner` skill for every development task.

Before changing product behavior, read:

- the current approved Plan;
- `docs/project-requirements-baseline.md`.

Implement only the current Plan.

Each Phase must:

1. finish independently;
2. run focused validation;
3. review `git diff` and `git status`;
4. Each implementation Phase with tracked changes must create one focused commit;
5. continue without waiting for routine confirmation.

Before each Phase commit, record the commit's `Version impact` as exactly one of
`none`, `patch`, `minor`, or `major`. A `patch`, `minor`, or `major` commit is
version-bearing and must include the product change, `src/bgm_side_b/_version.py`,
the matching concrete `CHANGELOG.md` release entry, and its required tests in
the same commit. Pure tests, documentation, CI, or non-user-visible maintenance
use `none` and do not bump the version.

Version numbers belong to the commit's actual product impact. Do not create a
mechanical version bump merely because a Plan is ending or a publish is about to
run. One Plan may contain multiple concrete application versions; push them once
at the Plan boundary after integrated validation.

Each completed Phase must only create its focused commit; do not push at a Phase boundary.

After the entire Plan is complete and integrated validation passes, Codex must run one ordinary `git push` to the current branch's configured upstream. If push is rejected, authentication fails, a non-fast-forward occurs, or the upstream is abnormal, mark the Plan `BLOCKED` and do not report `PASS`.

Never force-push or force-with-lease, modify remotes, rebase, amend, squash, delete, or rewrite already-pushed history.

## Scope

Do not modify MyKr-ops. Do not add speculative integration, plugin systems, accounts, community features, webpage editing, unrelated refactors, dependency upgrades, or full-repository formatting.

Do not create empty future services, fake interfaces, or unusable UI.

## Product constraints

- First version includes only TV and theatrical movies.
- Managed archive facts currently cover the verified quarters present in SQLite,
  including TV premiere/continuing appearances and movie premiere appearances.
- AI must not decide quarter ownership, continuation, format, source, tags, blacklist, or generated facts.
- `sync` commits facts/covers and then triggers an affected-scope incremental build;
  `build` remains fully offline, and `publish` never calls either command.
- Runtime pages use static HTML, CSS, and native JavaScript.
- Runtime pages do not read SQLite or request Bangumi data.
- The only formal generated site is `dist/site`; localhost preview is an HTTP view
  of that same tree, not a second product output.
- Build reads only SQLite, verified workspace covers, config, and source assets.
- `workspace/build-state.json` and build reports are derived, ignored state; the
  site must be reproducible after deleting `dist/site`.
- Pages/PWA must not publish character images.
- Voice-actor images are not stored.
- Build must work offline.
- Missing data is omitted or reported, never invented.
- Blacklisted subjects are physically removed within cleanup scope; shared entities are removed only when orphaned.

## PWA and release invariants

- Pages PWA extends the same online `dist/site` with a minimal precached shell,
  runtime caching for visited resources, and explicit complete quarter downloads.
- Normal online startup is never gated on downloading archive data. Offline
  quarter replacement uses its manifest's hash/size metadata and keeps verified
  completed resources until the replacement is complete.
- Application updates use a thin nonblocking notice and require an explicit user
  refresh; never surprise-reload the page.
- Pages never publishes character images; `publish` never calls `sync` or `build`.
- A successful Plan push only pushes the current development branch source; it never publishes `gh-pages`. Pages publishing remains restricted to an explicit release/publish workflow.

## Repository boundaries

Track source, config, templates, static source assets, tests, docs, and project metadata.

Never commit:

- SQLite databases;
- downloaded covers or character images;
- reports, backups, generated sites, caches, or temporary files;
- secrets, tokens, authorization headers, `.env`;
- private absolute paths, local usernames, full API dumps, or raw stack traces.

`dist/site` is a local derived artifact and is never committed to `main`.

## Engineering

Prefer the standard library and small justified dependencies. Avoid ORMs, Web frameworks, task queues, DI frameworks, large logging systems, React, Vue, Node frontend tooling, SQLite WASM, and runtime business IndexedDB.

Keep the clean supported SQLite schema contract strict. Unknown or old development
schemas are rejected; do not add migration baggage until a released fact store
requires a real upgrade path.

Keep tests compact and risk-focused. Never claim a command or test passed unless it was executed.

Long-running CLI operations must use the unified ProgressReporter for stages, counters, retries, and heartbeat; business layers must not scatter print calls.

## Completion report

At the end of a Plan report:

- completed Phases;
- commit hashes and messages;
- key changes;
- commands and tests actually run;
- results and unresolved risks;
- push branch and upstream;
- push result;
- whether local and remote are synchronized.

Only report `PASS` after the required push succeeds. A failed or abnormal push requires a `BLOCKED` Plan report.
