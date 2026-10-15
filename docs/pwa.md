# Pages PWA contract

This document defines the active product contract for the Pages PWA. The PWA is
part of the unified static site and is implemented by the current generated
`dist/site` runtime.

## One online site

GitHub Pages is the directly browsable production site. PWA support adds
installation, caching, and offline capability to the same generated `dist/site`
tree; it does not create a second site or a monolithic history snapshot.

Runtime pages use only same-origin generated HTML, CSS, JavaScript, JSON, covers,
icons, and manifests. They do not read SQLite, call the Bangumi API, or request a
third-party data API. TV premiere, TV continuing, and Movie premiere appearances
use the same quarter payloads as the online site.

## Shell and runtime cache

The default precache is the minimum application shell:

- root, Archive, and Settings shells;
- shared CSS and JavaScript;
- the web app manifest and required icons;
- only the minimum navigation data required to enter the site.

It must not precache every year catalog, quarter JSON file, cover, or historical
quarter. While browsing online, the runtime cache may retain visited quarter
pages, quarter JSON, year catalogs, covers, and the archive index. Online use is
available immediately and is never gated on downloading a complete archive.

## Quarter offline unit

`data/offline/YYYY-MM.json` is the only formal resource manifest for one offline
quarter. A quarter download contains the quarter page, quarter detail JSON,
quarter-owned covers, shared shell dependencies, and the manifest itself. Movie
and continuing records are included whenever they are present in that quarter's
payload.

Settings may create queues for the current quarter, one year, a year range, or
all available quarters. These choices only expand to a newest-to-oldest quarter
queue; one quarter remains the indivisible download and removal unit.

Resume and retry use the manifest's per-resource hash and size metadata. The
runtime supports pause, continue, cancel, reopen resume, and online resume, with
retry intervals of 1, 3, and 10 seconds. Completed resources survive a partial
failure and the quarter is visibly marked `INCOMPLETE`. Background Fetch is not
used, and the application does not promise downloads will continue after the
operating system terminates it. Cancel stops the current operation; it is not the
same action as remove quarter.

Progress counts logical manifest resources and bytes. Content-addressed storage
deduplicates equal hashes, and equal-hash resources in one quarter task share an
in-flight fetch. A failed or interrupted update keeps the active version while
its staging version is marked `UPDATE_INCOMPLETE`.

Settings exposes a retry action when Service Worker registration fails; it is
hidden while registration is still in progress or when the browser does not
support the required APIs. Quarter pages never retry registration directly: when
offline capability is unavailable they link to Settings. Settings also exposes a
confirmed remove action for `INCOMPLETE`, `UPDATE_INCOMPLETE`, and
`UPDATE_AVAILABLE` records. Removal deletes the active/staging metadata and
progress record, then garbage-collects only content that no remaining shell or
quarter references.

## Settings interaction contract

Downloading is a nonblocking page task. Progress ticks update the current queue
row and, when needed, its quarter status; they do not rerender every Settings
section. Metadata notifications carry scopes so app, storage, quarter, queue,
and selector areas can refresh independently. A coalescing scheduler and
revision token prevent an older asynchronous read from replacing newer DOM.
Selectors, menus, and the user's focus remain mounted while unrelated progress
changes arrive.

The ordinary UI uses `未下载`, `下载中 N%`, `已暂停`, `等待网络`, `下载未完成`,
`已下载`, and `有更新`. Service Worker capability details and raw incomplete
state names remain in the advanced diagnostics area. Pause, continue, cancel,
retry, and online resume are explicit queue actions; cancel does not remove a
verified quarter, and an interrupted update keeps the active version available.

## Content lifecycle invariants

Content GC is reference-safe across windows and the Service Worker. A quarter is
`COMPLETE` only when every manifest content hash is physically present and
verified in the content store. On browsers without cross-context locking,
cleanup may be deferred rather than risking verified offline content.

## Verified local Archive scope

Archive offers an explicit `LOCAL / 仅本机可用` scope. It enumerates saved
quarter-state records and accepts only `COMPLETE`, `UPDATE_AVAILABLE`, or
`UPDATE_INCOMPLETE` states with an active manifest. A `COMPLETE` state must not
have a staging package. For every accepted quarter, the active manifest is
validated and its `data/quarters/YYYY-MM.json` content is read from the
content-addressed store only after checking the actual bytes against the
manifest hash and size and confirming that `data_revision` matches that hash.
The payload quarter and its TV premiere, TV continuing, and Movie premiere
collections are also checked before use.

