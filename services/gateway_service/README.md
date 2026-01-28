# Gateway Service Guide

This service exposes the unified HTTP API consumed by SwimBuddz clients and MCP tools. Treat it as an **async FastAPI BFF** that stitches together domain services without owning deep business logic.

## Key Points

- Entry point: `app/main.py` creates an async FastAPI app. Always register routers under `app/api/`.
- Dependencies:
  - `get_current_user()` / `require_admin()` from `libs/auth`.
  - `get_async_db()` from `libs/db` for cross-service data.
- Routers should only orchestrate:
  1. Validate/parse requests (Pydantic schemas in `app/schemas/`).
  2. Call domain/service-layer functions (import directly when possible; fall back to internal HTTP if needed).
  3. Shape responses for frontend/mobile use.
- Keep responses aligned with `API_CONTRACT.md`. Any breaking change must be versioned.
- Tests live under `app/tests/` and should focus on orchestrations (e.g., `GET /api/v1/me/dashboard` combining identity + attendance summaries).

## Agent Workflow

1. Read `ARCHITECTURE.md` and `CONVENTIONS.md`—the gateway follows those patterns exactly.
2. Implement new endpoints by:
   - Adding schemas → router → service helper.
   - Reusing domain functions instead of duplicating logic.
3. Update docs (`API_CONTRACT.md`, this README) when adding noteworthy flows.

If unsure how to wire a feature, leave a TODO referencing the relevant domain service and keep the gateway thin.
