# SwimBuddz Backend – TODO

This document lists the ordered tasks required to implement the backend. **Follow tasks sequentially** and satisfy all acceptance criteria before moving on. Refer to `ARCHITECTURE.md`, `CONVENTIONS.md`, and `API_CONTRACT.md` as you implement each item.

---

## Phase 0 – Repository Bootstrap

### Task 0.1 – Initialize project scaffolding

- [x] Task 0.1 – Initialize project scaffolding
- Create `pyproject.toml` with FastAPI, SQLAlchemy 2.x async stack, Pydantic v2, Alembic, httpx, python-dotenv, uvicorn, and testing tools (`pytest`, `pytest-asyncio`, `httpx[http2]`).
- Add `.env.example` with placeholders for `DATABASE_URL`, `SUPABASE_JWT_SECRET`, `SUPABASE_PROJECT_ID`, and any other core settings referenced later.
- Create `libs/`, `services/`, `mcp/`, and `alembic/` directories per `ARCHITECTURE.md`.
  **Acceptance Criteria**

1. Dependencies install via `pip install -e .` (or `pip install -r requirements.txt`).
2. `.env.example` matches settings fields defined in code.
3. Basic `uvicorn` run (`python -m uvicorn services.gateway_service.app.main:app`) succeeds with a placeholder FastAPI app.

### Task 0.2 – Docker + Compose bootstrap

- [x] Task 0.2 – Docker + Compose bootstrap
- Author a reusable backend Docker base image (Python 3.11-slim with Poetry/pip tooling) plus service-specific Dockerfiles that copy only their code.
- Update `docker-compose.yml` so each service runs in its own container (gateway, identity, members, sessions, attendance, communications, payments, academy, db) with restart policies that prevent one failure from crashing the others.
- Ensure compose wiring shares a common network while keeping per-service env files (e.g., `.env.gateway`, `.env.members`) that extend `.env`.
- Document how to `docker compose up`/down, rebuild individual services, and tail logs.
  **Acceptance Criteria**

1. `docker compose up gateway` builds and serves the FastAPI placeholder via http://localhost:8000 while Postgres runs separately.
2. Stopping one service container does not kill the remaining services (use `restart: unless-stopped` and no shared processes).
3. README instructions cover local dev via Docker (build, run, logs) and mention per-service env overrides.

---

## Phase 1 – Shared Libraries

### Task 1.1 – Global configuration (`libs/common`)

- [x] Task 1.1 – Global configuration (`libs/common`)
- Implement `libs/common/config.py` using `pydantic.BaseSettings` for env vars (database URL, Supabase credentials, logging level, etc.).
- Provide `get_settings()` helper with LRU caching.
- Add `libs/common/logging.py` that configures structured logging and exposes `get_logger(name)`.
  **Acceptance Criteria**

1. Importing `get_settings()` reads values from environment and supports `.env`.
2. `get_logger("test")` returns a configured logger with JSON-style output.

### Task 1.2 – Database utilities (`libs/db`)

- [x] Task 1.2 – Database utilities (`libs/db`)
- Implement `libs/db/base.py` defining the declarative `Base`.
- Implement `libs/db/config.py` to build an async SQLAlchemy engine/sessionmaker from `settings.database_url`.
- Provide `libs/db/session.py` with FastAPI dependency `get_async_db()` that yields an `AsyncSession`.
  **Acceptance Criteria**

1. Running an async smoke test can acquire a session and execute `SELECT 1`.
2. Engine/session are singletons reused across services.

### Task 1.3 – Auth helpers (`libs/auth`)

- [x] Task 1.3 – Auth helpers (`libs/auth`)
- Implement `AuthUser` Pydantic model capturing Supabase claims (user_id, email, role, member_id optional).
- Add `get_current_user()` FastAPI dependency that validates Supabase JWT (use `python-jose` or `pyjwt`) and returns `AuthUser`.
- Provide `require_admin()` dependency that ensures `AuthUser.role == "admin"`.
  **Acceptance Criteria**

1. Invalid/missing tokens raise `HTTPException(status_code=401/403)` with safe details.
2. Unit tests cover valid token decoding and admin guard failure.

---

## Phase 2 – Database & Migrations

### Task 2.1 – Alembic setup

- [x] Task 2.1 – Alembic setup
- Initialize `alembic/` with `env.py` wired to the async engine.
- Configure `alembic.ini`.
- Create seed migration that creates core tables for members, sessions, attendance, announcements, payments, and supporting enums (per models defined later).
  **Acceptance Criteria**

1. `alembic upgrade head` runs successfully against a local Postgres instance.
2. Migration reflects every ORM model introduced in Phase 3.

### Task 2.2 – Test database utilities

- [x] Task 2.2 – Test database utilities
- Create a pytest fixture that spins up an isolated database (use transaction rollbacks or a temp schema).
- Document how to run tests locally with the fixture.
  **Acceptance Criteria**

