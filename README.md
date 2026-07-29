# Bangumi Side B by MyKr

Bangumi Side B is a local-first archive of Japanese TV animation and theatrical
movies. It will synchronise selected Bangumi facts into SQLite, then produce
static sites from that local data. The project is independent of MyKr-ops.

## First-version scope

The first version will cover only TV animation and theatrical movies. WEB,
OVA/OAD, specials, characters, voice actors, images, site generation, PWA, and
publishing are not implemented by this initial project phase.

## Architecture

```text
Bangumi API -> deterministic local normalisation -> SQLite -> static output
```

The future runtime site will use only static HTML, CSS, and native JavaScript;
it will not read SQLite or call the Bangumi API.

## Current status

This repository currently provides the installable Python package and the
`bgmb` command shell. Synchronisation is planned but is not implemented yet.

```powershell
python -m bgm_side_b --help
bgmb --help
bgmb --version
```

Planned command surface:

```text
bgmb sync YEAR QUARTER_MONTH
bgmb sync YEAR
bgmb sync START-END
```

`sync`, `build`, and `publish` will remain separate commands.

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
