# Backend Service Restructuring Plan

> **Status:** Not started
> **Goal:** Standardise all backend services to follow the idiomatic FastAPI package-per-layer convention.
> **Rule of thumb:** Any layer file > ~300 lines becomes a directory with one file per entity/feature.

---

## Target Structure

### Complex services (academy, members, communications, payments, store, volunteer)

```
service_name/
├── __init__.py
├── main.py                       # FastAPI app factory (moved from app/main.py)
├── models/                       # SQLAlchemy models — one file per entity
│   ├── __init__.py               # Re-exports all models
│   ├── cohort.py
│   └── enrollment.py
├── schemas/                      # Pydantic schemas — mirrors models structure
│   ├── __init__.py
│   ├── cohort.py
│   └── enrollment.py
├── routers/                      # Route handlers — thin, delegate to services/
│   ├── __init__.py               # Assembles and re-exports the top-level router
│   ├── cohorts.py
│   ├── enrollments.py
│   └── internal.py
├── services/                     # Business logic layer
│   ├── __init__.py
│   └── cohort_service.py
├── clients/                      # External API integrations (if needed)
│   ├── __init__.py
│   └── paystack.py
├── tasks.py                      # Background/async task definitions (if needed)
├── worker.py                     # ARQ worker settings (if needed)
├── dependencies.py               # Shared FastAPI Depends() callables (if needed)
├── exceptions.py                 # Custom domain exceptions (if needed)
├── seed.py                       # Seed data script (if needed)
├── alembic/
│   ├── env.py
│   └── versions/
├── alembic.ini
├── Dockerfile
└── tests/
    ├── __init__.py
    ├── conftest.py
    └── test_cohorts.py
```

### Simple services (events, transport, media, attendance, sessions, ai)

```
service_name/
├── __init__.py
├── main.py                       # FastAPI app factory
├── models.py                     # Single file (< 300 lines)
├── schemas.py                    # Single file
├── router.py                     # Single file (or routers/ if > 300 lines)
├── internal_router.py            # If service has internal endpoints
├── alembic/
│   ├── env.py
│   └── versions/
├── alembic.ini
├── Dockerfile
└── tests/
    ├── __init__.py
    └── test_api.py
```

### Gateway service (special — no database)

```
gateway_service/
├── __init__.py
├── main.py                       # The full gateway app (currently 486 lines in app/main.py)
├── clients.py                    # HTTP client pool for downstream services
├── routers/
│   ├── __init__.py
│   ├── cleanup.py
│   └── dashboard.py
├── Dockerfile
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── stubs.py
    ├── test_cleanup.py
    ├── test_dashboard.py
    └── test_gateway_proxy.py
```

---

## What Changes Globally

### 1. Kill the `app/` wrapper

Every service currently has `app/main.py` as the entrypoint. Move it to `main.py` at the service root.

**Affected files for each service:**

- `services/<svc>/app/main.py` → `services/<svc>/main.py`
- `docker-compose.yml`: change `uvicorn services.<svc>.app.main:app` → `uvicorn services.<svc>.main:app`
- `Dockerfile CMD`: same change
- Worker commands stay as-is (`arq services.<svc>.worker.WorkerSettings`)
- Delete empty `app/` stub directories (`app/api/`, `app/core/`, `app/models/`, `app/schemas/`, `app/services/`)

### 2. Move tests from `app/tests/` to `tests/`

Currently tests live at `services/<svc>/app/tests/`. Move to `services/<svc>/tests/`.

### 3. Update all alembic env.py imports

When `models.py` becomes `models/`, the alembic env.py import changes from:

```python
from services.<svc>.models import Model1, Model2
```

to:

```python
from services.<svc>.models import Model1, Model2  # same — __init__.py re-exports
```

No actual change needed if `models/__init__.py` re-exports everything.

### 4. Update cross-service imports

Any file that does `from services.<svc>.models import ...` or `from services.<svc>.router import ...` keeps working as long as `__init__.py` re-exports correctly. The `service_client.py` helpers don't import models, so they're unaffected.

---

## Per-Service Tasks

### Phase 1: Foundation — Move `app/main.py` and tests (ALL services)

This is the simplest, most impactful change. Do it for every service at once.

