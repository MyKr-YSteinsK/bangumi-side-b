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

Never run `git push`. Do not amend, squash, rebase, delete, or rewrite existing user commits unless explicitly requested.

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

## Completion report

At the end of a Plan report:

- completed Phases;
- commit hashes and messages;
- key changes;
- commands and tests actually run;
- results and unresolved risks;
- explicit confirmation that no push occurred.
