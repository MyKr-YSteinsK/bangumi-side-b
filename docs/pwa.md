# Pages PWA contract

This document defines the product contract that Plan 20 will implement. It does
not describe the legacy Pages release implementation that remains temporarily in
the repository.

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

## Legacy boundary

`static/js/pwa-controller.js`, `static/js/pwa-ui.js`, and `static/sw.js` belong to
the legacy snapshot implementation. Plan 20 must not inherit their product
semantics. They remain isolated from the formal unified-site runtime until the
legacy release path is removed in Plan 21.
