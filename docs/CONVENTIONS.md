# SwimBuddz Backend – Coding Conventions

These conventions exist so multiple contributors (including AI agents) can work consistently.

---

## 1. Languages & Versions

- **Python:** 3.11+
- **Framework:** FastAPI (async-first)
- **ORM:** SQLAlchemy 2.x (declarative)
- **Schema library:** Pydantic v2

We use **async FastAPI endpoints** plus SQLAlchemy 2.x's async ORM/session APIs. Always prefer `async def` routers and async-compatible DB sessions.

---

## 2. Project Structure

- Shared code:
  - `libs/common` – configuration, logging, shared helpers.
  - `libs/db` – DB engine, Base, session dependencies.
  - `libs/auth` – auth dependencies and Supabase JWT validation.

- Services:
  - `services/<service_name>/app/main.py` – FastAPI app entrypoint.
  - `services/<service_name>/app/api/` – routers.
  - `services/<service_name>/app/models/` – SQLAlchemy models.
  - `services/<service_name>/app/schemas/` – Pydantic schema models.
  - `services/<service_name>/app/core/` – service-specific config, shared dependencies.
  - `services/<service_name>/app/services/` – optional domain/service-layer functions.
  - `services/<service_name>/app/tests/` – tests for that service.

- MCP:
  - `mcp/swimbuddz_core_mcp/` – MCP server and tools.

### ⚠️ CRITICAL - Service Isolation Rules

**Services MUST NOT import code from other services. Services communicate ONLY via HTTP APIs.**

```python
# ❌ FORBIDDEN - Do NOT do this
from services.members_service.models import Member
from services.academy_service.schemas import ProgramRead

# ❌ FORBIDDEN - Do NOT query other services' tables
member = db.query(Member).filter(Member.id == member_id).first()

# ✅ CORRECT - Call other services via HTTP
import httpx

async def get_member_info(member_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{MEMBERS_SERVICE_URL}/api/v1/members/{member_id}")
        return response.json() if response.status_code == 200 else None
```

**See [docs/reference/SERVICE_COMMUNICATION.md](../docs/reference/SERVICE_COMMUNICATION.md) for complete patterns.**

---

## 3. Python Style

- Use type hints on all function signatures.
- Use `snake_case` for functions and variables.
- Use `PascalCase` for classes.
- Follow PEP8 for formatting (or equivalent enforced by tools like `ruff` or `black`).

Example:

```python
async def sign_in_to_session(session_id: UUID, member_id: UUID) -> SessionAttendance:
    ...
```

---

## 4. FastAPI Usage

- Each service ships an **async FastAPI app** inside `app/main.py`.
- Routers are grouped by domain (e.g. `routes_members.py`, `routes_sessions.py`).
- Always declare route handlers as `async def` even if the body is mostly CPU-bound—this keeps interfaces consistent and avoids sync/async pitfalls later.
- Dependencies:
  - `get_async_db()` (or equivalent) returns an async SQLAlchemy session.
  - `get_current_user()` / `require_admin()` enforce auth/roles.

Example:

```python
router = APIRouter()

@router.get("/me", response_model=MemberRead)
async def get_my_profile(
    db: AsyncSession = Depends(get_async_db),
    user: AuthUser = Depends(get_current_user),
) -> MemberRead:
    member = await members_service.get_member_for_user(db, user)
    return MemberRead.model_validate(member)
```

---

## 5. SQLAlchemy Conventions

- Use the declarative base from `libs.db.base.Base`.
- Prefer UUID primary keys for core entities.
- Keep enums for constrained values (e.g. `swimming_level`, `attendance_status`).
- Use SQLAlchemy 2.x async APIs (`async_sessionmaker`, `async with session.begin()`).

```python
class Member(Base):
    __tablename__ = "members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name = Column(String, nullable=False)
```

---

## 6. Pydantic v2 Usage

- Define schemas under `schemas/`.
- Set `model_config = ConfigDict(from_attributes=True)` (or `Config.from_attributes = True`).
- When applying partial updates, call `model_dump(exclude_unset=True)`.

```python
class MemberRead(BaseModel):
    id: UUID
    full_name: str
    email: EmailStr

    model_config = ConfigDict(from_attributes=True)
```

---

## 7. Error Handling and Responses

- Raise `HTTPException` with clear `status_code` and `detail`.
- Never leak raw exceptions to clients.
- Keep error messages concise and actionable.

```python
if existing_member:
    raise HTTPException(
        status_code=400,
        detail="Member already exists for this Supabase user.",
    )
```

---

## 8. Tests

- Testing stack: `pytest` + async fixtures (e.g. `pytest-asyncio`).
- Service-specific tests go under `services/<service_name>/app/tests/`.
- Cross-service/integration tests go under `tests/`.
- Use a separate test DB (see `TODO.md` for flows to cover).

---

## 9. Logging

- Import loggers from `libs/common/logging.py`.
- Log key lifecycle events (sign-ins, payment state changes, errors).
- Never log sensitive information; redact medical data and Supabase tokens.

---

## 10. MCP Tool Design

- Tools are thin wrappers around domain logic or gateway endpoints.
- Do **not** re-implement business rules.
- Name tools with lower_snake_case verbs (`sign_in_to_session`, `list_upcoming_sessions`).

---

## 11. API Stability

- Endpoints defined in `API_CONTRACT.md` are stable for this iteration.
- Avoid breaking changes; if unavoidable, add a new versioned path and document the migration plan.
