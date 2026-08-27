# Active Documentation Ownership

This registry is the durable repository-visible replacement for the migration
package's supporting-doc manifest. Active documents may link to one another,
but they must not duplicate or contradict the owner listed here. Archived
material under `docs/archive/` is historical evidence only.

| Owner | Canonical responsibility |
| --- | --- |
| `AGENTS.md` | Repo-specific AI safety, data boundaries, CLI side effects, and release guardrails; generic Skills/runtime workflow is intentionally outside the file. |
| `docs/project/PROJECT_BRIEF.md` | Stable purpose, scope, architecture boundary, invariants, non-goals, and accepted UX baselines. |
| `docs/project/DECISIONS.md` | Durable product/repository decisions and superseding relationships. |
| `docs/project/CURRENT_STATE.md` | Adoption-time and current branch, version, capability, risk, USER CHECK, and delivery facts. |
| `README.md` | Public entry point, high-level product scope, installation, and navigation to current docs. |
| `docs/USER_GUIDE.md` | Current operator workflow and observable CLI, browsing, PWA, and release behavior. |
| `docs/development.md` | Repo-specific engineering semantics, command side effects, runtime boundaries, and frontend implementation constraints. |
| `docs/country-filter.md` | Deterministic country/region admission rules and structured evidence outcomes. |
| `docs/api-field-notes.md` | Date-stamped Bangumi API field evidence and fixture boundaries. |
| `config/source-rules.toml` | Exact source vocabulary and Infobox/tag mappings. |
| `config/allowed-tags.toml` | Exact display-tag membership and order. |
| `config/bangumi.toml` | Sync settings and manual/automatic exclusion configuration. |
| `config/quarter-overrides.toml` | Explicit human quarter adjudication; AI must not populate it autonomously. |
| `config/japanese-overrides.toml` | Explicit human Japanese-scope adjudication; separate from quarter ownership and AI must not populate it autonomously. |
| `docs/static-build.md` | Offline, incremental build, staging, rollback, and single-output contract. |
| `docs/pwa.md` | Online shell, runtime cache, complete-quarter download, replacement, GC, and update lifecycle. |
| `docs/visual-system.md` | Visual tokens, layout, accessibility, responsive UX, and local motion details. |
| `docs/publish.md` | Operational release prepare/publish runbook and safety checks. |
| `docs/releases.md` | Application version versus Pages batch identity and release metadata semantics. |
| `docs/repository-metadata.md` | Manual repository administration boundary; no remote metadata writes by the agent. |
| `CHANGELOG.md` | Concrete application release history consumed by build-time Settings output. |
| `src/bgm_side_b/_version.py` / `pyproject.toml` | Package and application version contract. |
| `.github/workflows/ci.yml` | Fast/default CI coverage and its local-only data/network boundary. |
| `.github/workflows/deep-regression.yml` | Explicit manual/deep regression coverage. |
| `LICENSE` | Source-license boundary; it does not grant rights to Bangumi data or media. |

Implementation modules, static assets, fixtures, and private workspace data are
owned by their code/config contracts and tests; they are not alternate AI
instruction or product-requirement aggregates. `docs/archive/README.md` is the
historical disposition index and cannot act as an active contract.