- [ ] **1.1** For each service: move `app/main.py` → `main.py` (merge content if needed)
- [ ] **1.2** Delete empty stub directories inside `app/` (`api/`, `core/`, `models/`, `schemas/`, `services/`)
- [ ] **1.3** Delete `app/` directory (now empty except maybe `__init__.py`)
- [ ] **1.4** Move `app/tests/` → `tests/` for services that have tests (members, sessions, attendance)
- [ ] **1.5** Update `docker-compose.yml` — change all `uvicorn services.<svc>.app.main:app` → `uvicorn services.<svc>.main:app`
- [ ] **1.6** Update every `Dockerfile` CMD line with same path change
- [ ] **1.7** Update any test imports that reference `services.<svc>.app.main`
- [ ] **1.8** Verify all services start correctly

**Services affected:** ALL 13 active services
**Risk:** Low — straightforward path change, one command to verify per service

---

### Phase 2: Academy Service (largest, most complex)

**Current state:**

- `models.py` — 900 lines
- `router.py` — 3,183 lines (!!!)
- `schemas.py` — 613 lines
- `coach_assignment_router.py` — 655 lines
- `coach_assignment_schemas.py` — 146 lines
- `curriculum_router.py` — 792 lines
- `curriculum_schemas.py` — 152 lines
- `self_enroll_schema.py` — 9 lines
- `scoring.py` — 267 lines
- `tasks.py` — 736 lines
- `worker.py` — 122 lines

**Target:**

```
academy_service/
├── main.py
├── models/
│   ├── __init__.py             # re-exports all
│   ├── program.py              # Program, ProgramInterest, ProgramCurriculum
│   ├── cohort.py               # Cohort, CohortResource, CohortComplexityScore
│   ├── enrollment.py           # Enrollment, StudentProgress
│   ├── milestone.py            # Milestone
│   ├── coach_assignment.py     # CoachAssignment, ShadowEvaluation
│   ├── curriculum.py           # CurriculumWeek, CurriculumLesson, Skill, LessonSkill
│   └── enums.py                # All enums: ProgramCategory, CoachGrade, CohortStatus, etc.
├── schemas/
│   ├── __init__.py             # re-exports all
│   ├── program.py
│   ├── cohort.py
│   ├── enrollment.py
│   ├── scoring.py              # Complexity scoring + AI scoring schemas
│   ├── coach_assignment.py     # (from coach_assignment_schemas.py)
│   └── curriculum.py           # (from curriculum_schemas.py)
├── routers/
│   ├── __init__.py             # assembles all sub-routers
│   ├── programs.py             # Program CRUD
│   ├── cohorts.py              # Cohort CRUD + status transitions
│   ├── enrollments.py          # Enrollment CRUD + self-enroll
│   ├── milestones.py           # Milestone + progress tracking
│   ├── scoring.py              # Complexity scoring + AI scoring endpoints
│   ├── coach_assignments.py    # (from coach_assignment_router.py)
│   ├── curriculum.py           # (from curriculum_router.py)
│   ├── coach_dashboard.py      # Coach-specific views
│   └── internal.py             # Internal service-to-service endpoints (if any)
├── services/
│   ├── __init__.py
│   └── scoring.py              # (from scoring.py — calculation logic)
├── tasks.py                    # keep as-is (async task definitions)
├── worker.py                   # keep as-is
├── alembic/
├── Dockerfile
└── tests/
```

**Steps:**

- [ ] **2.1** Split `models.py` (900 lines) → `models/` directory
- [ ] **2.2** Split `schemas.py` (613 lines) + merge `coach_assignment_schemas.py`, `curriculum_schemas.py`, `self_enroll_schema.py` → `schemas/` directory
- [ ] **2.3** Split `router.py` (3,183 lines) → `routers/` directory (programs, cohorts, enrollments, milestones, scoring, coach_dashboard)
- [ ] **2.4** Move `coach_assignment_router.py` → `routers/coach_assignments.py`
- [ ] **2.5** Move `curriculum_router.py` → `routers/curriculum.py`
- [ ] **2.6** Move `scoring.py` → `services/scoring.py` (business logic, not a router)
- [ ] **2.7** Create `routers/__init__.py` that assembles all sub-routers into one
- [ ] **2.8** Create `models/__init__.py` and `schemas/__init__.py` with re-exports
- [ ] **2.9** Update `main.py` imports
- [ ] **2.10** Update `alembic/env.py` imports
- [ ] **2.11** Update `tasks.py` imports
- [ ] **2.12** Delete old top-level files
- [ ] **2.13** Run tests, verify service starts

---

### Phase 3: Members Service

**Current state:**

