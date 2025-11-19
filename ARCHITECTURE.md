# SwimBuddz Backend – Architecture

This document describes the **overall backend architecture** for the SwimBuddz platform.

The goals are:

- A clear, **domain-based** backend design.
- A single, stable **HTTP API** that powers web/mobile apps and partner integrations.
- A **Model Context Protocol (MCP) layer** that exposes backend capabilities as tools for AI agents.
- A structure that can scale beyond Nigeria/Africa without major rewrites.

---

## 1. High-Level Overview

The backend is a **Python monorepo** composed of:

1. **Shared libraries (`libs/`)** – common configuration, database access, and authentication helpers.
2. **Domain services (`services/`)** – each encapsulates a core SwimBuddz domain:
   - `identity_service`: links Supabase users to SwimBuddz members and roles.
   - `members_service`: member registration and profiles.
   - `sessions_service`: all swim sessions and events (Yaba, Sunfit, meetups, trips, etc.).
   - `attendance_service`: session sign-ins, ride-share options, pool lists, attendance history.
   - `communications_service`: announcements / noticeboard.
   - `academy_service`: structured learning programs and enrolments.
   - `payments_service`: payment records and status (manual first, gateways later).
   - `gateway_service`: API gateway / backend-for-frontend (BFF) for client apps.
3. **MCP server (`mcp/`)** – `swimbuddz_core_mcp` exposes high-level tools to AI agents.
4. **Migrations (`alembic/`)** – Alembic manages the Postgres schema.

---

## 2. Directory Structure

Top-level layout:

```text
swimbuddz-backend/
  ARCHITECTURE.md
  AGENT_INSTRUCTIONS.md
  CONVENTIONS.md
  API_CONTRACT.md
  TODO.md
  .env.example
  pyproject.toml or requirements.txt
  alembic/
  libs/
    common/
    db/
    auth/
  services/
    gateway_service/
    identity_service/
    members_service/
    sessions_service/
    attendance_service/
    communications_service/
    academy_service/
    payments_service/
  mcp/
    swimbuddz_core_mcp/
  tests/
```

### 2.1 Containerization & Isolation

- Every microservice (gateway + each domain service) must ship with its own Dockerfile that extends a shared Python base image. Do **not** run multiple services inside the same container.
- `docker-compose.yml` orchestrates local development. Each service has:
  - Dedicated env file (e.g., `.env.members`) layered on `.env`.
  - `restart: unless-stopped` policies so one crash doesn’t cascade.
  - Independent health checks/ports for debugging.
- Shared infrastructure (Postgres, Redis, etc.) runs as separate containers on the same compose network.
- When testing failure scenarios, use `docker compose stop <service>` to confirm other services stay healthy. New services must respect this isolation principle.

### 2.2 Shared Libraries

**`libs/common`**
- `config.py`: global settings powered by `pydantic.BaseSettings`.
- `logging.py`: centralized logging configuration.
- `exceptions.py`: optional shared exception types.

**`libs/db`**
- `config.py`: SQLAlchemy engine and session maker sourced from `DATABASE_URL`.
- `base.py`: declares the global `Base = declarative_base()` for ORM models.
- `session.py`: `get_db()` dependency for FastAPI routes.

**`libs/auth`**
- `dependencies.py`: shared auth helpers, including:
  - `AuthUser` model (user_id, email, role).
  - `get_current_user()` to decode Supabase JWTs.
  - Role guards such as `require_admin()`.

These libraries guarantee consistent auth, config, and DB wiring across services.

---

## 3. Domain Services

Each service follows the same internal layout:

```text
services/<service_name>/
  app/
    main.py          # FastAPI app entrypoint
    api/             # FastAPI routers
    models/          # SQLAlchemy models
    schemas/         # Pydantic schemas
    core/            # service-specific config/dependencies
    services/        # optional domain/service layer
    tests/           # unit tests
```

### 3.1 Identity Service (`services/identity_service/`)

- **Responsibilities**
  - Connect Supabase auth users to SwimBuddz members and roles.
  - Provide identity/role information to clients and other services.
- **Key Capabilities**
  - `GET /api/v1/identity/me` returns Supabase user ID, email, inferred role (member, admin, volunteer), and linked `member_id`.

### 3.2 Members Service (`services/members_service/`)

- **Responsibilities**
  - Store and manage member profiles, replacing the Google Form workflow.
  - Track `membership_status` (active, inactive, banned) and role metadata.
- **Key Capabilities**
  - Create member profiles during registration.
  - Retrieve and update the logged-in member profile.
  - Provide admin listing/filtering of members.

### 3.3 Sessions Service (`services/sessions_service/`)

- **Responsibilities**
  - Represent all SwimBuddz sessions/events: Yaba club, Sunfit, Federal Palace meetups, trips, camps, open water, scuba, etc.