1. Tests can run concurrently without cross-contamination.
2. Fixture lives under `tests/conftest.py` or service-level fixtures.

---

## Phase 3 – Domain Services

> Follow the per-service layout in `ARCHITECTURE.md`. Each service needs models, schemas, routers, and thin service-layer helpers. All route handlers must be `async def`.

### Task 3.1 – Identity Service (`services/identity_service`)

- [x] Task 3.1 – Identity Service (`services/identity_service`)
- Models: store mappings between Supabase user IDs and member IDs/roles.
- API: `GET /api/v1/identity/me`.
  **Acceptance Criteria**

1. Endpoint returns user info plus linked member ID (or `null`) as defined in `API_CONTRACT.md`.
2. Unit tests mock Supabase JWT and database lookups.

### Task 3.2 – Members Service (`services/members_service`)

- [x] Task 3.2 – Members Service (`services/members_service`)
- Models: `Member` with profile fields (contact info, emergency contact, membership_status enum, swimming level).
- APIs: member CRUD set outlined in `API_CONTRACT.md`.
  **Acceptance Criteria**

1. CRUD routes serialize via Pydantic schemas that mirror contract payloads.
2. Admin listing supports pagination/filter query params (e.g., `status`, `role`).
3. Tests cover create + `GET /me` + admin list filtering + status updates.

### Task 3.3 – Sessions Service (`services/sessions_service`)

- [x] Task 3.3 – Sessions Service (`services/sessions_service`)
- Models: `Session` representing events with fields for title, description, location enum, pool_fee, capacity, time range.
- APIs: create session (admin) + list + get by ID (public) as defined.
  **Acceptance Criteria**

1. Public list endpoint supports filtering and returns chronologically sorted sessions.
2. Only admins can create sessions; tests cover permission errors.

### Task 3.4 – Attendance Service (`services/attendance_service`)

- [x] Task 3.4 – Attendance Service (`services/attendance_service`)
- Models: `SessionAttendance` linking members to sessions with ride-share fields, payment status enums, total_fee.
- APIs: sign-in endpoint, member attendance history, admin session attendance list, pool-list export.
- Implement service-layer logic to compute `total_fee` and enforce one attendance row per member/session.
  **Acceptance Criteria**

1. Sign-in endpoint idempotently upserts the caller’s attendance row and returns `AttendanceRead`.
2. Summary endpoint returns the structure in `API_CONTRACT.md`.
3. Pool-list endpoint returns CSV when `Accept: text/csv`.

### Task 3.5 – Communications Service (`services/communications_service`)

- [x] Task 3.5 – Communications Service (`services/communications_service`)
- Models: `Announcement`.
- APIs: create/list/get announcements (public list).
- Schema must support `title`, `summary`, `body`, `category`, timestamps (`created_at`, `updated_at`, `published_at`), and `is_pinned`.
- Categories enum: `rain_update`, `schedule_change`, `event`, `competition`, `general`.
  **Acceptance Criteria**

1. Endpoints align with contract; list is newest-first.
2. Tests cover admin auth for creation and public fetch.
3. Pydantic schemas and migrations reflect the standardized fields above.

### Task 3.6 – Pending Registration Workflow (`services/members_service` + gateway)

- [x] Task 3.6 – Pending Registration Workflow (`services/members_service` + gateway)
- Extend members domain to capture pending registration payloads submitted before email confirmation.
- Add endpoints:
  - `POST /api/v1/pending-registrations` – public endpoint accepting Supabase `user_id` plus questionnaire payload.
  - `POST /api/v1/pending-registrations/complete` – auth-required endpoint that finalizes the pending record into a full `Member` once Supabase confirms the email.
- Persist pending data (JSONB column or normalized tables) and ensure idempotency (repeated submissions overwrite same user_id).
- Wire these endpoints through the gateway with validation + rate limiting (if available).
  **Acceptance Criteria**

1. Database models/migrations capture pending payloads linked to Supabase user IDs and timestamps.
2. Completing a pending registration creates the member profile and deletes/archives the pending record.
3. Tests cover happy path + replays (duplicate submissions, completing without pending record) + unauthorized access.

### Task 3.7 – Payments & Academy Services (stubs)

- [x] Task 3.7 – Payments & Academy Services (stubs)
- Define minimal models/APIs if needed for near-term flows.
- **Crucial**: Implement a mechanism to generate and store a unique **Payment Reference** (e.g., `PAY-12345`) for every transaction, even if manual. This is required for the frontend confirmation screen.
  **Acceptance Criteria**

1. Folder structure exists with placeholder routers/tests referencing future work.
2. Document future ideas under a “Future Ideas” section at the bottom of this file once specifics are known.

---

## Phase 4 – Gateway Service (`services/gateway_service`)

### Task 4.1 – App wiring

- [x] Task 4.1 – App wiring
- Implement `app/main.py` with FastAPI app factory, router inclusion, lifespan events (startup/shutdown), and middleware (CORS, logging).
- Add health check at `GET /health`.
  **Acceptance Criteria**

