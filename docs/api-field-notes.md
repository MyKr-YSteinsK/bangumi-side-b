# API field notes

Verified against the official public Bangumi v0 OpenAPI (version 2026-07-24)
and small read-only responses through 2026-08-29.

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
  separators, including `・`. Exact public token `法国` is supported by the
  bounded 2025-01 controls; `欧美` remains a broad unresolved label.
  Compatible same-source co-production is accepted, while independent
  Japan/non-Japan source disagreement remains REVIEW. Ordinary `tags` are not
  country evidence, and no title, summary, language, staff, or company
  inference is permitted.
- Canonical `platform=剧场版` identifies Bangumi's media category but does not
  by itself prove ordinary theatrical exhibition. The bounded 2025-01 detail
  audit found exact Infobox `其他` values `游乐设施电影` and
  `プラネタリウム上映作品`; these are deterministic special-venue exclusions.
  A title or official-site URL is not promoted to runtime media evidence.

Fixtures retain representative public response shapes without tokens, headers,
full dumps, or user data. The detailed country examples and parsing outcomes
are documented in [country-filter.md](country-filter.md).
