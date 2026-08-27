# API field notes

Verified against the official public Bangumi v0 OpenAPI (version 2026-07-24)
and small read-only responses on 2026-08-27.

- `GET /v0/subjects` accepts Anime `type=2`, `cat`, `year`, `month`, `limit`,
  and `offset`. Anime categories are TV `1` and Movie `3`; both are paginated
  Browse evidence.
- `POST /v0/search/subjects` remains explicitly experimental. It requires a
  `keyword` and supports Anime `type` plus inclusive date filters such as
  `>=2026-03-25` and `<2026-07-01`. It supplements TV boundary observation;
  its previous-quarter lookback is filtered to TV candidates only; it does not
  establish TV/Movie admission by itself.
- Browse, Search, and single-subject responses currently expose `id`, `type`,
  `date`, `platform`, titles, summary, `eps`, rating, tags, images, and
  structured Infobox. Image variants are explicit URLs and must be decoded
  before their dimensions are trusted.
- Japanese-only admission consumes exact `meta_tags` region tokens and the
  verified Infobox keys `制片国家/地区`, `国家/地区`, `制片国家`, and `地区`.
  Values are normalized with NFKC/trim and split only on the documented exact
  separators, including `・`; `欧美` remains a broad unresolved label.
  Compatible same-source co-production is accepted, while independent
  Japan/non-Japan source disagreement remains REVIEW. Ordinary `tags` are not
  country evidence, and no title, summary, language, staff, or company
  inference is permitted.

Fixtures retain representative public response shapes without tokens, headers,
full dumps, or user data. The detailed country examples and parsing outcomes
are documented in [country-filter.md](country-filter.md).