- **Key Capabilities**
  - Create and edit sessions (admin).
  - Publish upcoming sessions.
  - Serve session details for the sign-in flow.

### 3.4 Attendance Service (`services/attendance_service/`)

- **Responsibilities**
  - Manage attendance for each session, including sign-ins, ride-share options (drivers/passengers), payment flags, and attendance history.
- **Key Capabilities**
  - Default 3-step sign-in experience: click link → confirm name → submit defaults.
  - Advanced options such as late arrival, early departure, and ride-share configuration (drivers specify seats).
  - Pool list export for administrators.
  - Member attendance history and statistics.

### 3.5 Communications Service (`services/communications_service/`)

- **Responsibilities**
  - Manage announcements and noticeboard posts (rain updates, schedule changes, events like Dermatologist Q&A, meetups, competitions).
- **Key Capabilities**
  - Admins create/edit announcements.
  - Clients retrieve a public list of recent announcements.

### 3.6 Academy Service (`services/academy_service/`)

- **Responsibilities**
  - Model the SwimBuddz Academy layer with structured programs, courses, and enrolments (scorecards/progress later).
- **Key Capabilities**
  - Create and list programs.
  - Enrol members into programs.
  - Provide admin visibility into enrolments.

### 3.7 Payments Service (`services/payments_service/`)

- **Responsibilities**
  - Track payment records for sessions, programs, and future offerings (plans, merchandise).
- **Key Capabilities**
  - Create payment records and manage lifecycle status (`pending`, `succeeded`, `failed`).
  - Integrate with gateways (Paystack/Flutterwave) via webhooks in future iterations.

---

## 4. Gateway Service (HTTP API Gateway / BFF)

**Path:** `services/gateway_service/`

- **Responsibilities**
  - Provide a single HTTP entrypoint for web/mobile clients.
  - Orchestrate data from multiple domain services into frontend-friendly responses.
- **Examples**
  - `GET /api/v1/me/dashboard`: combines identity, profile, attendance summary, and announcements.
  - `GET /api/v1/sessions/{id}/sign-in-view`: merges session details with any existing attendance record.
- **Design Notes**
  - Avoid heavy domain logic; validate requests, call other services (imports or internal HTTP), and shape responses for the frontend.

---

## 5. MCP Layer – `swimbuddz_core_mcp`

**Path:** `mcp/swimbuddz_core_mcp/`

- **Responsibilities**
  - Expose MCP tools aligned with key SwimBuddz actions.
  - Serve as the bridge between AI hosts (ChatGPT, Claude, etc.) and backend capabilities.
- **Tool Categories**
  - *Member*: `get_current_member_profile`, `update_member_profile`
  - *Sessions*: `list_upcoming_sessions`, `get_session_details`
  - *Attendance*: `sign_in_to_session`, `get_my_attendance_history`
  - *Communications*: `list_announcements`, `create_announcement` (admin-only)
- **Implementation Notes**
  - Tools call domain logic or gateway endpoints rather than reimplementing business rules.
  - The server remains intentionally thin—a translation layer from LLM requests to backend operations.

---

## 6. Data Flow Examples

### 6.1 Registration

1. User fills registration form (frontend).
2. Frontend calls `POST /api/v1/pending-registrations` (gateway → `members_service`) to store profile data.
3. Frontend triggers Supabase sign-up → User receives email confirmation link.
4. User clicks link → Supabase redirects to frontend `/auth/callback`.
5. Frontend calls `POST /api/v1/pending-registrations/complete` (gateway → `members_service`).
6. `members_service` creates the final `Member` record linked to the now-confirmed Supabase user.

### 6.2 Three-Step Session Sign-In

1. Admin shares a WhatsApp link: `https://app.swimbuddz.com/sessions/:id/sign-in`.
2. Member opens the link; frontend calls `GET /api/v1/sessions/{id}/sign-in-view` (gateway).
3. Member confirms attendance; frontend sends `POST /api/v1/sessions/{id}/sign-in` (gateway → `attendance_service`).
4. `attendance_service`:
   - Creates `SessionAttendance` with default time and no ride-share.
   - Computes `total_fee` (session fee + optional ride-share).
5. Member sees confirmation (status and payment reference).
6. Admin later confirms payments and exports pool list via `GET /api/v1/sessions/{id}/pool-list`.

### 6.3 MCP Tool Example – `sign_in_to_session`

1. AI agent receives intent: “Sign me up for Yaba this Saturday.”
2. Agent calls `list_upcoming_sessions` → selects the target session.
3. Agent invokes `sign_in_to_session` with `session_id`.
4. Tool handler calls backend domain logic/gateway to create attendance.
5. Handler returns success plus payment instructions to the host application.

---

## 7. Non-Goals

- Replacing HTTP APIs with MCP.
- Deep WhatsApp Business integration in the initial implementation.
- Advanced analytics dashboards (charts, long-term metrics) in the first version.

These features can be layered in later without changing the overall architecture.
