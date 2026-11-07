# Agent Instructions – SwimBuddz Backend

This document tells **AI coding agents** how to work inside this repository without drifting.

If you are an automated AI assistant making changes here, you MUST follow these rules.

---

## 1. Always Read These Files First

Before making any code changes or generating new files, you MUST:

1. Read [`docs/DECISIONS.md`](./DECISIONS.md) (and workspace root `../docs/DECISIONS.md`) – **canonical log of settled product, pricing, onboarding, attendance, and architecture decisions (`ADR-001`–`ADR-006`) and why they were made**.
2. Read `docs/ARCHITECTURE.md` – to understand the overall microservice structure.
3. Read `docs/CONVENTIONS.md` – to follow coding and style rules.
4. Read `docs/API_CONTRACT.md` – to understand the expected API surface.
5. Read the relevant domain runbook in `docs/` (e.g., `ACADEMY_ADMIN_COMMERCIAL_TERMS_RUNBOOK.md`, `ACADEMY_BANK_RECEIPT_RECONCILIATION_RUNBOOK.md`, `ACADEMY_ATTENDANCE_CLASS_ACCOUNTING_RUNBOOK.md`, `CLUB_ONBOARDING_ACCESS_AND_OPERATIONS.md`, `CLUB_MOBILITY_AND_TRANSFERS_RUNBOOK.md`, `SESSION_SETTLEMENT_OPERATIONS_RUNBOOK.md`).

Do not ignore or override these documents.

---

## 1A. Mandatory Cross-Agent Habit: Deliberate → Document → Code

Every AI agent (OpenAI ChatGPT / Codex, Anthropic Claude, Google Gemini / Antigravity, Cursor, GitHub Copilot) MUST follow these two rules:

1. **Never Override Settled Decisions Without Explicit Approval**:
   - Before proposing or implementing changes to commercial terms, discount codes, bank transfer reconciliation, attendance defaults, Club onboarding/mobility, membership boundaries, or member deletion, check [`docs/DECISIONS.md`](./DECISIONS.md).
   - If your idea or a prompt conflicts with an accepted ADR in `docs/DECISIONS.md`, stop and surface the conflict with the ADR number and rationale instead of silently changing the behavior.
2. **Always Update the Docs When Thinking Through or Implementing Features**:
   - Whenever you and the user think through, deliberate on, or implement a product rule, pricing/commercial mechanism, user journey, or technical design, **update [`docs/DECISIONS.md`](./DECISIONS.md) (and `../docs/DECISIONS.md` if workspace root is mounted) plus the relevant runbook in `docs/` in the same session/commit**.

---

## 2. Task Execution Rules

- **Follow `TODO.md` in order.**
  - Implement tasks **sequentially**, unless explicitly instructed otherwise.
  - Do not skip tasks or invent new phases.

- **One task at a time.**
  - When implementing a task, focus only on the scope described in that task.
  - Do not introduce unrelated features or refactors.

- **Satisfy acceptance criteria.**
  - Each task has acceptance criteria.
  - Only consider a task complete when all criteria are met.

- **No scope creep.**
  - If you think of an improvement that is not in `TODO.md`:
    - Add a comment or TODO in code, or
    - Append a suggestion at the end of `TODO.md` under a “Future Ideas” section.
  - Do NOT implement it immediately unless explicitly asked.

---

## 3. File and Folder Rules

- **Do not change the top-level directory layout** unless `TODO.md` explicitly tells you to.
- Use these paths consistently:
  - Shared libs: `libs/common`, `libs/db`, `libs/auth`.
  - Services: `services/<service_name>/app/`.
  - MCP: `mcp/swimbuddz_core_mcp/`.

- **Do not create new top-level directories** such as:
  - `backend/`, `src/`, `server/`, or similar.
  - All backend code belongs under the structure already defined.

- **Per-service structure standard:**

  ```bash
  services/<service_name>/app/
    main.py
    api/
    models/
    schemas/
    core/
    services/   # optional
    tests/
  If a subfolder does not yet exist, you may create it following this pattern.
  ```

---

## 4. Dependencies and Environment

- Only add Python dependencies in `pyproject.toml` or `requirements.txt` (whichever exists).
- Never hard-code secrets. Read them from environment variables defined in `.env.example` via `libs/common/config.py`.
- When introducing a new env var:
  1. Add it to the settings model in `libs/common/config.py`.
  2. Add a placeholder to `.env.example`.
  3. Reference it in code through `settings.<NAME>`.
- Keep containerization first-class:
  - Each service requires its own Dockerfile that extends the shared base image.
  - Compose definitions must treat services independently; restarting one container must not take others down.
  - Document any new compose services or env files in `README.md`.

---

## 5. Database and Migrations

- Reflect all schema changes in:
  - SQLAlchemy models under `services/<service_name>/app/models/`.
  - Alembic migrations under `alembic/`.
  - Any related Pydantic schemas.
- Never mutate the database schema without a migration.
- Validate foreign keys and enum changes for backward compatibility.

---

## 6. Code Style and Structure

- Follow `CONVENTIONS.md` for Python version, async FastAPI usage, SQLAlchemy patterns, and response formats.
- Key reminders:
  - Type hints on every function.
  - Keep domain logic in `services/.../app/services/` (or equivalent) and routes as thin orchestrators.
  - Prefer small, focused modules over monolith files.

---

## 7. MCP-Specific Rules

- MCP code lives under `mcp/swimbuddz_core_mcp/`.
- Tools must:
  - Stay small and focused.
  - Call existing backend logic either via direct Python imports or gateway HTTP requests.
- Tools must **not**:
  - Re-create business rules.
  - Touch the database directly.
- If functionality is missing, add it to the backend first, then expose it via MCP.

---

## 8. Testing and Safety

- Add/adjust tests alongside any logic changes.
- Place tests under `services/<service>/app/tests/` or root `tests/` depending on scope.
- Tests should follow flows in `TODO.md`, use dedicated test DBs, and assert on JSON error responses instead of catching-and-forgetting exceptions.

---

## 9. Logging & Observability

- Use `libs/common/logging.py`.
- Log meaningful events (sign-ins, payment status updates, failures) with IDs for traceability.
- Redact or omit sensitive data entirely.

---

## 10. If Unsure

- Avoid inventing architecture; align with the documents referenced above.
- Prefer minimal, well-justified changes.
- If assumptions are necessary, leave TODOs or comments explaining them rather than over-building.