1. Running `uvicorn services.gateway_service.app.main:app` serves `/health` returning `{"status": "ok"}`.

### Task 4.2 – Identity & Member endpoints

- [x] Task 4.2 – Identity & Member endpoints
- Implement gateway routers that call the identity and members services (prefer direct imports; fall back to HTTP if services run separately).
- Ensure response models exactly match `API_CONTRACT.md`.
  **Acceptance Criteria**

1. Contract tests (snapshot or schema) confirm structures.
2. Error responses use standardized format (per `CONVENTIONS.md`).

### Task 4.3 – Sessions & Attendance endpoints

- [x] Task 4.3 – Sessions & Attendance endpoints
- Expose combined session list/detail endpoints and session sign-in flow through the gateway, orchestrating the domain services.
- Implement `GET /api/v1/sessions/{id}/sign-in-view` (from `ARCHITECTURE.md` examples) that merges session + attendance info for the current member.
  **Acceptance Criteria**

1. Sign-in flow replicates the three-step experience outlined in `ARCHITECTURE.md`.
2. Admin pool-list export accessible via gateway.

### Task 4.4 – Dashboard endpoint

- [x] Task 4.4 – Dashboard endpoint
- Implement `GET /api/v1/me/dashboard` combining identity, member profile, upcoming sessions, attendance summary, and announcements.
  **Acceptance Criteria**

1. Endpoint responds within async orchestration best practices (concurrent awaits where safe).
2. Unit/integration test validates orchestration logic using mocks/fakes for underlying services.

### Task 4.5 – Admin Dashboard endpoint

- [x] Task 4.5 – Admin Dashboard endpoint
- Implement `GET /api/v1/admin/dashboard-stats` aggregating:
  - Total members (active/inactive).
  - Upcoming sessions count.
  - Recent announcements count.
- Ensure this endpoint is protected by `require_admin`.
  **Acceptance Criteria**

1. Returns JSON structure matching the frontend Admin Dashboard needs.
2. Returns 403 Forbidden for non-admin users.

---

## Phase 5 – MCP Layer (`mcp/swimbuddz_core_mcp`)

### Task 5.1 – MCP server scaffolding

- [x] Task 5.1 – MCP server scaffolding
- Create FastAPI (or textual) MCP server exposing tools listed in `ARCHITECTURE.md`.
- Implement auth/config wiring shared with backend libs.
  **Acceptance Criteria**

1. `uvicorn mcp.swimbuddz_core_mcp.server:app` starts and lists available tools.

### Task 5.2 – Tool implementations

- [x] Task 5.2 – Tool implementations
- Implement tools: `get_current_member_profile`, `update_member_profile`, `list_upcoming_sessions`, `get_session_details`, `sign_in_to_session`, `get_my_attendance_history`, `list_announcements`, `create_announcement`.
- Tools should call gateway HTTP endpoints or shared domain functions—no direct DB writes.
  **Acceptance Criteria**

1. Each tool has pytest coverage using mocked HTTP responses/service calls.
2. Error handling mirrors backend HTTP errors and returns user-friendly messages to the MCP host.

---

## Phase 6 – Testing, QA, and Ops

### Task 6.1 – Test suite

- [/] Task 6.1 – Test suite
- Ensure each service has tests under `app/tests/` covering routers, services, and schemas.
- Add integration tests under root `tests/` for cross-service flows (registration, three-step sign-in, admin pool list).
  **Acceptance Criteria**

1. `pytest` passes locally via CI-ready command.
2. Code coverage reports highlight any missing critical paths.

### Task 6.2 – Tooling & CI

- [ ] Task 6.2 – Tooling & CI
- Add linting/formatting (ruff + black or equivalent) and enforce via pre-commit or CI script.
- Add GitHub Actions (or similar) workflow to run lint + pytest + Alembic migration check.
  **Acceptance Criteria**

1. CI workflow succeeds on clean repo.
2. Documentation in `README.md` explains how to run lint/tests locally.

### Task 6.3 – Documentation & handoff

- [ ] Task 6.3 – Documentation & handoff
- Update `README.md` with setup instructions, service overview, and troubleshooting tips.
- Ensure `API_CONTRACT.md`, `CONVENTIONS.md`, and this TODO stay in sync with implemented functionality.
  **Acceptance Criteria**

1. New contributors can follow docs to run the stack locally (docker-compose if needed).
2. TODO’s “Future Ideas” section lists any deferred work discovered during implementation.

---

## Future Ideas

Use this section to list scoped ideas that are **not** part of the current TODO sequence (e.g., WhatsApp integrations, advanced analytics, additional MCP tools).

- **Member Scorecard**: A visual representation of a member's progress, skills, and achievements (linked to Academy service).
- **WhatsApp Integration**: For automated announcements and reminders.
- **Advanced Analytics**: Dashboard for detailed attendance and financial metrics.
- **Stripe Integration**: Replace manual payment reference with real payment processing.
