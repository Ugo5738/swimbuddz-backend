# AGENTS.md — Universal AI Agent Instructions (`swimbuddz-backend`)

> **Applies to ALL AI Assistants:** OpenAI ChatGPT / Codex, Anthropic Claude, Google Gemini / Antigravity, Cursor, GitHub Copilot, and any automated coding or planning agent.

---

## 1. Mandatory Working Habit: "Deliberate → Document (`docs/DECISIONS.md`) → Code"

1. **Read Settled Decisions FIRST:**
   - Before proposing, modifying, or refactoring any product workflow, pricing/commercial rule, attendance behavior, or accounting boundary, **read [`docs/DECISIONS.md`](./docs/DECISIONS.md)** and the corresponding domain runbook in [`docs/`](./docs/).
2. **Never Silently Override a Settled Decision:**
   - Do **not** change an invariant documented in `docs/DECISIONS.md` (`ADR-001` through `ADR-006`) or `docs/*.md` as a side effect of another task.
   - If a request conflicts with an existing ADR in `docs/DECISIONS.md`, cite the ADR number and rationale and ask whether the user wants to explicitly supersede it.
3. **Always Update Documentation When Deliberating or Building:**
   - Whenever you and the user think through, debate, or agree on a product rule, edge case, pricing model, or architecture choice, **immediately record or update the decision and *why* it was made in [`docs/DECISIONS.md`](./docs/DECISIONS.md)** (and `../docs/DECISIONS.md` if the parent workspace is accessible) as well as the relevant runbook in `docs/`.

---

## 2. Required Backend Reading

- [`docs/DECISIONS.md`](./docs/DECISIONS.md) — Canonical Product & Architecture Decision Log (`ADR-001`–`ADR-006`)
- [`docs/AGENT_INSTRUCTIONS.md`](./docs/AGENT_INSTRUCTIONS.md) — Backend AI agent instructions
- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — Microservices architecture
- [`docs/CONVENTIONS.md`](./docs/CONVENTIONS.md) — FastAPI / SQLAlchemy / Alembic conventions
- [`docs/API_ENDPOINTS.md`](./docs/API_ENDPOINTS.md) — HTTP API reference