- `models.py` — 1,002 lines
- `schemas.py` — 518 lines
- `router.py` — 22 lines (just imports from `routers/`)
- `routers/` — already split (2,119 lines across 6 files) ✅
- `coach_router.py` — 2,153 lines (!!!)
- `coach_schemas.py` — (exists, need to check size)
- `volunteer_router.py` — 491 lines
- `volunteer_schemas.py` — (exists, need to check size)
- `service.py` — 242 lines

**Target:**

```
members_service/
├── main.py
├── models/
│   ├── __init__.py
│   ├── member.py
│   ├── coach_profile.py
│   ├── coach_agreement.py
│   └── enums.py
├── schemas/
│   ├── __init__.py
│   ├── member.py
│   ├── coach.py                # (from coach_schemas.py)
│   └── volunteer.py            # (from volunteer_schemas.py)
├── routers/                    # Already partially done ✅
│   ├── __init__.py             # update to include coach + volunteer
│   ├── members.py              # ✅ exists
│   ├── registration.py         # ✅ exists
│   ├── admin.py                # ✅ exists
│   ├── internal.py             # ✅ exists
│   ├── coaches.py              # ✅ exists (60 lines) — BUT coach_router.py (2,153 lines) is separate!
│   └── volunteers.py           # (from volunteer_router.py)
├── services/
│   ├── __init__.py
│   └── member_service.py       # (from service.py)
├── alembic/
├── Dockerfile
└── tests/
```

**Steps:**

- [ ] **3.1** Split `models.py` (1,002 lines) → `models/` directory
- [ ] **3.2** Split/merge schemas → `schemas/` directory
- [ ] **3.3** Merge `coach_router.py` (2,153 lines) into `routers/coaches.py` — this is the big one; the current `routers/coaches.py` is 60 lines while the top-level `coach_router.py` has 2,153 lines. Split by domain: profiles, agreements, payouts
- [ ] **3.4** Move `volunteer_router.py` → `routers/volunteers.py`
- [ ] **3.5** Rename `service.py` → `services/member_service.py`
- [ ] **3.6** Delete old top-level files, update `routers/__init__.py`
- [ ] **3.7** Update `main.py` and `alembic/env.py` imports
- [ ] **3.8** Delete the now-empty `router.py` (22-line stub)
- [ ] **3.9** Run tests, verify service starts

---

### Phase 4: Payments Service

**Current state:**

- `models.py` — 286 lines (keep as single file)
- `schemas.py` — 165 lines (keep as single file)
- `router.py` — 1,835 lines
- `payout_router.py` — 486 lines
- `payout_schemas.py` — 104 lines
- `paystack_client.py` — 333 lines

**Target:**

```
payments_service/
├── main.py
├── models.py                   # 286 lines — fine as single file
├── schemas/
│   ├── __init__.py
│   ├── payment.py              # (from schemas.py)
│   └── payout.py               # (from payout_schemas.py)
├── routers/
│   ├── __init__.py
│   ├── payments.py             # Split from router.py — payment CRUD, webhooks
│   ├── invoices.py             # Split from router.py — invoice endpoints
│   ├── admin.py                # Split from router.py — admin reports/stats
│   └── payouts.py              # (from payout_router.py)
├── clients/
│   ├── __init__.py
│   └── paystack.py             # (from paystack_client.py)
├── alembic/
├── Dockerfile
└── tests/
```

**Steps:**

- [ ] **4.1** Split `router.py` (1,835 lines) → `routers/` directory
- [ ] **4.2** Move `payout_router.py` → `routers/payouts.py`
- [ ] **4.3** Merge `payout_schemas.py` into `schemas/payout.py`
- [ ] **4.4** Move `paystack_client.py` → `clients/paystack.py`
- [ ] **4.5** Update `main.py` and `alembic/env.py` imports
- [ ] **4.6** Run tests, verify service starts

---

### Phase 5: Communications Service

**Current state:**

- `models.py` — 445 lines
- `schemas.py` — 318 lines
- `router.py` — 1,074 lines
- `email_router.py` — 409 lines
- `messaging_router.py` — 316 lines
- `preferences_router.py` — 152 lines
- `tasks.py` — 693 lines
- `worker.py` — 62 lines
- `templates/` — already well-organised ✅ (2,521 lines across 9 files)

**Target:**

