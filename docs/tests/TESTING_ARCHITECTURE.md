# SwimBuddz Testing Architecture

> **Status:** Implementation plan — not yet started
> **Author:** Senior engineering review, February 2026
> **Purpose:** This document is the single source of truth for building the test suite. Any AI agent or developer should be able to pick this up and implement it without additional context.

---

## Table of Contents

1. [Why This Exists](#why-this-exists)
2. [Current State](#current-state)
3. [Target Architecture](#target-architecture)
4. [The Three Test Layers](#the-three-test-layers)
5. [Directory Structure](#directory-structure)
6. [Infrastructure: conftest.py Redesign](#infrastructure-conftestpy-redesign)
7. [Test Factories](#test-factories)
8. [Layer 1: Unit Tests](#layer-1-unit-tests)
9. [Layer 2: Integration Tests](#layer-2-integration-tests)
10. [Layer 3: Contract Tests](#layer-3-contract-tests)
11. [What NOT to Test](#what-not-to-test)
12. [Implementation Priority](#implementation-priority)
13. [Running Tests](#running-tests)
14. [Appendix: Service Dependency Map](#appendix-service-dependency-map)

---

## Why This Exists

The backend has undergone a major architectural refactor: services that previously queried each other's databases directly now communicate via HTTP through internal routers and a centralized service client (`libs/common/service_client.py`). This means:

- A change to the members service's `/internal/members/{id}` response shape silently breaks the academy service, communications service, and payments service
- There is no compile-time check across service boundaries — only runtime HTTP calls
- The existing 5 test files (3 unit, 2 integration) cover <5% of the API surface

Without a testing system, every deploy is a gamble. This document defines the system that eliminates that gamble.

---

## Current State

### What exists today

```
tests/
├── test_db.py                   # 1 test: SELECT 1 (DB connectivity)
├── test_members_service.py      # 13 tests: pure unit tests for tier logic
├── test_registration_flow.py    # 4 tests: integration tests through gateway
├── test_session_delete.py       # 1 test: session deletion through gateway
├── test_session_stats.py        # 3 tests: datetime handling (pure unit)
```

**Root conftest.py** provides:

- `test_engine` — creates/drops all tables on real dev database (Supabase)
- `db_session` — transactional session with rollback
- `client` — AsyncClient through gateway with `InAppServiceClient` wiring for members + sessions only
- `auth_headers` — placeholder Bearer token

### Problems with current setup

1. **Runs against live dev database** — loads `.env.dev`, connects to Supabase. Tests can't run in CI, can't run concurrently, and nuke the dev schema on every run.
2. **Only 2 services wired** — `InAppServiceClient` only covers members and sessions. The other 10 services aren't reachable through the gateway in tests.
3. **No service isolation** — integration tests go through the gateway, so a failure could be in the gateway proxy, the service router, the database query, or the auth middleware. No way to tell.
4. **No cross-service validation** — nothing checks that the service client's expectations match the internal routers' actual responses.
5. **Manual auth mocking** — every test function manually sets and clears `app.dependency_overrides`. Repetitive and error-prone.

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Layer 3: Contract Tests            (~15 tests, ~5s)    │
│  "Do services agree on what they send each other?"      │
│  ─ Internal router request/response shapes              │
│  ─ Service client expectations vs actual responses      │
│  ─ Catches: Schema drift between services               │
├─────────────────────────────────────────────────────────┤
│  Layer 2: Integration Tests         (~60 tests, ~30s)   │
│  "Does each service work correctly with its database?"  │
│  ─ One test suite per service                           │
│  ─ Real database (local/test), mocked auth              │
│  ─ External service calls mocked via httpx mock         │
│  ─ Catches: Query bugs, auth scoping, status machines   │
├─────────────────────────────────────────────────────────┤
│  Layer 1: Unit Tests                (~30 tests, ~2s)    │
│  "Does the business logic compute correctly?"           │
│  ─ Pure functions, no I/O                               │
│  ─ No database, no HTTP, no auth                        │
│  ─ Catches: Calculation errors, validation bugs         │
└─────────────────────────────────────────────────────────┘
```

**No E2E layer.** E2E tests that spin up all 12 services are fragile, slow, and tell you "something broke" without telling you what. The contract tests give the same cross-service confidence at 1/100th the cost.

**Target: ~105 tests, ~37 seconds total runtime.**

---

## The Three Test Layers

### Layer 1: Unit Tests

**What:** Pure Python functions with no I/O — no database, no HTTP, no auth, no fixtures.

**When they run:** On every save. They finish in <2 seconds.

**What they catch:** Calculation errors, validation bugs, business rule mistakes.

**Rule:** If a test needs `await`, `db_session`, or any import from `libs/db`, it is NOT a unit test.

### Layer 2: Integration Tests

**What:** Each service's FastAPI app tested in isolation. Real database (transactional, rolled back after each test). Mocked auth. External service-to-service HTTP calls mocked.

**When they run:** Before pushing. ~30 seconds.

**What they catch:** SQLAlchemy query bugs, missing joins, wrong status transitions, auth scoping errors, Pydantic serialization issues.

**Rule:** Each test file targets ONE service. The service under test has a real DB; all other services it calls are mocked.

### Layer 3: Contract Tests

**What:** Validates that internal router responses match what the service client expects. Calls the real internal endpoint with a real DB, then asserts the response contains every field the consuming services depend on.

**When they run:** On every PR. ~5 seconds.

**What they catch:** Schema drift between services — e.g., the members service renames `full_name` to `name` and breaks academy, comms, and payments.

**Rule:** Contract tests never test business logic. They only test shape: "does this JSON have these keys with these types?"

---

## Directory Structure

```
swimbuddz-backend/
├── conftest.py                          # Root: engine + session fixtures (REDESIGNED)
├── pytest.ini                           # NEW: test configuration
├── tests/
│   ├── __init__.py
│   ├── conftest.py                      # NEW: shared helpers, auth fixtures, mock builders
│   ├── factories.py                     # NEW: model factories for all services
│   │
│   ├── unit/                            # Layer 1
│   │   ├── __init__.py
│   │   ├── test_member_tiers.py         # MOVED from tests/test_members_service.py
│   │   ├── test_session_stats.py        # MOVED from tests/test_session_stats.py
│   │   ├── test_enrollment_validation.py
│   │   ├── test_discount_calculation.py
│   │   ├── test_complexity_scoring.py
│   │   └── test_payout_calculation.py
│   │
│   ├── integration/                     # Layer 2
│   │   ├── __init__.py
│   │   ├── conftest.py                  # Service-specific client fixtures
│   │   ├── test_members_api.py          # ABSORBS tests/test_registration_flow.py
│   │   ├── test_members_internal.py
│   │   ├── test_sessions_api.py         # ABSORBS tests/test_session_delete.py
│   │   ├── test_sessions_internal.py
│   │   ├── test_attendance_api.py
│   │   ├── test_attendance_internal.py
│   │   ├── test_academy_programs.py
│   │   ├── test_academy_cohorts.py
│   │   ├── test_academy_enrollments.py
│   │   ├── test_academy_coach_assignment.py
│   │   ├── test_communications_api.py
│   │   ├── test_payments_api.py
│   │   ├── test_payments_webhooks.py
│   │   └── test_payments_payouts.py
│   │
│   └── contract/                        # Layer 3
│       ├── __init__.py
│       ├── conftest.py                  # Service role auth fixture
│       ├── test_members_contract.py
│       ├── test_sessions_contract.py
│       └── test_attendance_contract.py
```

**Migration path for existing tests:**

- `tests/test_members_service.py` → `tests/unit/test_member_tiers.py` (rename, no changes needed)
- `tests/test_session_stats.py` → `tests/unit/test_session_stats.py` (rename, no changes needed)
- `tests/test_registration_flow.py` → absorbed into `tests/integration/test_members_api.py`
- `tests/test_session_delete.py` → absorbed into `tests/integration/test_sessions_api.py`
- `tests/test_db.py` → delete (a SELECT 1 test provides zero value once real integration tests exist)

---

## Infrastructure: conftest.py Redesign

### Root conftest.py — What changes

The root `conftest.py` handles ONLY database fixtures. It should NOT import any service apps or set up HTTP clients.

**Key changes:**

1. Import ALL service models (not just members + sessions) so `Base.metadata.create_all` creates all tables
2. Keep the transactional `db_session` fixture — it's well-designed
3. Remove the `client` fixture — each test layer creates its own clients
4. Remove `InAppServiceClient` — integration tests use `ASGITransport` directly per-service
5. Remove `auth_headers` — moved to `tests/conftest.py` as proper fixtures

### tests/conftest.py — New shared fixtures

This file provides:

1. **Auth user factories** — `mock_member()`, `mock_admin()`, `mock_coach()`, `mock_service_role()` — return `AuthUser` instances
2. **Auth override helpers** — context managers that set/clear `dependency_overrides` on a given FastAPI app
3. **Service role headers** — for contract tests that call internal endpoints
4. **Service client mock builders** — functions that return pre-configured `AsyncMock` objects for `libs.common.service_client`

### tests/integration/conftest.py — Per-service clients

This file provides one fixture per service:

- `members_client` — AsyncClient targeting members service app
- `sessions_client` — AsyncClient targeting sessions service app
- `attendance_client` — AsyncClient targeting attendance service app
- `academy_client` — AsyncClient targeting academy service app (with mocked service client)
- `communications_client` — AsyncClient targeting communications service app (with mocked service client)
- `payments_client` — AsyncClient targeting payments service app (with mocked service client)

Each fixture:

1. Overrides `get_async_db` to use the transactional `db_session`
2. Overrides auth dependencies with the appropriate mock user
3. Mocks `libs.common.service_client` functions for services that make cross-service calls
4. Yields an `AsyncClient` with `ASGITransport(app=service_app)`
5. Cleans up all overrides after the test

---

## Test Factories

Located at `tests/factories.py`. Each factory creates a valid SQLAlchemy model instance with sensible defaults. Override any field via kwargs.

### Design principles

1. **Every factory produces a valid, insertable model** — no missing required fields
2. **UUIDs and emails are unique per call** — use `uuid4()` and random suffixes
3. **Timestamps default to now** — `datetime.now(timezone.utc)`
4. **Enums default to the most common value** — e.g., `status="approved"` for members
5. **Relationships are NOT auto-created** — if you need a Member with a CoachProfile, create both explicitly. Factories don't hide complexity.

### Factories to implement

| Factory                      | Model                 | Service        | Key defaults                                                                             |
| ---------------------------- | --------------------- | -------------- | ---------------------------------------------------------------------------------------- |
| `MemberFactory`              | `Member`              | members        | `approval_status="approved"`, `is_active=True`, `registration_complete=True`             |
| `CoachProfileFactory`        | `CoachProfile`        | members        | `status="approved"`, `learn_to_swim_grade="grade_2"`                                     |
| `PendingRegistrationFactory` | `PendingRegistration` | members        | Valid `profile_data_json`                                                                |
| `SessionFactory`             | `Session`             | sessions       | `status="SCHEDULED"`, `session_type="CLUB"`, `starts_at=tomorrow`, `ends_at=tomorrow+2h` |
| `SessionCoachFactory`        | `SessionCoach`        | sessions       | `role="lead"`                                                                            |
| `ProgramFactory`             | `Program`             | academy        | `is_published=True`, `level="BEGINNER_1"`, `duration_weeks=12`                           |
| `CohortFactory`              | `Cohort`              | academy        | `status="OPEN"`, `capacity=20`                                                           |
| `EnrollmentFactory`          | `Enrollment`          | academy        | `status="ENROLLED"`, `payment_status="PAID"`                                             |
| `AttendanceRecordFactory`    | `AttendanceRecord`    | attendance     | `status="PRESENT"`, `role="SWIMMER"`                                                     |
| `PaymentFactory`             | `Payment`             | payments       | `status="PENDING"`, `purpose="COMMUNITY"`, `amount=20000`                                |
| `DiscountFactory`            | `Discount`            | payments       | `discount_type="PERCENTAGE"`, `value=10.0`, `is_active=True`                             |
| `AnnouncementFactory`        | `Announcement`        | communications | `status="PUBLISHED"`, `audience="COMMUNITY"`                                             |

---

## Layer 1: Unit Tests

### Files and what they test

#### `tests/unit/test_member_tiers.py`

**Source:** `services/members_service/service.py` — `normalize_member_tiers()`, `calculate_community_expiry()`, `calculate_club_expiry()`, `validate_club_readiness()`, `check_club_eligibility()`

| Test                                                         | What it validates            |
| ------------------------------------------------------------ | ---------------------------- |
| No tiers defaults to community                               | New members get community    |
| Active club adds both club and community                     | Tier hierarchy               |
| Expired club keeps existing tiers (additive model)           | Tiers are never auto-removed |
| Tiers sorted by priority: academy > club > community         | Primary tier selection       |
| Community expiry extends from current expiry if active       | Renewal stacking             |
| Club expiry calculates correctly for 3, 6, 12 months         | Duration math                |
| Readiness: all fields present returns True                   | Gate check                   |
| Readiness: missing emergency contact returns False           | Gate check                   |
| Readiness: empty arrays return False                         | Gate check                   |
| Eligibility: already approved = eligible                     | Bypass logic                 |
| Eligibility: academy approved = club eligible                | Tier inclusion               |
| Eligibility: not requested = ineligible                      | Prerequisite check           |
| Eligibility: requested but incomplete readiness = ineligible | Gate check                   |

_(13 tests — these already exist, just move the file)_

#### `tests/unit/test_session_stats.py`

**Source:** datetime handling patterns used across services

_(3 tests — these already exist, just move the file)_

#### `tests/unit/test_enrollment_validation.py`

**Source:** Academy enrollment business rules

| Test                                                   | What it validates                           |
| ------------------------------------------------------ | ------------------------------------------- |
| Enrollment under capacity succeeds                     | Happy path                                  |
| Enrollment at capacity returns waitlist status         | Capacity enforcement                        |
| Enrollment over capacity rejected                      | Hard limit                                  |
| Duplicate enrollment (same member + cohort) rejected   | Idempotency                                 |
| Mid-entry allowed before cutoff week                   | `allow_mid_entry` + `mid_entry_cutoff_week` |
| Mid-entry blocked after cutoff week                    | Cutoff enforcement                          |
| Status transition: PENDING_APPROVAL → ENROLLED (valid) | State machine                               |
| Status transition: WAITLIST → ENROLLED (valid)         | Waitlist promotion                          |
| Status transition: GRADUATED → ENROLLED (invalid)      | Terminal state                              |
| Status transition: DROPPED → ENROLLED (invalid)        | Terminal state                              |

#### `tests/unit/test_discount_calculation.py`

**Source:** Discount application logic

| Test                                  | What it validates      |
| ------------------------------------- | ---------------------- |
| Percentage discount applies correctly | 10% of 20000 = 18000   |
| Fixed discount applies correctly      | 5000 off 20000 = 15000 |
| Discount never goes below zero        | Edge case              |
| Expired discount is inactive          | Date check             |
| Max uses reached = inactive           | Usage limit            |
| Discount applies_to filters correctly | Scope check            |

#### `tests/unit/test_complexity_scoring.py`

**Source:** `services/ai_service/scoring/cohort_complexity.py`

| Test                                       | What it validates |
| ------------------------------------------ | ----------------- |
| Beginner cohort scores lower than advanced | Level scaling     |
| Large capacity increases complexity        | Size factor       |
| Special populations increase complexity    | Category factor   |
| Score is between 0.0 and 1.0               | Bounds check      |
| All 7 dimensions sum correctly             | Aggregation       |

#### `tests/unit/test_payout_calculation.py`

**Source:** Coach payout computation logic

| Test                                       | What it validates |
| ------------------------------------------ | ----------------- |
| Payout amount matches session rate × hours | Basic math        |
| Deductions applied correctly               | Platform fee      |
| Zero-rate coach gets zero payout           | Edge case         |

---

## Layer 2: Integration Tests

### Common patterns

Every integration test follows this pattern:

```python
@pytest.mark.asyncio
async def test_something(service_client, db_session):
    # 1. ARRANGE: Insert test data via factory
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    # 2. ACT: Call the endpoint
    response = await service_client.get(f"/api/v1/members/{member.id}")

    # 3. ASSERT: Check status code and response body
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == member.email
```

### Files and what they test

#### `tests/integration/test_members_api.py`

| Test                                     | Endpoint                             | What it validates           |
| ---------------------------------------- | ------------------------------------ | --------------------------- |
| Complete pending registration            | POST /pending-registrations/complete | Creates member from pending |
| Complete registration is idempotent      | POST /pending-registrations/complete | Returns existing member     |
| Complete without pending → 404           | POST /pending-registrations/complete | Error handling              |
| Get my profile                           | GET /members/me                      | Profile assembly            |
| Update profile (partial)                 | PATCH /members/me                    | Partial update doesn't null |
| Admin list members                       | GET /admin/members                   | Pagination, filtering       |
| Admin approve member                     | POST /admin/members/{id}/approve     | Status transition           |
| Member can't access admin endpoint → 403 | GET /admin/members                   | RBAC                        |
| Coach can access coach endpoints         | GET /coaches/me                      | Role access                 |

#### `tests/integration/test_members_internal.py`

| Test                                    | Endpoint                                | What it validates         |
| --------------------------------------- | --------------------------------------- | ------------------------- |
| Get member by auth_id                   | GET /internal/members/by-auth/{id}      | Lookup works              |
| Get member by auth_id — not found → 404 | GET /internal/members/by-auth/{id}      | Error case                |
| Get member by ID                        | GET /internal/members/{id}              | Lookup works              |
| Bulk member lookup                      | POST /internal/members/bulk             | Multiple members returned |
| Bulk with empty list                    | POST /internal/members/bulk             | Returns empty array       |
| Bulk with nonexistent IDs               | POST /internal/members/bulk             | Skips missing, no error   |
| Get coach profile                       | GET /internal/coaches/{id}/profile      | Returns coach data        |
| Get coach profile for non-coach → 404   | GET /internal/coaches/{id}/profile      | Error case                |
| Get eligible coaches filtered by grade  | GET /internal/coaches/eligible          | Grade filtering           |
| Get coach readiness data                | GET /internal/coaches/{id}/readiness    | Extended data shape       |
| Get bank account                        | GET /internal/members/{id}/bank-account | Sensitive data access     |
| Non-service-role auth → 403             | Any /internal/\*                        | Auth enforcement          |

#### `tests/integration/test_sessions_api.py`

| Test                                    | Endpoint                    | What it validates     |
| --------------------------------------- | --------------------------- | --------------------- |
| Create draft session                    | POST /sessions              | Draft created         |
| Publish session                         | POST /sessions/{id}/publish | Status → SCHEDULED    |
| List sessions (public, excludes drafts) | GET /sessions               | Filtering             |
| List sessions by type                   | GET /sessions?type=club     | Type filter           |
| Cancel session                          | POST /sessions/{id}/cancel  | Status transition     |
| Delete session                          | DELETE /sessions/{id}       | Deletion              |
| Create session with coaches             | POST /sessions              | SessionCoach junction |
| Admin creates, member can't → 403       | POST /sessions              | RBAC                  |

#### `tests/integration/test_sessions_internal.py`

| Test                              | Endpoint                                         | What it validates      |
| --------------------------------- | ------------------------------------------------ | ---------------------- |
| Get session by ID                 | GET /internal/sessions/{id}                      | Lookup                 |
| Next session for cohort           | GET /internal/cohorts/{id}/next-session          | Returns closest future |
| Next session — no upcoming → null | GET /internal/cohorts/{id}/next-session          | Empty case             |
| Session IDs for cohort            | GET /internal/cohorts/{id}/session-ids           | Returns UUID list      |
| Completed session IDs             | GET /internal/cohorts/{id}/completed-session-ids | Status filter          |
| Session coaches                   | GET /internal/sessions/{id}/coaches              | Returns member IDs     |
| Scheduled sessions in date range  | GET /internal/sessions/scheduled                 | Date filtering         |

#### `tests/integration/test_attendance_api.py`

| Test                         | Endpoint                                        | What it validates |
| ---------------------------- | ----------------------------------------------- | ----------------- |
| Sign in to session           | POST /attendance/sessions/{id}/sign-in          | Creates record    |
| Double sign-in is idempotent | POST /attendance/sessions/{id}/sign-in          | No duplicate      |
| View session attendance      | GET /attendance/sessions/{id}/attendance        | List records      |
| Cohort attendance summary    | GET /attendance/cohorts/{id}/attendance/summary | Aggregation       |
| My attendance history        | GET /attendance/me/attendance                   | Scoped to user    |
| Coach can view their session | GET /attendance/sessions/{id}/attendance        | Auth scoping      |

#### `tests/integration/test_attendance_internal.py`

| Test                                      | Endpoint                                             | What it validates  |
| ----------------------------------------- | ---------------------------------------------------- | ------------------ |
| Member attendance records                 | GET /internal/attendance/member/{id}                 | Returns records    |
| Member attendance filtered by session IDs | GET /internal/attendance/member/{id}?session_ids=... | Query param filter |
| Session attendee member IDs               | GET /internal/attendance/session/{id}/member-ids     | Returns ID list    |

#### `tests/integration/test_academy_programs.py`

| Test                        | Endpoint                            | What it validates        |
| --------------------------- | ----------------------------------- | ------------------------ |
| Create program              | POST /academy/programs              | Program created          |
| List programs               | GET /academy/programs               | Returns published only   |
| Get program with curriculum | GET /academy/programs/{id}          | Includes curriculum_json |
| Update program              | PATCH /academy/programs/{id}        | Partial update           |
| Publish program             | POST /academy/programs/{id}/publish | Status flip              |

#### `tests/integration/test_academy_cohorts.py`

| Test                                        | Endpoint                                | What it validates       |
| ------------------------------------------- | --------------------------------------- | ----------------------- |
| Create cohort from program                  | POST /academy/cohorts                   | Inherits program config |
| List cohorts                                | GET /academy/cohorts                    | Filtered by status      |
| Cohort status: open → active                | POST /academy/cohorts/{id}/activate     | Transition              |
| Cohort status: active → completed           | POST /academy/cohorts/{id}/complete     | Transition              |
| Cohort status: completed → active (invalid) | POST /academy/cohorts/{id}/activate     | Blocked                 |
| Cascade delete cohort                       | DELETE /academy/cohorts/{id}            | Children cleaned up     |
| Assign coach to cohort                      | POST /academy/cohorts/{id}/assign-coach | Coach linked            |
| Assign coach — grade too low → 400          | POST /academy/cohorts/{id}/assign-coach | Grade check             |

#### `tests/integration/test_academy_enrollments.py`

| Test                          | Endpoint                                | What it validates  |
| ----------------------------- | --------------------------------------- | ------------------ |
| Enroll student (happy path)   | POST /academy/enrollments               | Status = ENROLLED  |
| Enroll at capacity → waitlist | POST /academy/enrollments               | Status = WAITLIST  |
| Duplicate enrollment → 409    | POST /academy/enrollments               | Conflict           |
| Promote from waitlist         | POST /academy/enrollments/{id}/promote  | FIFO ordering      |
| Drop enrollment               | POST /academy/enrollments/{id}/drop     | Status = DROPPED   |
| Record student progress       | POST /academy/enrollments/{id}/progress | Milestone tracked  |
| Graduate student              | POST /academy/enrollments/{id}/graduate | Status = GRADUATED |

#### `tests/integration/test_academy_coach_assignment.py`

| Test                    | Endpoint                                      | What it validates  |
| ----------------------- | --------------------------------------------- | ------------------ |
| Assign lead coach       | POST /academy/coach-assignments               | Assignment created |
| Assign assistant coach  | POST /academy/coach-assignments               | Role = assistant   |
| Shadow evaluation       | POST /academy/coach-assignments/{id}/evaluate | Evaluation saved   |
| Remove coach assignment | DELETE /academy/coach-assignments/{id}        | Assignment removed |

#### `tests/integration/test_communications_api.py`

| Test                   | Endpoint                      | What it validates                     |
| ---------------------- | ----------------------------- | ------------------------------------- |
| Create announcement    | POST /announcements           | Created with targeting                |
| List announcements     | GET /announcements            | Filtered by audience                  |
| Mark announcement read | POST /announcements/{id}/read | Read tracking                         |
| Send cohort message    | POST /messages                | Uses service client for member lookup |

#### `tests/integration/test_payments_api.py`

| Test                        | Endpoint                      | What it validates            |
| --------------------------- | ----------------------------- | ---------------------------- |
| Create payment intent       | POST /payments/intents        | Paystack reference generated |
| Verify payment              | POST /payments/verify         | Status updated               |
| List my payments            | GET /payments/me              | Scoped to user               |
| Apply discount code         | POST /payments/apply-discount | Amount recalculated          |
| Invalid discount code → 404 | POST /payments/apply-discount | Error case                   |
| Expired discount → 400      | POST /payments/apply-discount | Validation                   |

#### `tests/integration/test_payments_webhooks.py`

| Test                            | Endpoint                         | What it validates    |
| ------------------------------- | -------------------------------- | -------------------- |
| Valid Paystack signature → 200  | POST /payments/webhooks/paystack | HMAC verified        |
| Invalid signature → 400         | POST /payments/webhooks/paystack | Rejected             |
| Webhook updates payment status  | POST /payments/webhooks/paystack | Status = PAID        |
| Duplicate webhook is idempotent | POST /payments/webhooks/paystack | No double-processing |

#### `tests/integration/test_payments_payouts.py`

| Test                                        | Endpoint                             | What it validates     |
| ------------------------------------------- | ------------------------------------ | --------------------- |
| Create payout request                       | POST /payments/payouts               | Payout created        |
| Admin approve payout                        | POST /payments/payouts/{id}/approve  | Status transition     |
| Coach view their payouts                    | GET /payments/payouts/me             | Scoped to coach       |
| Payout uses service client for bank account | POST /payments/payouts/{id}/initiate | Mocked service client |

---

## Layer 3: Contract Tests

### Purpose

Contract tests answer one question: **"If I call this internal endpoint, does the response contain every field that consuming services depend on?"**

They don't test business logic. They test _shape_.

### How they work

1. Insert a model via factory (real DB)
2. Call the internal endpoint with service-role auth
3. Assert the response JSON contains every key the service client helpers expect

### Files and what they test

#### `tests/contract/test_members_contract.py`

| Contract          | Producer endpoint                       | Consumers                            | Required fields                                                                                |
| ----------------- | --------------------------------------- | ------------------------------------ | ---------------------------------------------------------------------------------------------- |
| Member by auth ID | GET /internal/members/by-auth/{id}      | attendance, sessions, academy, comms | `id`, `auth_id`, `email`, `full_name`, `is_active`                                             |
| Member by ID      | GET /internal/members/{id}              | academy, comms, payments             | `id`, `auth_id`, `email`, `full_name`, `first_name`, `last_name`, `is_active`                  |
| Bulk members      | POST /internal/members/bulk             | academy (cohort roster)              | list of `{id, full_name, email}`                                                               |
| Coach profile     | GET /internal/coaches/{id}/profile      | academy (assignment)                 | `member_id`, `display_name`, `learn_to_swim_grade`, `total_coaching_hours`, `status`           |
| Coach readiness   | GET /internal/coaches/{id}/readiness    | AI service (scoring)                 | `member_id`, `coaching_years`, `total_coaching_hours`, `average_feedback_rating`, grade fields |
| Eligible coaches  | GET /internal/coaches/eligible          | academy, AI                          | list of coach objects with grade fields                                                        |
| Bank account      | GET /internal/members/{id}/bank-account | payments (payout)                    | `bank_name`, `account_number`, `account_name` (or 404)                                         |

#### `tests/contract/test_sessions_contract.py`

| Contract                | Producer endpoint                                | Consumers           | Required fields                                                             |
| ----------------------- | ------------------------------------------------ | ------------------- | --------------------------------------------------------------------------- |
| Session by ID           | GET /internal/sessions/{id}                      | attendance, academy | `id`, `title`, `starts_at`, `ends_at`, `status`, `session_type`, `location` |
| Next session for cohort | GET /internal/cohorts/{id}/next-session          | academy, comms      | `id`, `starts_at`, `title` (or null)                                        |
| Session IDs for cohort  | GET /internal/cohorts/{id}/session-ids           | academy             | list of UUID strings                                                        |
| Completed session IDs   | GET /internal/cohorts/{id}/completed-session-ids | academy             | list of UUID strings                                                        |
| Session coaches         | GET /internal/sessions/{id}/coaches              | attendance          | list of member ID strings                                                   |

#### `tests/contract/test_attendance_contract.py`

| Contract             | Producer endpoint                                | Consumers            | Required fields                            |
| -------------------- | ------------------------------------------------ | -------------------- | ------------------------------------------ |
| Member attendance    | GET /internal/attendance/member/{id}             | comms (missed class) | list of `{session_id, status, created_at}` |
| Session attendee IDs | GET /internal/attendance/session/{id}/member-ids | comms, academy       | list of member ID strings                  |

---

## What NOT to Test

| Don't test                    | Why                                                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------------------------------ |
| SQLAlchemy column definitions | If you define `Column(String)`, SQLAlchemy handles it.                                                 |
| FastAPI routing mechanics     | `@app.get("/foo")` returning 200 is FastAPI's job.                                                     |
| Alembic migration application | `reset.sh` validates this every time it runs.                                                          |
| Gateway proxy forwarding      | The proxy is mechanical HTTP forwarding, not business logic.                                           |
| Seed scripts                  | Developer tools, not production code.                                                                  |
| Third-party libraries         | Don't test that `httpx.get()` works.                                                                   |
| Pydantic validation           | Schema validation is Pydantic's responsibility. Test your business logic that USES the validated data. |

**Focus your testing on YOUR code** — business logic, query logic, auth scoping, status machines, and cross-service contracts.

---

## Implementation Priority

Build in this order. Each step builds on the previous one.

### Phase 1: Infrastructure (MUST DO FIRST)

1. Create `pytest.ini`
2. Redesign root `conftest.py` (import all models, remove client fixture)
3. Create `tests/conftest.py` (auth fixtures, mock builders)
4. Create `tests/factories.py` (all model factories)
5. Create `tests/integration/conftest.py` (per-service client fixtures)
6. Create `tests/contract/conftest.py` (service role fixtures)
7. Move existing unit tests to `tests/unit/`
8. Verify existing tests still pass

### Phase 2: Members Service (Foundation)

9. `tests/integration/test_members_api.py`
10. `tests/integration/test_members_internal.py`
11. `tests/contract/test_members_contract.py`

### Phase 3: Sessions + Attendance

12. `tests/integration/test_sessions_api.py`
13. `tests/integration/test_sessions_internal.py`
14. `tests/contract/test_sessions_contract.py`
15. `tests/integration/test_attendance_api.py`
16. `tests/integration/test_attendance_internal.py`
17. `tests/contract/test_attendance_contract.py`

### Phase 4: Academy

18. `tests/integration/test_academy_programs.py`
19. `tests/integration/test_academy_cohorts.py`
20. `tests/integration/test_academy_enrollments.py`
21. `tests/integration/test_academy_coach_assignment.py`

### Phase 5: Payments

22. `tests/integration/test_payments_api.py`
23. `tests/integration/test_payments_webhooks.py`
24. `tests/integration/test_payments_payouts.py`

### Phase 6: Communications + Unit Tests

25. `tests/integration/test_communications_api.py`
26. `tests/unit/test_enrollment_validation.py`
27. `tests/unit/test_discount_calculation.py`
28. `tests/unit/test_complexity_scoring.py`
29. `tests/unit/test_payout_calculation.py`

---

## Running Tests

```bash
# All tests
pytest

# By layer
pytest tests/unit/              # Fast, ~2s
pytest tests/integration/       # Medium, ~30s
pytest tests/contract/          # Fast, ~5s

# By pytest marker
pytest -m unit
pytest -m integration
pytest -m contract
pytest -m "not slow"

# Single service
pytest tests/integration/test_members_api.py
pytest tests/integration/test_academy_cohorts.py

# With coverage
pytest --cov=services --cov-report=html

# Verbose with stdout
pytest -v -s tests/integration/test_members_internal.py
```

---

## Appendix: Service Dependency Map

This shows which services call which internal endpoints. If you change an internal endpoint, check all consumers.

```
members_service /internal/*
├── academy_service       ← get_member_by_id, get_coach_profile, get_eligible_coaches
├── attendance_service    ← get_member_by_auth_id
├── communications_service ← get_member_by_id, get_members_bulk
├── payments_service      ← get_member_by_id (bank account for payouts)
└── ai_service            ← get_coach_readiness_data, get_eligible_coaches

sessions_service /internal/*
├── academy_service       ← get_session_by_id, get_next_session_for_cohort, get_session_ids_for_cohort
├── attendance_service    ← (reads session_id from request, not via service client)
└── communications_service ← get_next_session_for_cohort

attendance_service /internal/*
├── academy_service       ← member attendance for progress tracking
└── communications_service ← attendance data for missed class notifications
```

---

_Created: February 2026_
_Last updated: February 2026_
