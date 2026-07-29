# Bangumi Side B by MyKr

Bangumi Side B is a local-first archive of Japanese TV animation and theatrical
movies. It will synchronise selected Bangumi facts into SQLite, then produce
static sites from that local data. The project is independent of MyKr-ops.

## First-version scope

The first version covers only TV animation and theatrical movies. WEB,
OVA/OAD, specials, STAFF, PWA, and publishing remain out of scope.

## Architecture

```text
Bangumi API -> deterministic local normalisation -> SQLite -> static output
```

The future runtime site will use only static HTML, CSS, and native JavaScript;
it will not read SQLite or call the Bangumi API.

## Current status

The installable package synchronises subject facts, main-story episodes, TV
continuation evidence, configured main characters, all of their listed voice
actors, and verified cover/character images into local SQLite and workspace
media. It writes local sync and tag-audit reports, then builds an offline
local and Pages static archive from that SQLite data. It does not publish a site.

```powershell
python -m bgm_side_b --help
bgmb --help
bgmb --version
bgmb sync 2022 1
bgmb build 2022 1
bgmb build --all
```

Available sync scopes:

```text
bgmb sync YEAR QUARTER_MONTH
bgmb sync YEAR
bgmb sync START-END
bgmb sync YEAR QUARTER_MONTH --force
bgmb sync YEAR QUARTER_MONTH --force-images
```

`--force` refreshes non-image structured facts. `--force-images` independently
revalidates and redownloads verified cover and main-character images. Neither
option builds or publishes output. Images are stored only below
`workspace/media/`; the SQLite record keeps a workspace-relative path, verified
format, hash, size, and dimensions. Voice-actor images are never downloaded.

`sync`, `build`, and `publish` will remain separate commands.

`build` is offline and defaults to `dist/local/` plus `dist/pages/`. Both
profiles share data projection, Jinja templates, CSS, and native JavaScript.
Local output includes verified main-character images; Pages output produces
WebP covers and never publishes character images. The homepage works directly
through `file://`; runtime pages do not read SQLite, request JSON, or contact
Bangumi. See [static build notes](docs/static-build.md) and the
[visual system](docs/visual-system.md).

See [the sync notes](docs/subject-sync.md) and
[API field notes](docs/api-field-notes.md) for the verified fields, incremental
strategy, media-cache rules, and SQLite schema outline.

## Development

Requires Python 3.11 or later.

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

## Git boundaries

Source, configuration, templates, static source assets, tests, and project
documentation are tracked. Local databases, downloaded media, reports,
backups, generated sites, caches, temporary files, secrets, and environment
files are not committed. `dist/pages` belongs on the `gh-pages` branch, not
`main`.

## Third-party content

The MIT license applies to this source code only. It does not grant rights to
Bangumi data, covers, character images, or other third-party content.
