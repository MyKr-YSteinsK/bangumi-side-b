# Bangumi Side B by MyKr

Bangumi Side B is a local-first static archive for one deliberately narrow
release: Japan TV animation first airing in **2026-04**. It synchronises
verified Bangumi facts to SQLite and builds static local and Pages/PWA output
without runtime database or API access.

## Current scope

- only TV subjects whose complete first-air date is in 2026-04;
- deterministic automatic Japan-TV classification: structured Infobox evidence
  first, then exact configured tags, then the narrow quarterly-TV default when
  no region evidence exists (consistent co-productions are allowed);
- no movies, continuations, roles, people, character images, or voice-actor
  images;
- only subject covers for admitted subjects.

```powershell
python -m bgm_side_b --help
bgmb sync 2026 4
bgmb audit
bgmb build 2026 4
bgmb build --all
bgmb publish --dry-run
```

`sync`, `build`, and `publish` are independent. `build --all` means every
configured release quarter, currently only `2026-04`; it does not expand to
old data stored in SQLite. `publish` consumes an existing verified Pages build
and never invokes sync or build.

If discovery finds candidates but automatic classification admits none, `sync`
fails without creating a new data generation. A build or publish dry run also
refuses an empty release, preserving the last valid output.

## Output and PWA

`build` writes `dist/local/` and `dist/pages/` from one data model, generator,
template system, CSS, and native JavaScript source. Both profiles include
cards, drawers, details, main-story episodes, and subject covers. Neither
queries or emits roles, voice actors, or character media. Pages derives bounded
WebP covers and uses complete verified PWA snapshots; ordinary startup never
checks for updates.

Runtime pages do not read SQLite, request Bangumi data, or require a web
backend. A failed build keeps the prior complete static output.

## First-release readiness

After a reviewed `main` is manually pushed by the operator, move any old
`workspace/` and `dist/` out of the repository using the
[safe data-reset procedure](docs/data-reset.md). Then run:

```powershell
bgmb sync --progress plain 2026 4
bgmb audit
bgmb build --progress plain --all
bgmb publish --progress plain --dry-run
```

The dry run is not a publication. Review its report and the generated output
before separately authorising a real Pages release.

## Development

Requires Python 3.11 or later.

```powershell
python -m pip install -e ".[dev]"
python -m pytest tests -q
python -m ruff check .
```

Tracked files are source, configuration, templates, static assets, tests, and
documentation. SQLite databases, downloaded covers, reports, backups,
generated output, caches, temporary files, and secrets are not committed.

See [sync notes](docs/subject-sync.md), [country filter](docs/country-filter.md),
[static build notes](docs/static-build.md), [safe data reset](docs/data-reset.md),
and [PWA notes](docs/pwa.md).
