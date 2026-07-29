# Bangumi API field notes

Verified anonymously on 2026-07-29 against the official v0 API, using a
project User-Agent and small requests only. No response bodies are stored in
this repository.

- `GET /v0/subjects` accepts `type=2`, `cat`, `year`, `month`, `limit`, and
  `offset`. The TV category is `1`; the theatrical movie category is `3`.
- Browse responses provide `total`, `limit`, `offset`, and `data`; a second
  page with a larger offset was accepted.
- Browse and detail responses exposed `id`, `name`, `name_cn`, `date`,
  `platform`, `rating`, `tags`, `infobox`, and `images` for the smoke-tested
  subject. `date` and `platform` were strings, while `rating` and `images`
  were objects and `tags` and `infobox` were lists.
- A raw tag had `name`, `count`, and `total_count`. An Infobox item had `key`
  and a structured `value`; the value is therefore retained as JSON rather
  than flattened.

The OpenAPI document describes the browse endpoint, optional anonymous bearer
security, animation category values, pagination, and the subject fields. The
real response matched those fields in this smoke check. The implementation
accepts absent optional fields and ignores unknown fields.

Sources: <https://bangumi.github.io/api/> and
<https://bangumi.github.io/api/dist.json>.