```
communications_service/
├── main.py
├── models.py                   # 445 lines — borderline, keep as single file for now
├── schemas.py                  # 318 lines — keep as single file
├── routers/
│   ├── __init__.py
│   ├── announcements.py        # Split from router.py
│   ├── notifications.py        # Split from router.py
│   ├── email.py                # (from email_router.py)
│   ├── messaging.py            # (from messaging_router.py)
│   └── preferences.py          # (from preferences_router.py)
├── templates/                  # ✅ Already well-organised — keep as-is
├── tasks.py
├── worker.py
├── alembic/
├── Dockerfile
└── tests/
```

**Steps:**

- [ ] **5.1** Split `router.py` (1,074 lines) → `routers/` directory
- [ ] **5.2** Move `email_router.py` → `routers/email.py`
- [ ] **5.3** Move `messaging_router.py` → `routers/messaging.py`
- [ ] **5.4** Move `preferences_router.py` → `routers/preferences.py`
- [ ] **5.5** Update `main.py` imports
- [ ] **5.6** Run tests, verify service starts

---

### Phase 6: Store Service

**Current state:**

- `models.py` — 998 lines
- `schemas.py` — 517 lines
- `router.py` — 979 lines
- `admin_router.py` — 1,262 lines
- `seed_store_data.py` — 439 lines

**Target:**

```
store_service/
├── main.py
├── models/
│   ├── __init__.py
│   ├── product.py
│   ├── order.py
│   ├── inventory.py
│   └── enums.py
├── schemas/
│   ├── __init__.py
│   ├── product.py
│   └── order.py
├── routers/
│   ├── __init__.py
│   ├── products.py             # (from router.py — public product endpoints)
│   ├── orders.py               # (from router.py — order endpoints)
│   ├── cart.py                  # (from router.py — cart endpoints)
│   └── admin.py                # (from admin_router.py)
├── seed.py                     # (from seed_store_data.py)
├── alembic/
├── Dockerfile
└── tests/
```

**Steps:**

- [ ] **6.1** Split `models.py` (998 lines) → `models/` directory
- [ ] **6.2** Split `schemas.py` (517 lines) → `schemas/` directory
- [ ] **6.3** Split `router.py` (979 lines) + move `admin_router.py` → `routers/` directory
- [ ] **6.4** Rename `seed_store_data.py` → `seed.py`
- [ ] **6.5** Update `main.py` and `alembic/env.py` imports
- [ ] **6.6** Run tests, verify service starts

---

### Phase 7: Volunteer Service

**Current state:**

- `models.py` — 429 lines
- `schemas.py` — 369 lines
- `router.py` — 768 lines
- `admin_router.py` — 902 lines
- `services.py` — 205 lines

**Target:**

```
volunteer_service/
├── main.py
├── models.py                   # 429 lines — borderline, keep single file
├── schemas.py                  # 369 lines — borderline, keep single file
├── routers/
│   ├── __init__.py
│   ├── volunteers.py           # (from router.py)
│   └── admin.py                # (from admin_router.py)
├── services/
│   ├── __init__.py
│   └── volunteer_service.py    # (from services.py)
├── alembic/
├── Dockerfile
└── tests/
```

**Steps:**

- [ ] **7.1** Split `router.py` (768 lines) + `admin_router.py` (902 lines) → `routers/` directory
- [ ] **7.2** Move `services.py` → `services/volunteer_service.py`
- [ ] **7.3** Update `main.py` imports
- [ ] **7.4** Run tests, verify service starts

---

### Phase 8: Media Service

**Current state:**

- `models.py` — 242 lines (keep)
- `schemas.py` — 161 lines (keep)
- `router.py` — 1,038 lines
- `storage.py` — 276 lines

**Target:**

```
media_service/
├── main.py
├── models.py
├── schemas.py
├── routers/
│   ├── __init__.py
│   ├── media.py                # Public media endpoints
│   └── admin.py                # Admin upload/management
├── clients/
│   ├── __init__.py
│   └── storage.py              # (from storage.py)
├── alembic/
├── Dockerfile
└── tests/
```

**Steps:**

- [ ] **8.1** Split `router.py` (1,038 lines) → `routers/` directory
- [ ] **8.2** Move `storage.py` → `clients/storage.py`
- [ ] **8.3** Update `main.py` imports
- [ ] **8.4** Run tests, verify service starts

---

### Phase 9: Transport Service

**Current state:**

- `models.py` — 184 lines (keep)
- `router.py` — 900 lines
- No `schemas.py` (schemas inline in router or not present)

**Target:**

