# API field notes

Verified against the official public Bangumi v0 OpenAPI (version 2026-07-24)
and small read-only responses on 2026-08-10.

- `GET /v0/subjects` accepts Anime `type=2`, `cat`, `year`, `month`, `limit`,
  and `offset`. Anime categories are TV `1` and Movie `3`; both are paginated
  Browse evidence.
- `POST /v0/search/subjects` remains explicitly experimental. It requires a
  `keyword` and supports Anime `type` plus inclusive date filters such as
  `>=2026-03-25` and `<2026-07-01`. It supplements TV boundary observation;
  it does not establish TV/Movie admission by itself.
- Browse, Search, and single-subject responses currently expose `id`, `type`,
  `date`, `platform`, titles, summary, `eps`, rating, tags, images, and
  structured Infobox. Image variants are explicit URLs and must be decoded
  before their dimensions are trusted.
- Japanese-only admission consumes only exact structured country evidence. It
  never infers country, media format, or quarter from titles, summaries, tags,
  staff, or companies.

Fixtures retain representative public response shapes without tokens, headers,
full dumps, or user data. The detailed country examples and parsing outcomes
are documented in [country-filter.md](country-filter.md).
