# SwimBuddz Backend

Monorepo for the SwimBuddz backend services and shared libraries.

- Python 3.11+
Backend monorepo for the SwimBuddz application, built with FastAPI, SQLAlchemy (Async), Pydantic v2, and PostgreSQL.

## Tech Stack

- **Framework**: FastAPI
- **Database**: PostgreSQL (Async via `asyncpg`)
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
│   └── communications_service/ # Announcements
└── tests/                # Integration tests
```

## Getting Started

### Prerequisites

- Docker & Docker Compose
- Python 3.11+ (for local dev without Docker)

### Running with Docker (Recommended)

1.  **Configure Environment**:
    Copy `.env.example` to `.env` and fill in your Supabase credentials.
    ```bash
    cp .env.example .env
    ```

2.  **Start Services**:
    ```bash
    docker compose up --build
    ```
    The Gateway API will be available at `http://localhost:8000`.

3.  **Run Migrations**:
    ```bash
    docker compose exec gateway alembic upgrade head
    ```

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
