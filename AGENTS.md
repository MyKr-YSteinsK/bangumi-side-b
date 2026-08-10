# AGENTS.md

## Project

- Repository: `MyKr-YSteinsK/bangumi-side-b`
- Local root: `D:\CS\bangumi-side-b`
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
4. create one focused commit;
5. continue without waiting for routine confirmation.

Each completed Phase must only create its focused commit; do not push at a Phase boundary.

After the entire Plan is complete and integrated validation passes, Codex must run one ordinary `git push` to the current branch's configured upstream. If push is rejected, authentication fails, a non-fast-forward occurs, or the upstream is abnormal, mark the Plan `BLOCKED` and do not report `PASS`.

Never force-push or force-with-lease, modify remotes, rebase, amend, squash, delete, or rewrite already-pushed history.

## Scope

Do not modify MyKr-ops. Do not add speculative integration, plugin systems, accounts, community features, webpage editing, unrelated refactors, dependency upgrades, or full-repository formatting.

Do not create empty future services, fake interfaces, or unusable UI.

## Product constraints

- First version includes only TV and theatrical movies.
- AI must not decide quarter ownership, continuation, format, source, tags, blacklist, or generated facts.
- `sync`, `build`, and `publish` remain independent.
- Runtime pages use static HTML, CSS, and native JavaScript.
- Runtime pages do not read SQLite or request Bangumi data.
- Local and Pages builds share one data model, generator, template system, and frontend source.
- Pages/PWA must not publish character images.
- Voice-actor images are not stored.
- Build must work offline.
- Missing data is omitted or reported, never invented.
- Blacklisted subjects are physically removed within cleanup scope; shared entities are removed only when orphaned.

## PWA and release invariants

- Pages PWA uses only complete, verified snapshots; normal startup never checks for updates.
- Keep the previous active snapshot until a replacement has completely verified and activated.
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

`dist/pages` belongs on `gh-pages`, not `main`.

## Engineering

Prefer the standard library and small justified dependencies. Avoid ORMs, Web frameworks, task queues, DI frameworks, large logging systems, React, Vue, Node frontend tooling, SQLite WASM, and runtime business IndexedDB.

Use numbered transactional SQLite migrations with backup and rollback.

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
