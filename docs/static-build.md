# Static build

`bgmb build` is a completely offline operation. After reconciling any pending write, its inputs are the SQLite
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
├─ assets/{app.<SHA256>.css,app.<SHA256>.js,pwa.<SHA256>.js}
├─ sw-v2.js
├─ covers/<ID>.webp
├─ covers/previews/<identity>.webp
└─ data/{archive-index.json,catalog/,quarters/,offline-v2/,pwa-shell-v2.json}
```

## Archive initial document

`archive/index.html` initially selects the YEAR tab without inventing a year before
`archive-index.json` arrives. Its same-origin browser shell is present and busy in
the generated HTML; result controls remain inert until the applicable catalog loads.
Scope tabs remain switchable while the index is pending, and range inputs remain
disabled until its years are known. The reserved pending area contains no subject
markup, record keys, media URLs, or focusable controls; runtime catalog data replaces
it in place. Index or catalog errors remain visible as errors, while a successfully
loaded empty catalog uses the existing empty-results state.

Quarter JSON keeps TV premiere, TV continuing, and Movie premiere separate. It
contains the deterministic subject display projection and detail payload; no
Subject detail pages, episode files, role/person artifacts, remote URLs, or
character images are generated. Missing or invalid covers produce a warning and
`cover_url: null` instead of a fake image.

Browse cards use one derived WebP profile: maximum edge 640px, quality 78,
method 6, LANCZOS resampling, EXIF orientation applied, RGB/RGBA with alpha and
ICC preserved. Generation checks the canonical source SHA256/size and the
existing cover byte/dimension limits before decoding. Small (at most 640px),
animated, or non-beneficial inputs retain their original bytes at the preview
path. Canonical covers and JSON `cover_url` remain unchanged; detail and
Lightbox use the original cover.

The preview rule fingerprint includes the profile, fallback/colour/orientation
rules, Pillow version, and libwebp version. Its physical filename identity is
SHA256 of ASCII `<rule-fingerprint>\n<source-content-hash>` (one literal newline).
The full 64-hex identity avoids long nested Windows paths and lets identical
sources share a preview across subjects/quarters. It is a producer identity,
not the preview content hash: artifact state and offline manifests record the
actual output SHA256 and size. HTML carries the rule fingerprint; Archive derives
the same path from the existing original URL without changing fact projections.
Changing this rule changes shared/resource fingerprints and manifest `revision`,
not the quarter JSON's facts `data_revision`.

The site is updated incrementally. `workspace/build-state.json` stores only
deterministic shared, quarter, year, archive, artifact hashes/sizes, and quarter
status. A changed quarter dirties its page/data/offline manifest, the owning year
catalog, and the archive index. When the public quarter list changes, all
eligible quarter pages and their offline manifests refresh so navigation
matches the new list. The quarter JSON stays byte-identical for quarters with
the same facts. A scoped build still updates this shared navigation when the
public list changes. Missing or corrupt state triggers one safe full
convergence. With usable state, the planner uses metadata and cheap `stat` checks;
an identical second build does not read cover bytes, regenerate unchanged HTML or
JSON, compare every output byte, or scan the whole site. The writer stages only
changed files in `workspace/build-staging`, validates newly generated artifacts and
cross-artifact metadata, checks retained artifacts by existence/size, applies
replacements/deletions, and commits build-state last. A failed replace, delete or
validation starts rollback. If a sharing violation blocks restoration, the writer
retains the backup and marks the tree as pending recovery.

An identical second build must leave generated artifact bytes and timestamps
unchanged and report skipped quarters. Unchanged preview artifacts reuse the
recorded hash/size and existence/size checks without reading canonical covers.
Missing or size-invalid previews are regenerated. Stale original and preview
covers are removed only when no current or retained quarter references them.
Retained quarter pages keep their own rule identity and both cover dependencies.
A quarter that returns to the current scope regenerates its page and manifest
against the current asset and preview rules before obsolete previews are removed.
A changed original that conflicts with a retained quarter's physical cover fails
before mutation, preserving the previous site. Newly generated previews must
decode safely, and each offline manifest must include all page preview dependencies
plus all original covers with their actual hashes/sizes.

`sync` invokes the same incremental builder only after a successful facts/covers
commit. Incomplete facts or relevant unresolved REVIEW retain only that quarter's
last-known-good artifacts; they do not freeze healthy quarters. A blocked quarter
that has never been published locally is omitted with a warning. A recorded
quarter with an unverifiable previous tree stops the build before replacement.
Restore a verified copy or rebuild from complete facts. Complete facts with
missing covers may build with warnings. `bgmb release publish` is a later explicit
release operation and never calls `sync` or `build`.

## Artifact planning and validation

The [builder](../src/bgm_side_b/build/site_builder.py) reads facts, chooses the
dirty and retained scopes, and hands an `ArtifactPlan` to the
[writer](../src/bgm_side_b/build/writer.py). [Path rules](../src/bgm_side_b/build/site_paths.py)
define core site files and the page, data and manifest paths for a quarter.
[Manifest generation](../src/bgm_side_b/build/site_manifests.py) uses the planned
file identities. The page and offline manifest both take `data_revision` from
the planned quarter JSON. A healthy no-op leaves both manifests and the Service
Worker on their reuse path.

| Check | Evidence it uses |
| --- | --- |
| Full build | Materialized bytes and references within the generated tree. |
| Dirty artifacts | Newly materialized bytes, planned hashes and sizes for reused dependencies, and the quarter's page and data when its manifest changes. |
| Scoped final check | Generated sources plus the existence and known sizes of the planned files. |
| Retained package | The previous page, JSON, manifest, public indexes and each listed resource against the previous build state and actual bytes. |
| Release candidate | Actual paths, SHA-256 and sizes across the tree. The shell and every indexed quarter must match those bytes, including their HTML and worker bindings. |

Both byte-based build checks verify the quarter data hash and package revision.
They reject missing dependencies, non-local resource paths, and mismatched
hashes or sizes. Shell validation also checks the ordered resource list and the
revision derived from it. Build validation and test entry points are in
[site validation](../src/bgm_side_b/build/site_validation.py) and
[builder integration tests](../tests/test_site_builder.py).

## Shared resources across builds

Root, Archive and Settings use the current CSS, application script and PWA script.
Each filename contains the SHA-256 of its bytes. An offline quarter records the
exact three paths used in that quarter's HTML. Identical bytes for a given asset
reuse one file. The writer retains paths used by the current shell, current
quarters or verified retained quarters. When the last reference disappears, the
ordinary recoverable patch removes the old file. The quarter data and cover URL
contracts keep their revision query parameters. A shared original cover at a
conflicting physical path still causes the patch to stop.

A blocked quarter is eligible for retention only while its actual files match
its v2 manifest and build state. A healthy build with no retained work and no
changes preserves the metadata-only fast path. A retained build reads only the
covered dependencies with shared hashes checked once per retention pass. Whole
tree validation is performed for a full materialized build or release candidate.

0.10.0 can rebuild a complete v1 tree from local facts through the ordinary
writer. A recorded blocked quarter with only a v1 package requires a verified v2
backup or complete facts. The build keeps the existing tree in place and reports
the affected quarter if it cannot verify a retained package. See the
[upgrade steps](项目源文件/RUNBOOK.md#离线协议-v2-升级).

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

## Interrupted builds

Before planning, the builder reconciles a pending patch against the old and
intended build state. Backups cover touched files, including a record of paths
that did not previously exist. The preview server can serve different generations
across requests while a patch is in progress. Full candidate validation checks
for a pending patch and verifies the paths, sizes and SHA-256 of the site.
See the [runbook](项目源文件/RUNBOOK.md) for lock conflicts and recovery.