`UPDATE_AVAILABLE` and `UPDATE_INCOMPLETE` can use only their still-verified
active version; staging content is never included. Missing or invalid active
content skips that quarter while healthy quarters remain searchable. A
runtime-only cache entry, an incomplete-only quarter, or a quarter absent from
saved state does not establish local coverage. The scope does not fetch an
annual catalog, an uncached quarter, or network detail data to fill gaps.

The Archive rebuilds its query records in memory from the accepted quarter
payloads. Each TV premiere or continuing appearance and each Movie premiere
keeps its subject, quarter, and appearance identity. Counts distinguish
appearance records from unique works. Details opened from this scope use the
same in-memory verified snapshot as the result list. `?local=1` can restore the
scope, but the saved quarter packages remain specific to that browser profile;
the URL does not transfer offline data to another device. YEAR and RANGE keep
their generated-catalog behavior, and the default online Archive is unchanged.

## Cover cache identity

The offline manifest identifies an original physical cover as:

```text
covers/<ID>.webp
```

Browser HTML and JSON use the revision-bearing URL:

```text
covers/<ID>.webp?v=<content-hash>
```

The Service Worker must deterministically normalize the browser URL to the same
content-addressed cache entry described by the manifest. It must not cache a
second duplicate copy of the cover as a workaround.

Browse previews additionally use `covers/previews/<identity>.webp`, without a
query parameter. The deterministic full SHA256 identity combines the original
content hash with the encoding-rule fingerprint defined in [Static build](static-build.md).
It is distinct from the actual preview content SHA256. The quarter manifest
includes both the page's previews and all original covers, each with its actual
SHA256 and size; detail/Lightbox therefore remain available offline. Small or
non-beneficial previews can contain the original bytes, and equal-content hashes
share one content-store entry through the existing deduplication protocol.

A preview rule change is resource/package maintenance: manifest `revision` may
change while unchanged quarter JSON retains its facts `data_revision`. Missing
or invalid new preview content cannot complete the replacement. The existing
active package remains available while staging is incomplete; pause/resume,
complete-quarter units, verification, and reference-safe GC keep their existing
semantics. Old pages without a preview rule use originals; a failed browse
preview falls back once to its original, then to the real missing-cover state.

## Updates

A new Service Worker or application shell produces a thin, nonblocking notice.
The user explicitly refreshes when ready; the application never performs a
surprise reload. Updates do not switch a complete archive snapshot or block
ordinary online startup.

### Verified quarter page freshness

Generated quarter HTML pins its quarter JSON URL as
`data/quarters/YYYY-MM.json?v=<SHA256>`. The hash is the JSON bytes' SHA256 and
matches the quarter manifest's `data_revision`; the package `revision` may also
change when shell or other offline resources change. This keeps an opened old
quarter page bound to its matching JSON and distinguishes archive data changes
from an application update.

An ordinary same-origin quarter navigation first honors a matching per-client
pin created by a successful user-selected online update. Without such a pin, it
can use the immediately available saved page only when the quarter metadata is
`COMPLETE`, has no staging package, its active manifest validates, its page and
JSON bytes exist with the declared hashes and sizes, and the HTML's pinned JSON
hash matches `data_revision`.
Complete status continues to mean the entire manifest was verified when the
download finished. Missing or damaged page/JSON content, staging state, a
runtime-only visit, another route, or older HTML without the pinned JSON URL
uses the existing network-first path.

After the quarter page is available, the page asks its controlling Service
Worker to check the same-origin quarter manifest and verify the current JSON
bytes. A different `data_revision` produces a small page notice; matching data,
an unavailable manifest, invalid identity, or a failed hash check does not
claim that an update exists. CSS/JavaScript/package-only changes remain covered
by the separate application update notice, even when both notices are visible.
The check never replaces the open page's JSON, promotes online data into the
offline active package, changes `COMPLETE` or staging state, or adds a quarter
to the download selection.

The `查看更新` action is explicit. The Service Worker rechecks the requested
quarter manifest, page, JSON, and the page's versioned core CSS/JavaScript
resources before serving that online generation. It stores only those needed
resources and the page in the existing bounded runtime cache, then pins that
generation to the resulting client. Cover resources keep their existing
content-hash URLs and verification path. If verification or loading fails, the
saved page remains available and the user receives an inline status; a
successful online view still does not count as a complete offline download.
Use Settings' explicit quarter download/update action when the offline package
itself should be replaced.

The selected online quarter pin follows the same tab across same-origin
non-quarter pages, so returning to that quarter can keep the selected
generation. Navigating to a different quarter clears the previous quarter pin;
each tab keeps its own pin, and online pins never replace offline active data.
