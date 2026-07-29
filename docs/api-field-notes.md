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
- `GET /v0/episodes` accepts `subject_id`, `type=0`, `limit`, and `offset`.
  Main-story episode rows expose `id`, `ep`, `sort`, `name`, `name_cn`,
  `airdate`, `duration`, and `duration_seconds`; non-main types are excluded.
- `GET /v0/subjects/{id}/characters` provides subject-local relation order,
  exact `relation`, character `images`, and the embedded ordered `actors`.
  The verified exact relation for configured main characters is `主角`.
- `GET /v0/characters/{id}` and `GET /v0/persons/{id}` provide original names
  and structured Infobox values. The only configured Chinese-name key is the
  exact `简体中文名`; missing values remain missing and are never translated.
- Subject and character `images` are explicit URL objects. The client selects
  the first available `large`, `medium`, `common`, `grid`, then `small` URL,
  while excluding the Bangumi `no_icon_subject.png` placeholder. Person images
  are intentionally not retained or fetched by synchronisation.

The OpenAPI document describes the browse endpoint, optional anonymous bearer
security, animation category values, pagination, and the subject fields. The
real response matched those fields in this smoke check. The implementation
accepts absent optional fields and ignores unknown fields.

Sources: <https://bangumi.github.io/api/> and
<https://bangumi.github.io/api/dist.json>.
