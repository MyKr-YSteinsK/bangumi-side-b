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

## Cover cache identity

The offline manifest identifies one physical cover as:

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

## Updates

A new Service Worker or application shell produces a thin, nonblocking notice.
The user explicitly refreshes when ready; the application never performs a
surprise reload. Updates do not switch a complete archive snapshot or block
ordinary online startup.
