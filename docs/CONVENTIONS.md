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

---

## 12. File Size Limits

Large files hurt review velocity, IDE responsiveness, AI assistance, and test isolation. We aim for files that fit on one screen end-to-end and split anything beyond that into focused modules.

**Targets (whole-file line counts):**

| File kind | Soft target | Hard cap |
|---|---:|---:|
| Router (`services/*/routers/*.py`) | 500 | 800 |
| Model (`services/*/models/*.py`) | 400 | 600 |
| Schema (`services/*/schemas/*.py`) | 500 | 800 |
| Service / domain logic (`services/*/services/*.py`) | 500 | 800 |
| Shared library (`libs/**/*.py`, `mcp/**/*.py`) | 600 | 1000 |

**Excluded from these limits** (do not flag, do not split):

- Alembic migrations (`services/*/alembic/versions/`) — generated.
- Seed data (`scripts/seed/*.py`) — data, not logic.
- Email / notification templates (`services/*/templates/*.py`) — copy-heavy.
- Tests — split by what they cover, not by line count.

**How to split when you hit the cap:**

- **Routers** — split by sub-resource and `include_router` from `app/main.py`. Example: `routers/payments/intents.py`, `routers/payments/webhooks.py`, `routers/payments/refunds.py` rather than one 2,000-line `intents.py`.
- **Models** — split by aggregate root (`models/enrollment.py`, `models/cohort.py`) and re-export from `models/__init__.py`.
- **Schemas** — split alongside the matching model file (`schemas/enrollment.py` mirrors `models/enrollment.py`).
- **Shared libraries** — split by responsibility; if `service_client.py` is 1,000+ lines, it's doing too many things.

### 12.1 Split patterns — pick one, document why

Two layouts are both acceptable; pick by directory context.

**Pattern A — sibling modules in an existing directory.** Use when the parent dir already aggregates several files via its own `__init__.py` (e.g. `services/*/schemas/`, `services/*/routers/` with multiple existing routers). Add new sibling files for the split content; if the original file was a re-export hub (e.g. `schemas/main.py`), keep it as a thin re-export shim so external import paths don't break.

Example: `services/academy_service/schemas/main.py` (965 lines) → 10 sibling files (`program.py`, `cohort.py`, …) + a 159-line `main.py` shim that re-exports everything. `services/academy_service/schemas/__init__.py` still imports from `.main`.

**Pattern B — convert the file to a package directory of the same name.** Use when the original file is a self-contained unit, exports more than one top-level router, or its split produces ≥4 tightly cohesive submodules that benefit from namespace isolation. The original `<name>.py` is replaced by `<name>/__init__.py` which exposes the same public names.

Example: `services/members_service/routers/admin.py` (804 lines) → `services/members_service/routers/admin/` package with 6 submodules + `__init__.py` exposing `router`. The existing `from services.members_service.routers.admin import router as admin_router` keeps working.

Both patterns require that the public surface (every name any caller imports) survives unchanged. Verify with the four-step ritual in §12.3.

### 12.2 Internal naming + structure (applies to both patterns)

- **Private files** start with `_`: `_shared.py` (helpers), `_schemas.py` (Pydantic shapes used by multiple submodules), `_helpers.py` (pure functions + constants), `_constants.py` (literals only), `_milestones.py` etc. for narrowly-scoped private modules.
- **Sub-routers** are declared as `router = APIRouter()` **without a prefix**. The aggregator's `__init__.py` declares the prefixed router (`router = APIRouter(prefix="/coaches", ...)`) and calls `router.include_router(_submodule.router)` once per sub-router.
- **Aggregator imports** use the `from . import submodule as _submodule` pattern so cross-submodule name collisions (e.g. multiple `router` exports) don't bleed into the package namespace.
- **Cross-submodule imports** use relative paths: `from ._shared import X`, `from ._schemas import Y`. External imports remain absolute.
- **Route ordering matters** when sub-routers carry routes that could match the same path under different segment counts. If any sub-router has a `/{member_id}` catch-all, sub-routers with static-prefix routes (`/active`, `/search`, etc.) MUST be `include_router`'d first. Document this in the aggregator's docstring (see `routers/internal/__init__.py` for an example).
- **`__all__`** in the aggregator lists exactly what callers can import. For schema shims, list every re-exported class. For router packages, list `router` (and `admin_router` if applicable).

### 12.3 Verification ritual (do all four, in order, every time)

1. **AST byte-equality** of every top-level function/class against `git show HEAD:<original-path>`. Any divergence beyond cosmetic Unicode escaping is a bug; investigate before continuing.
2. **`python3 -m py_compile`** every new file. Catches indent and import-name typos that AST equality can't.
3. **`docker compose restart <service> gateway`** — wait for `Application startup complete` in the logs. A boot failure here means a runtime import or initialization bug.
4. **Integration tests + endpoint smoke** for the affected service. Hit at least one endpoint per public sub-router via the gateway to confirm route registration and prefix wiring. Compare OpenAPI route count before/after — should be exactly equal.

**Enforcement:**

```bash
bash scripts/lint/check_file_sizes.sh
```

Prints every file in violation, classified `[soft]` or `[HARD]`. The script is non-blocking (always exits 0); treat results as a backlog. Once the hard-cap list is empty, wire the script into CI with `exit 1` on hard violations.
