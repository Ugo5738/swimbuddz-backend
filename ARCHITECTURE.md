# SwimBuddz Backend – Architecture

This document describes the **overall backend architecture** for the SwimBuddz platform.

The goals are:

- A clear, **domain-based** backend design.
- A single, stable **HTTP API** that powers web/mobile apps and partner integrations.
- A **Model Context Protocol (MCP) layer** that exposes backend capabilities as tools for AI agents.
- A structure that can scale beyond Nigeria/Africa without major rewrites.

---

## 1. High-Level Overview

The backend uses a **microservices architecture** where each domain service runs as an independent process:

1. **Shared libraries (`libs/`)** – common configuration, database access, and authentication helpers.
2. **Domain services (`services/`)** – each runs as a standalone FastAPI application:
   - `members_service` (Port 8001): member registration and profiles.
   - `sessions_service` (Port 8002): all swim sessions and events.
   - `attendance_service` (Port 8003): session sign-ins, ride-share, pool lists.
   - `communications_service` (Port 8004): announcements / noticeboard.
   - `payments_service` (Port 8005): payment records and status.
   - `academy_service` (Port 8006): cohort-based programs and curriculum.
   - `events_service` (Port 8007): community events (basic implementation).
   - `media_service` (Port 8008): photo/video galleries (basic implementation).
   - `transport_service` (Port 8009): ride-sharing and route management.
   - `store_service` (Port 8010): e-commerce platform (extensive models, basic routes).
   - `gateway_service` (Port 8000): **API Gateway** that proxies requests to domain services via HTTP.
3. **MCP server (`mcp/`)** – `swimbuddz_core_mcp` exposes high-level tools to AI agents.
4. **Migrations (`alembic/`)** – Alembic manages the Postgres schema.

### Architecture Pattern

The **Gateway Service** acts as the single entry point for clients and **proxies HTTP requests** to the appropriate domain service. This provides:

- **Independent scaling** - Scale high-traffic services separately
- **Fault isolation** - Service failures don't crash the entire system
- **Independent deployment** - Deploy services separately
- **Clear boundaries** - Enforced separation of concerns

Each domain service is a complete FastAPI application with its own endpoints, models, and business logic. Services communicate via HTTP when needed, typically through the Gateway.

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
    events_service/
    media_service/
    transport_service/
    store_service/
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

### Service Overview

| Service | Port | Status | Key Models | Frontend Integration |
|---------|------|--------|------------|---------------------|
| gateway_service | 8000 | Production | - | All routes |
| members_service | 8001 | Production | Member, PendingRegistration | `/account/profile`, `/admin/members` |
| sessions_service | 8002 | Production | Session | `/sessions`, `/admin/sessions` |
| attendance_service | 8003 | Production | SessionAttendance | `/sessions/[id]/sign-in`, `/account/attendance` |
| communications_service | 8004 | Production | Announcement | `/announcements` |
| payments_service | 8005 | Production | PaymentRecord, PaymentIntent | `/checkout`, `/account/billing` |
| academy_service | 8006 | Production | Program, Cohort, Enrollment, Progress | `/academy/*`, `/account/academy/*`, `/admin/academy/*` |
| events_service | 8007 | Minimal | Event, EventRSVP | `/community/events/*` |
| media_service | 8008 | Minimal | MediaItem, Album, Gallery | `/gallery/*` |
| transport_service | 8009 | Production | RideArea, PickupLocation, RideBooking | `/admin/transport/*` |
| store_service | 8010 | Minimal | Product, Order, Cart, Inventory | `/store/*`, `/admin/store/*` |
| identity_service | N/A | **Not Implemented** | - | N/A |

**Complete Service Registry:** See [docs/reference/SERVICE_REGISTRY.md](../../docs/reference/SERVICE_REGISTRY.md)

### 3.1 Members Service (`services/members_service/`) - Port 8001

- **Responsibilities**
  - Store and manage member profiles, replacing the Google Form workflow.
  - Track `membership_status` (active, inactive, banned) and role metadata.
- **Key Capabilities**
  - Create member profiles during registration.
  - Retrieve and update the logged-in member profile.
  - Provide admin listing/filtering of members.

### 3.2 Sessions Service (`services/sessions_service/`) - Port 8002

- **Responsibilities**
  - Represent all SwimBuddz sessions/events: Yaba club, Sunfit, Federal Palace meetups, trips, camps, open water, scuba, etc.
- **Key Capabilities**
  - Create and edit sessions (admin).
  - Publish upcoming sessions.
  - Serve session details for the sign-in flow.

### 3.3 Attendance Service (`services/attendance_service/`) - Port 8003

