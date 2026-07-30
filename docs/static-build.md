# Static build

`bgmb build` is offline. It reads only local SQLite facts, checked-in rules,
templates, static source files, and verified subject covers; it never syncs or
publishes.

```powershell
bgmb build 2026 4
bgmb build --all
```

Both commands build the configured release scope only: `2026-04` Japan TV.
An explicit scope must equal that configuration. `--all` means all configured
release quarters, not every quarter found in SQLite.

The build performs a second structured-country check from stored Infobox facts.
Only `new` TV subjects with consistent Japan evidence are projected. Database
quarters outside configuration are not rendered, linked, or placed in
navigation; their keys are listed as `ignored_database_quarters` in the build
report.

`dist/local/` and `dist/pages/` share the same models, Jinja templates, CSS,
and native JavaScript. They contain quarter cards, drawers, subject detail
pages, main-story episodes, and covers only. No build profile queries role or
person tables, emits a role/voice section, or copies character media. Pages
uses WebP cover derivatives and rejects any character-media output.

Outputs are assembled in `dist/.staging/`, validated, then atomically promoted
so a failed build preserves the previous complete profile. The external build
report records configured quarters, ignored database quarters, TV subject
count, country-filtered subject count, and `character_sections: 0`.

Runtime pages use static HTML, CSS, and native JavaScript only. They do not
read SQLite or request Bangumi data. Pages PWA release checks occur only after
an explicit user action; normal startup uses the active verified snapshot.
