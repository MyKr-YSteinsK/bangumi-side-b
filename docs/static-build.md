# Static build

`bgmb build` is a completely offline operation. It reads only the clean SQLite
fact store, `workspace/covers`, configuration, and checked-in static source files.
It never calls Bangumi and never publishes.

```powershell
bgmb build 2026 7
bgmb build --all
```

The only formal output is `dist/site/`:

```text
dist/site/
├─ index.html
├─ YYYY-MM/index.html
├─ archive/index.html
├─ settings/index.html
├─ assets/app.css
├─ assets/app.js
├─ covers/<ID>.webp
└─ data/{archive-index.json,catalog/,quarters/,offline/}
```

Quarter JSON keeps TV premiere, TV continuing, and Movie premiere separate. It
contains the deterministic subject display projection and detail payload; no
Subject detail pages, episode files, role/person artifacts, remote URLs, or
character images are generated. Missing or invalid covers produce a warning and
`cover_url: null` instead of a fake image.

The site is updated incrementally. `workspace/build-state.json` stores only
deterministic shared, quarter, year, archive, artifact hashes/sizes, and quarter
status. A changed quarter dirties its page/data/offline manifest, the owning year
catalog, and the archive index. Missing or corrupt state triggers one safe full
convergence. With usable state, the planner uses metadata and cheap `stat` checks;
an identical second build does not read cover bytes, regenerate unchanged HTML or
JSON, compare every output byte, or scan the whole site. The writer stages only
changed files in `workspace/build-staging`, validates newly generated artifacts and
cross-artifact metadata, checks retained artifacts by existence/size, applies
replacements/deletions, and commits build-state last. A failed replace, delete, or
validation restores the touched files and leaves the previous state intact.

An identical second build must leave generated artifact bytes and timestamps
unchanged and report skipped quarters. Stale generated covers are removed only
when no current quarter references them.

`sync` invokes the same incremental builder only after a successful facts/covers
commit. Incomplete facts or relevant unresolved REVIEW retain only that quarter's
last-known-good artifacts; they do not freeze healthy quarters. A blocked quarter
without a usable previous tree is omitted with a warning. Complete facts with
missing covers may build with warnings. `bgmb release publish` is a later explicit
release operation and never calls `sync` or `build`.

## Local preview

```powershell
bgmb serve --port 8000
```

The stdlib server binds to `127.0.0.1`, serves only `dist/site`, and exposes the
same project prefix used by GitHub Pages:

```text
http://127.0.0.1:8000/bangumi-side-b/
```

It does not read SQLite, build, sync, publish, or mutate files. A used port is a
clear error; it is never silently replaced with another port.