```
transport_service/
├── main.py
├── models.py
├── schemas.py                  # Extract from router.py if inline, or create
├── routers/
│   ├── __init__.py
│   ├── routes.py               # Route management
│   ├── rides.py                # Ride-sharing
│   └── locations.py            # Pickup locations
├── alembic/
├── Dockerfile
└── tests/
```

**Steps:**

- [ ] **9.1** Extract schemas from `router.py` if they're inline → `schemas.py`
- [ ] **9.2** Split `router.py` (900 lines) → `routers/` directory
- [ ] **9.3** Update `main.py` imports
- [ ] **9.4** Run tests, verify service starts

---

### Phase 10: Sessions Service

**Current state:**

- `models.py` — 243 lines (keep)
- `schemas.py` — 83 lines (keep)
- `router.py` — 467 lines (borderline — keep or split)
- `template_router.py` — 228 lines
- `template_schemas.py` — 57 lines
- `internal_router.py` — 198 lines

**Target:**

```
sessions_service/
├── main.py
├── models.py
├── schemas.py                  # Merge template_schemas.py into here
├── routers/
│   ├── __init__.py
│   ├── sessions.py             # (from router.py)
│   ├── templates.py            # (from template_router.py)
│   └── internal.py             # (from internal_router.py)
├── alembic/
├── Dockerfile
└── tests/
```

**Steps:**

- [ ] **10.1** Move `router.py`, `template_router.py`, `internal_router.py` → `routers/` directory
- [ ] **10.2** Merge `template_schemas.py` into `schemas.py`
- [ ] **10.3** Create `routers/__init__.py` that assembles all sub-routers
- [ ] **10.4** Update `main.py` imports
- [ ] **10.5** Run tests, verify service starts

---

### Phase 11: Attendance Service

**Current state:**

- `models.py` — 87 lines (keep)
- `schemas.py` — 70 lines (keep)
- `router.py` — 398 lines (keep as single file)
- `internal_router.py` — 92 lines
- `seed_locations.py` — 103 lines

**Target:**

```
attendance_service/
├── main.py
├── models.py
├── schemas.py
├── routers/
│   ├── __init__.py
│   ├── attendance.py           # (from router.py)
│   └── internal.py             # (from internal_router.py)
├── seed.py                     # (from seed_locations.py)
├── alembic/
├── Dockerfile
└── tests/
```

**Steps:**

- [ ] **11.1** Move `router.py` → `routers/attendance.py` and `internal_router.py` → `routers/internal.py`
- [ ] **11.2** Rename `seed_locations.py` → `seed.py`
- [ ] **11.3** Update `main.py` imports
- [ ] **11.4** Run tests, verify service starts

---

### Phase 12: Events Service (minimal)

**Current state:**

- `models.py` — 82 lines (keep)
- `schemas.py` — 70 lines (keep)
- `router.py` — 245 lines (keep)

**Target:** Same as current but with `main.py` at root (done in Phase 1). No further splitting needed.

**Steps:**

- [ ] **12.1** No changes beyond Phase 1 (move `app/main.py` → `main.py`)

---

### Phase 13: AI Service

**Current state:**

- `models.py` — 135 lines (keep)
- `schemas.py` — 183 lines (keep)
- `router.py` — 430 lines (borderline — keep or split)
- `providers/` — already well-organised ✅
- `scoring/` — already well-organised ✅

**Target:**

```
ai_service/
├── main.py
├── models.py
├── schemas.py
├── router.py                   # 430 lines — keep as single file
├── providers/                  # ✅ Keep as-is
│   ├── __init__.py
│   └── base.py
├── scoring/                    # ✅ Keep as-is
│   ├── __init__.py
│   ├── cohort_complexity.py
│   ├── coach_grade.py
│   └── coach_suggestion.py
├── alembic/
├── Dockerfile
└── tests/
```

**Steps:**

- [ ] **13.1** No changes beyond Phase 1 (move `app/main.py` → `main.py`)

---

### Phase 14: Gateway Service

**Current state:**

- `app/main.py` — 486 lines
- `app/clients.py` — 115 lines
- `app/routers/cleanup.py` — 251 lines
- `app/routers/dashboard.py` — 173 lines
- `app/tests/` — 4 test files
- Empty stub directories: `app/api/`, `app/core/`, `app/models/`, `app/schemas/`, `app/services/`

**Target:**