- **Responsibilities**
  - Manage attendance for each session, including sign-ins, ride-share options (drivers/passengers), payment flags, and attendance history.
- **Key Capabilities**
  - Default 3-step sign-in experience: click link → confirm name → submit defaults.
  - Advanced options such as late arrival, early departure, and ride-share configuration (drivers specify seats).
  - Pool list export for administrators.
  - Member attendance history and statistics.

### 3.4 Communications Service (`services/communications_service/`) - Port 8004

- **Responsibilities**
  - Manage announcements and noticeboard posts (rain updates, schedule changes, events like Dermatologist Q&A, meetups, competitions).
- **Key Capabilities**
  - Admins create/edit announcements.
  - Clients retrieve a public list of recent announcements.

### 3.5 Payments Service (`services/payments_service/`) - Port 8005

- **Responsibilities**
  - Track payment records for sessions, programs, and merchandise.
  - Integrate with Paystack for payment processing.
- **Key Capabilities**
  - Create payment intents and manage Paystack checkouts.
  - Handle payment webhooks for automated verification.
  - Manual payment verification for admin overrides.
  - Link payments to sessions, cohorts, or store orders.

### 3.6 Academy Service (`services/academy_service/`) - Port 8006 ⭐

- **Responsibilities**
  - Complete cohort-based learning system with programs, curriculum, enrollments, and progress tracking.
- **Status:** **Production-ready** - Fully implemented backend with minor operational gaps.
- **Key Models:** Program, ProgramCurriculum, Cohort, Enrollment, Milestone, StudentProgress (20,332 lines total)
- **Key Capabilities**
  - Create and manage academy programs with structured curriculum.
  - Cohort management with enrollment workflows.
  - Student progress tracking with milestone assessment.
  - Payment integration for program fees.
  - 33+ API endpoints for complete academy operations.
- **Known Gaps:** Coach dashboard, capacity enforcement, waitlist automation (see [ACADEMY_REVIEW.md](../../docs/ACADEMY_REVIEW.md))

### 3.7 Events Service (`services/events_service/`) - Port 8007

- **Responsibilities**
  - Community events distinct from recurring sessions (one-off meets, trips, camps).
- **Status:** Minimal implementation - basic models and routes only.
- **Key Models:** Event, EventRSVP

### 3.8 Media Service (`services/media_service/`) - Port 8008

- **Responsibilities**
  - Photo and video management, gallery creation, site asset storage.
- **Status:** Minimal implementation - basic models and routes only.
- **Key Models:** MediaItem, Album, Gallery, SiteAsset

### 3.9 Transport Service (`services/transport_service/`) - Port 8009

- **Responsibilities**
  - Ride-sharing system for session transportation with pickup locations and route management.
- **Status:** Production - Complete implementation (6,229 lines of models).
- **Key Models:** RideArea, PickupLocation, RouteInfo, RideBooking

### 3.10 Store Service (`services/store_service/`) - Port 8010

- **Responsibilities**
  - E-commerce platform for swim gear, merchandise, and equipment sales.
- **Status:** Minimal routes - Extensive models (998 lines) with basic CRUD endpoints.
- **Key Models:** Product, ProductVariant, Order, OrderItem, Cart, Inventory
- **Architecture Docs:** [STORE_ARCHITECTURE.md](../../docs/STORE_ARCHITECTURE.md)

### 3.11 Identity Service - **Not Implemented**

This service exists as an empty directory but has no implementation. Authentication is handled via Supabase JWT validation in `libs/auth/dependencies.py`. If identity aggregation or advanced RBAC is needed, this service can be implemented in the future.

---

## 4. Gateway Service (HTTP API Gateway)

**Path:** `services/gateway_service/`
**Port:** 8000

- **Responsibilities**
  - Provide a single HTTP entrypoint for web/mobile clients.
  - **Proxy requests** to domain services via HTTP.
  - Orchestrate data from multiple services into frontend-friendly responses (e.g., dashboard).
- **Implementation**
  - Uses HTTP clients to forward requests to domain services.
  - Services discovered via Docker Compose network (e.g., `http://members-service:8001`).
  - Dashboard endpoints aggregate data from multiple services using direct database queries for efficiency.
- **Examples**
  - `GET /api/v1/members/*` → proxies to Members Service (8001)
  - `GET /api/v1/sessions/*` → proxies to Sessions Service (8002)
  - `GET /api/v1/me/dashboard` → aggregates from multiple sources
- **Design Notes**
  - Stateless proxy pattern for most endpoints.
  - Dashboard uses direct DB access for performance (queries from multiple tables).
  - All authentication/authorization handled by individual services.

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
