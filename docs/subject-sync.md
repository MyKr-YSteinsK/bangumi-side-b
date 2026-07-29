# Subject sync foundation

## Current command

`bgmb sync YEAR QUARTER_MONTH`, `bgmb sync YEAR`, and
`bgmb sync START-END` synchronise TV and theatrical-movie facts only. Every
discovered candidate is confirmed with subject detail so its rating is refreshed
from the detail response; an absent rating never clears an existing value.
`--force` refreshes all non-image structured units. `--force-images` is
independent and revalidates/redownloads images without implying `--force`.
`sync` does not build or publish anything.

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
source evidence, permanent quarter relation, and typed sync states. Main-story
episodes are stored in API order; TV continuation quarters require an exact
episode-air-date or configured end-date evidence value. Only exact configured
main-character relations are saved, with every embedded actor retained as a
subject-local voice relation. Local failures are typed and do not discard other
successful facts or snapshots.

Reports under `workspace/reports/` contain command flags, timing, per-quarter
and total counters, and safe typed failures; they omit response bodies, headers,
tokens, absolute paths, and stack traces. Ctrl+C returns 130 after writing a
partial usable report.

## Schema outline

SQLite is the fact source. `subjects` is global; titles, Infobox, raw tags,
sources, episodes, and quarter appearances are subject-owned children.
`characters` and `persons` are reusable global entities connected through
`subject_characters` and `character_voices`, and are removed only when orphaned.
`media_files` records only subject covers and main-character images with source
URL, relative path, SHA-256, MIME type, size, dimensions, and status.

Images download through the API client to `workspace/tmp/`, require an image
Content-Type and Pillow decode check, then atomically replace a verified-format
target below `workspace/media/`. A failed replacement retains an old valid file.
Person images are never stored. Migrations are numbered, transactional, backed
up before changes, and enable foreign keys.

The next Plan can build offline static data views and page structure from this
local model. Pages, PWA, publishing, and visual-system work remain out of scope.