```
gateway_service/
├── main.py                     # (from app/main.py)
├── clients.py                  # (from app/clients.py)
├── routers/
│   ├── __init__.py
│   ├── cleanup.py              # (from app/routers/cleanup.py)
│   └── dashboard.py            # (from app/routers/dashboard.py)
├── Dockerfile
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── stubs.py
    ├── test_cleanup.py
    ├── test_dashboard.py
    └── test_gateway_proxy.py
```

**Steps:**

- [ ] **14.1** Move `app/main.py` → `main.py`
- [ ] **14.2** Move `app/clients.py` → `clients.py`
- [ ] **14.3** Move `app/routers/` → `routers/`
- [ ] **14.4** Move `app/tests/` → `tests/`
- [ ] **14.5** Delete empty stub directories and `app/`
- [ ] **14.6** Update all internal imports (`from services.gateway_service.app.routers...` → `from services.gateway_service.routers...`)
- [ ] **14.7** Update `docker-compose.yml` and `Dockerfile`
- [ ] **14.8** Run tests, verify gateway starts and proxies work

---

### Phase 15: Cleanup

- [ ] **15.1** Delete `identity_service/` entirely (empty stub, never implemented, auth handled by Supabase)
- [ ] **15.2** Verify no remaining `app/` directories in any service
- [ ] **15.3** Run full test suite across all services
- [ ] **15.4** Update `CLAUDE.md` with new structure conventions
- [ ] **15.5** Update `ARCHITECTURE.md` with the standard service layout
- [ ] **15.6** Update migration scripts (`scripts/db/migrate.sh` etc.) if they reference old paths
- [ ] **15.7** Update `API_TYPE_GENERATION.md` if it references old paths
- [ ] **15.8** Commit with clear message explaining the restructuring

---

## Execution Order

| Phase | Service                | Complexity | Risk   | Est. Files Changed |
| ----- | ---------------------- | ---------- | ------ | ------------------ |
| 1     | ALL (move app/main.py) | Low        | Low    | ~30                |
| 2     | Academy                | High       | Medium | ~25                |
| 3     | Members                | High       | Medium | ~20                |
| 4     | Payments               | Medium     | Low    | ~12                |
| 5     | Communications         | Medium     | Low    | ~12                |
| 6     | Store                  | Medium     | Low    | ~12                |
| 7     | Volunteer              | Medium     | Low    | ~8                 |
| 8     | Media                  | Low        | Low    | ~6                 |
| 9     | Transport              | Low        | Low    | ~6                 |
| 10    | Sessions               | Low        | Low    | ~8                 |
| 11    | Attendance             | Low        | Low    | ~6                 |
| 12    | Events                 | None       | None   | 0                  |
| 13    | AI                     | None       | None   | 0                  |
| 14    | Gateway                | Medium     | Medium | ~15                |
| 15    | Cleanup                | Low        | Low    | ~5                 |

**Total estimated: ~165 file moves/edits**

---

## Key Risks and Mitigations

1. **Alembic breaks** — Model re-exports via `__init__.py` must preserve the exact same import paths. Test with `./scripts/db/migrate.sh <svc> "test"` and then delete the test migration.

2. **Docker builds break** — The `COPY` paths in Dockerfiles must match new structure. Test with `docker compose build <service>`.

3. **Cross-service imports break** — We've replaced most of these with service client HTTP calls, but some remain in test files and seed scripts. Grep for `from services.<svc>.models` across the entire codebase before and after each phase.

4. **Gateway proxy stops working** — Gateway copies ALL services in its Dockerfile. After restructuring, verify the gateway can still import/proxy correctly.

5. **Worker processes break** — Academy and communications have ARQ workers. The `worker.py` path stays the same, but verify `tasks.py` imports still resolve.

---

## Conventions to Enforce Going Forward

After restructuring, add these rules to `CONVENTIONS.md`:

1. **New services** must follow the standard layout from this document
2. **Single files** become directories when they exceed **300 lines**
3. **Routers are thin** — HTTP concerns only, delegate to `services/` for business logic
4. **No `app/` wrapper** — `main.py` lives at service root
5. **Tests at service root** — `tests/` directory, not nested inside `app/`
6. **`__init__.py` re-exports** — All `models/__init__.py` and `schemas/__init__.py` must re-export every public symbol so external imports don't break
7. **Naming**: `routers/`, `schemas/`, `models/`, `services/`, `clients/`, `tests/` — these exact names, no variations

---

_Created: February 2026_
_Last updated: February 2026_
