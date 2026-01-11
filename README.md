# SwimBuddz Backend

Monorepo for the SwimBuddz backend services and shared libraries.

- Python 3.11+
Backend monorepo for the SwimBuddz application, built with FastAPI, SQLAlchemy (Async), Pydantic v2, and PostgreSQL.

## Tech Stack

- **Framework**: FastAPI
- **Database**: PostgreSQL (Async via `psycopg3`)
- **ORM**: SQLAlchemy 2.0+
- **Migrations**: Alembic
- **Authentication**: Supabase (JWT)
- **Testing**: Pytest + Pytest-Asyncio
- **Linting**: Ruff

## Project Structure

```
swimbuddz-backend/
├── alembic/              # Database migrations
├── libs/                 # Shared libraries
│   ├── auth/             # Authentication helpers
│   ├── common/           # Config, logging
│   └── db/               # Database session/engine
├── mcp/                  # Model Context Protocol (MCP) Layer
│   └── swimbuddz_core_mcp/
├── services/             # Domain Services
│   ├── gateway_service/  # API Gateway (Entry point)
│   ├── members_service/  # Member management
│   ├── sessions_service/ # Session management
│   ├── attendance_service/ # Attendance tracking
│   ├── payments_service/ # Payment processing
│   ├── academy_service/ # Academy management
│   ├── events_service/ # Events management
│   ├── media_service/ # Media management
│   ├── transport_service/ # Transport management
│   ├── store_service/ # Store management
│   └── communications_service/ # Announcements
└── tests/                # Integration tests
```

## Architecture

**SwimBuddz** uses a **microservices architecture** where each domain service runs independently:

| Service | Port | Status | Purpose |
|---------|------|--------|---------|
| **Gateway Service** | 8000 | Production | API Gateway - Single entry point for all requests |
| **Members Service** | 8001 | Production | Member profiles and registration |
| **Sessions Service** | 8002 | Production | Session scheduling and management |
| **Attendance Service** | 8003 | Production | Session check-ins and tracking |
| **Communications Service** | 8004 | Production | Announcements and notifications |
| **Payments Service** | 8005 | Production | Payment processing and Paystack integration |
| **Academy Service** | 8006 | Production | Cohort-based programs and curriculum |
| **Events Service** | 8007 | Minimal | Community events (basic implementation) |
| **Media Service** | 8008 | Minimal | Photo/video galleries (basic implementation) |
| **Transport Service** | 8009 | Production | Ride-sharing and route management |
| **Store Service** | 8010 | Minimal | E-commerce platform (extensive models, basic routes) |

**Complete Service Details:** See [docs/reference/SERVICE_REGISTRY.md](../docs/reference/SERVICE_REGISTRY.md)

The Gateway proxies requests to the appropriate service via HTTP, allowing each service to be:
- Scaled independently
- Deployed independently
- Developed and tested in isolation

## Getting Started

### Prerequisites

- Docker & Docker Compose
- Python 3.11+ (for local dev without Docker)

### Running with Docker (Recommended)

1.  **Configure Environment**:
    Copy `.env.example` to `.env.dev` and fill in your Supabase credentials.
    ```bash
    cp .env.example .env.dev
    ```

2.  **Start All Services**:
    ```bash
    docker compose up --build
    ```

    This will start all services:
    - Gateway at `http://localhost:8000` (main entry point)
    - Individual services at ports 8001-8010

3.  **Run Migrations**:
    ```bash
    docker compose exec gateway alembic upgrade head
    ```

### Testing Individual Services

Each service exposes its own FastAPI docs during development:
- Gateway: `http://localhost:8000/docs`
- Members: `http://localhost:8001/docs`
- Sessions: `http://localhost:8002/docs`
- Attendance: `http://localhost:8003/docs`
- Communications: `http://localhost:8004/docs`
- Payments: `http://localhost:8005/docs`
- Academy: `http://localhost:8006/docs`
- Events: `http://localhost:8007/docs`
- Media: `http://localhost:8008/docs`
- Transport: `http://localhost:8009/docs`
- Store: `http://localhost:8010/docs`

### Local Development

1.  **Install Dependencies**:
    ```bash
    pip install -e ".[dev]"
    ```

2.  **Run Database**:
    You can run just the database via Docker:
    ```bash
    docker compose up -d db
    ```

3.  **Run Gateway**:
    ```bash
    python -m uvicorn services.gateway_service.app.main:app --reload
    ```

## Testing

We use `pytest` for testing. The test suite includes unit tests for each service and integration tests.

```bash
# Run all tests
pytest

# Run tests for a specific service
pytest services/members_service/app/tests/
```

## MCP Server

The project includes a Model Context Protocol (MCP) server to expose backend functionality to AI agents.

- **Location**: `mcp/swimbuddz_core_mcp/`
- **Tools**:
    - `get_current_member_profile`
    - `list_upcoming_sessions`
    - `sign_in_to_session`
    - ... and more.

## CI/CD

GitHub Actions is configured to run on every push to `main`:
- **Linting**: `ruff check .`
- **Testing**: `pytest` (with a service container for Postgres)
