# Subject sync

## Current command and scope

The checked-in first release accepts exactly one command scope:

```powershell
bgmb sync 2026 4
```

`sync YEAR`, a year range, another quarter, theatrical movies, continuations,
roles, people, and role images are outside the current release scope and are
rejected or skipped. `sync` never builds or publishes.

## Discovery and admission

Discovery makes only the three paginated TV browse requests for April, May,
and June 2026, then deduplicates subject IDs. Each candidate is confirmed by
subject detail in this order:

1. usable detail and TV format;
2. complete first-air date inside 2026-04;
3. verified structured Infobox country evidence;
4. persistence, main-story episodes, and subject cover.

Country evidence uses only the configured Infobox keys. The exact `日本` or
`Japan` token is required; a consistent co-production value is accepted.
Missing, unparseable, conflicting, or non-Japan values are excluded before
episodes, covers, or any role-related request. See
[country filter](country-filter.md) for the parsing rule and audit decisions.

## Storage, retries, and reports

Accepted subjects are stored transactionally with titles, Infobox, raw tags,
source evidence, one `new` 2026-04 quarter relation, main-story episodes, and
at most a verified subject cover. Existing accepted subjects can refresh their
rating and incomplete episodes without expanding scope.

Reports under `workspace/reports/` record safe counters and a country audit;
they never contain API bodies, headers, tokens, absolute paths, or stack
traces. Progress supports `--progress auto|plain|off`, `--verbose`, and
`--quiet`. Plain output uses permanent lines; TTY output clears a previous
longer line before drawing a shorter one.
