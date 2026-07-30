# API field notes

Verified against the public Bangumi v0 API on 2026-07-30 for the current
release scope.

- TV discovery uses the animation browse endpoint with TV category `1`, one
  paginated request for each of April, May, and June 2026.
- Subject detail supplies the authoritative media format, first-air date,
  titles, summary, rating, structured Infobox, raw tags, main episodes, and
  subject image candidates.
- The country filter consumes only configured structured Infobox keys and only
  exact `日本` or `Japan` tokens. It never infers country from title, summary,
  tag, language, staff, or company fields.
- Only accepted Japan TV subjects proceed to main-episode and cover handling.
  The release makes no character, person, role, or role-image API requests.

Fixtures retain representative public response shapes without tokens, headers,
full dumps, or user data. The detailed country examples and parsing outcomes
are documented in [country-filter.md](country-filter.md).
