# Agent Prompt: Implement SwimBuddz Test Suite

> Copy everything below the line and paste it as the prompt to the AI agent.

---

## Task

Implement the comprehensive test suite for the SwimBuddz backend, following the plan defined in two documents that already exist in the codebase. Work through the implementation checklist phase by phase, running tests after each file to catch errors early.

## Context

The SwimBuddz backend is a Python FastAPI microservices application with 12 services that communicate via HTTP through internal routers and a centralized service client (`libs/common/service_client.py`). A major architectural refactor was recently completed and the codebase needs a proper test suite to validate everything works.

Two planning documents have already been written and are your source of truth:

1. **`TESTING_ARCHITECTURE.md`** — Read this FIRST. It explains the 3-layer testing strategy (unit, integration, contract), what each layer tests, what NOT to test, and the service dependency map.

2. **`TESTING_IMPLEMENTATION_GUIDE.md`** — This is your implementation guide. It contains copy-pasteable code for every infrastructure file and example tests. It also has a step-by-step implementation checklist at the bottom — work through that checklist in order.

Also read **`CLAUDE.md`** at the project root for overall project conventions and architecture.

## How to work

1. **Read the docs first.** Before writing any code, read `TESTING_ARCHITECTURE.md` fully, then `TESTING_IMPLEMENTATION_GUIDE.md` fully. Understand the 3-layer strategy, the fixture design, and the factory pattern.

2. **Follow the implementation checklist** in `TESTING_IMPLEMENTATION_GUIDE.md` (section "Step-by-Step Implementation Checklist"). Work through it in order: Phase 1 (infrastructure) → Phase 2 (members) → Phase 3 (sessions + attendance) → Phase 4 (academy) → Phase 5 (payments) → Phase 6 (communications + unit tests) → Phase 7 (cleanup).

3. **Run tests after every file you create.** Don't batch. After creating each file, run `cd /Users/i/Documents/work/swimbuddz/swimbuddz-backend && pytest tests/ -v` (or the relevant subset) to catch import errors and fixture issues immediately. Fix failures before moving to the next file.

4. **Adapt the code examples to match the real codebase.** The implementation guide contains example code that is close to correct but may need adjustment. Before writing each file:
   - Read the actual service model (`services/<service>/models.py`) to verify field names and types for factories
   - Read the actual router or internal router to verify endpoint URLs and request/response shapes
   - Read the actual `app/main.py` for each service to verify how routers are mounted and what URL prefixes are used
   - If something doesn't match the guide, trust the actual code, not the guide

5. **Don't modify production code.** You are ONLY creating and modifying test files. The only exception is the root `conftest.py` which needs to be redesigned (the guide explains how). Do NOT modify any files under `services/`, `libs/`, or `scripts/`.

6. **Stop at phase boundaries if context is getting long.** Each phase is self-contained. If you're running low on context, finish the current phase, run all tests to confirm they pass, then report what's done and what's remaining. The next agent can pick up from the checklist.

## File locations

All work happens in the `swimbuddz-backend/` directory:

```
swimbuddz-backend/
├── conftest.py                    ← REWRITE (root test config)
├── pytest.ini                     ← CREATE
├── tests/
│   ├── __init__.py                ← CREATE
│   ├── conftest.py                ← CREATE (shared fixtures)
│   ├── factories.py               ← CREATE (model factories)
│   ├── unit/                      ← CREATE directory
│   │   ├── __init__.py
│   │   ├── test_member_tiers.py   ← MOVE from tests/test_members_service.py
│   │   ├── test_session_stats.py  ← MOVE from tests/test_session_stats.py
│   │   └── (new unit test files)
│   ├── integration/               ← CREATE directory
│   │   ├── __init__.py
│   │   ├── conftest.py            ← CREATE (service client fixtures)
│   │   └── (new integration test files)
│   └── contract/                  ← CREATE directory
│       ├── __init__.py
│       ├── conftest.py            ← CREATE
│       └── (new contract test files)
```

## Critical rules

- **Phase 1 must be completed before any other phase.** The infrastructure (conftest files, factories, pytest.ini) is the foundation everything else depends on.
- **Always verify factory field names** against the actual model source. The most common failure is a factory using a field name that doesn't exist on the model.
- **Enum values are strings**, not Python enum instances. Check each model to see if values are uppercase (`"CLUB"`, `"SCHEDULED"`) or lowercase (`"approved"`, `"pending"`).
- **Internal endpoint URL prefixes vary by service.** Always check the actual router file and `app/main.py` to verify the exact URL path.
- **The `_db_override` function must use `async def` with `yield`** to match FastAPI's dependency injection pattern for database sessions.
- **Do NOT install new packages.** Everything needed is already in `pyproject.toml`.
- **Do NOT touch migration files.** The production database depends on them.

## Definition of done

- All ~105 tests pass when running `pytest tests/ -v`
- Old test files (`test_db.py`, `test_registration_flow.py`, `test_session_delete.py`) are deleted
- Old test logic from those files is preserved in the new test structure
- `CLAUDE.md` test commands section is updated to reflect the new structure
- Tests can be run by layer: `pytest tests/unit/`, `pytest tests/integration/`, `pytest tests/contract/`
- Tests can be run by marker: `pytest -m unit`, `pytest -m integration`, `pytest -m contract`

## If you get stuck

- Factory creation fails → read the model source and compare field names
- Import error → check that `__init__.py` files exist in all test directories
- Fixture not found → make sure the conftest.py is in the right directory level
- 404 on endpoint → check the actual router URL prefix in `app/main.py`
- Service client mock not working → verify the patch path matches the import path used in the service code (it should be `libs.common.service_client.<function_name>`)
- Auth 403 when it shouldn't → check which dependency the endpoint uses (`get_current_user` vs `require_admin` vs `require_coach`) and make sure you override that specific one
