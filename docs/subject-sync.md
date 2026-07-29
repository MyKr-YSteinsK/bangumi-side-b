# Subject sync foundation

## Current command

`bgmb sync YEAR QUARTER_MONTH`, `bgmb sync YEAR`, and
`bgmb sync START-END` synchronise TV and theatrical-movie subject facts only.
`--force` refreshes stable details; normal runs refresh ratings from browse
results while reusing successful detail snapshots. `sync` does not build or
publish anything.

The API was verified anonymously against the current v0 documentation and a
small live smoke request. Browse uses animation type `2`, TV category `1`, and
movie category `3`, with `year`, `month`, `limit`, and `offset`. Details retain
structured Infobox values, raw tag names/counts/order, rating, date, platform,
and titles. See [API field notes](api-field-notes.md) for the field record.

## Sync strategy

For every quarter month, the command fully pages TV and movie candidates for
the three months, deduplicates IDs, applies the configured blacklist and
explicit unsupported-format filter, then confirms each remaining item by
detail. A subject is saved only when its complete first-air date belongs to the
target quarter; TV is recorded as `new`, and theatrical movies as `movie`.
Missing dates and ownership mismatches are reported, never guessed.

Each stored subject uses one transaction for facts, titles, Infobox, raw tags,
source evidence, quarter relation, and sync state. Local per-subject failures
are recorded safely and do not stop other items. Reports are written under
`workspace/reports/` and never include response bodies, headers, tokens,
absolute paths, or stack traces.

## Schema outline

SQLite is the fact source. `subjects` is global; titles, Infobox, raw tags,
sources, and quarter appearances are subject-owned children. `characters` and
`persons` are global future entities connected through `subject_characters` and
`character_voices`, so they are only removed when orphaned. Migrations are
numbered, transactional, backed up before changes, and enable foreign keys.

The next Plan should cover episodes, characters, voice actors, their relations,
and image handling. Static build, pages, PWA, and publishing remain out of
scope.
