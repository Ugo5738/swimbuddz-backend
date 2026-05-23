"""Integration tests for the corporate_service admin pipeline.

Covers:
- Creating contacts, deals, and touchpoints
- Closing a deal as won → a draft CorporateProgram is created with correct pricing
- Bulk-adding employees (idempotent on email)
- Linking a cohort + provisioning a wallet bumps program status to READY
- Bulk-enrolling registered employees calls sessions_service and marks them ENROLLED
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Pipeline: contact → deal → win → program
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_contact(corporate_client):
    payload = {
        "company_name": "Acme Tech",
        "primary_contact_name": "Jane Doe",
        "primary_contact_email": "jane@acme.com",
        "industry": "tech",
        "company_size": "50_to_250",
    }
    resp = await corporate_client.post("/admin/corporate/contacts", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["company_name"] == "Acme Tech"
    assert body["industry"] == "tech"
    assert body["is_active"] is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_full_pipeline_win_creates_program(corporate_client):
    # 1. Contact
    contact_resp = await corporate_client.post(
        "/admin/corporate/contacts",
        json={
            "company_name": "Pipeline Inc",
            "primary_contact_name": "Sam Lead",
            "primary_contact_email": "sam@pipeline.example.com",
        },
    )
    assert contact_resp.status_code == 201, contact_resp.text
    contact_id = contact_resp.json()["id"]

    # 2. Deal
    deal_resp = await corporate_client.post(
        f"/admin/corporate/contacts/{contact_id}/deals",
        json={
            "title": "Q3 Wellness Cohort",
            "expected_employees": 8,
        },
    )
    assert deal_resp.status_code == 201, deal_resp.text
    deal_id = deal_resp.json()["id"]
    assert deal_resp.json()["stage"] == "lead"

    # 3. Win the deal
    win_resp = await corporate_client.post(
        f"/admin/corporate/deals/{deal_id}/win",
        json={
            "program_name": "Pipeline Inc — Q3 Cohort",
            "employee_count": 8,
            "discount_tier": "bulk_5_9",
        },
    )
    assert win_resp.status_code == 201, win_resp.text
    program = win_resp.json()
    assert program["status"] == "draft"
    assert program["employee_count"] == 8
    assert program["discount_tier"] == "bulk_5_9"
    assert program["per_employee_kobo"] == 13_500_000  # ₦135k
    assert program["total_kobo"] == 8 * 13_500_000  # ₦1.08M
    assert program["deal_id"] == deal_id

    # 4. The deal is now closed-won
    deal_after = (
        await corporate_client.get(f"/admin/corporate/deals/{deal_id}")
    ).json()
    assert deal_after["stage"] == "won"
    assert deal_after["actual_close_date"] is not None

    # 5. Cannot win twice
    second = await corporate_client.post(
        f"/admin/corporate/deals/{deal_id}/win",
        json={
            "program_name": "Dup",
            "employee_count": 8,
            "discount_tier": "bulk_5_9",
        },
    )
    assert second.status_code == 400


@pytest.mark.asyncio
@pytest.mark.integration
async def test_lose_deal(corporate_client):
    contact_resp = await corporate_client.post(
        "/admin/corporate/contacts",
        json={
            "company_name": "Lost Co",
            "primary_contact_name": "L",
            "primary_contact_email": "l@lost.example.com",
        },
    )
    contact_id = contact_resp.json()["id"]
    deal_resp = await corporate_client.post(
        f"/admin/corporate/contacts/{contact_id}/deals",
        json={"title": "Will Lose"},
    )
    deal_id = deal_resp.json()["id"]

    resp = await corporate_client.post(
        f"/admin/corporate/deals/{deal_id}/lose",
        json={"lost_reason": "price", "lost_notes": "Budget cut"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["stage"] == "lost"
    assert resp.json()["lost_reason"] == "price"


# ---------------------------------------------------------------------------
# Touchpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_log_touchpoint_updates_deal_last_touch(corporate_client):
    contact_resp = await corporate_client.post(
        "/admin/corporate/contacts",
        json={
            "company_name": "Touch Co",
            "primary_contact_name": "T",
            "primary_contact_email": "t@touch.example.com",
        },
    )
    contact_id = contact_resp.json()["id"]
    deal_resp = await corporate_client.post(
        f"/admin/corporate/contacts/{contact_id}/deals",
        json={"title": "T1"},
    )
    deal_id = deal_resp.json()["id"]

    tp_resp = await corporate_client.post(
        f"/admin/corporate/contacts/{contact_id}/touchpoints",
        json={
            "type": "email_intro",
            "deal_id": deal_id,
            "summary": "Sent the intro pitch",
        },
    )
    assert tp_resp.status_code == 201, tp_resp.text
    assert tp_resp.json()["type"] == "email_intro"

    deal_after = (
        await corporate_client.get(f"/admin/corporate/deals/{deal_id}")
    ).json()
    assert deal_after["last_touch_at"] is not None


# ---------------------------------------------------------------------------
# Employee manifest
# ---------------------------------------------------------------------------


async def _make_program(corporate_client, employee_count: int = 6) -> str:
    contact_resp = await corporate_client.post(
        "/admin/corporate/contacts",
        json={
            "company_name": f"Test-{uuid.uuid4().hex[:8]}",
            "primary_contact_name": "X",
            "primary_contact_email": f"x-{uuid.uuid4().hex[:8]}@test.example.com",
        },
    )
    contact_id = contact_resp.json()["id"]
    prog_resp = await corporate_client.post(
        "/admin/corporate/programs",
        json={
            "contact_id": contact_id,
            "name": "Direct Program",
            "employee_count": employee_count,
            "discount_tier": "bulk_5_9",
            "per_employee_kobo": 0,  # triggers tier-based recompute
            "total_kobo": 0,
        },
    )
    assert prog_resp.status_code == 201, prog_resp.text
    return prog_resp.json()["id"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bulk_add_employees_idempotent_on_email(corporate_client):
    program_id = await _make_program(corporate_client)

    add_resp = await corporate_client.post(
        f"/admin/corporate/programs/{program_id}/employees",
        json={
            "employees": [
                {"full_name": "A One", "email": "a@x.example.com"},
                {"full_name": "B Two", "email": "b@x.example.com"},
                {"full_name": "A Dup", "email": "a@x.example.com"},  # within-payload dup
            ]
        },
    )
    assert add_resp.status_code == 201, add_resp.text
    body = add_resp.json()
    assert body["added"] == 2
    assert body["skipped_duplicates"] == 1

    # Re-add A — should skip.
    again = await corporate_client.post(
        f"/admin/corporate/programs/{program_id}/employees",
        json={"employees": [{"full_name": "A Again", "email": "a@x.example.com"}]},
    )
    assert again.json() == {"added": 0, "skipped_duplicates": 1, "items": []}


# ---------------------------------------------------------------------------
# Orchestration: link cohort, provision wallet, enroll-all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_orchestration_link_provision_enroll(corporate_client):
    program_id = await _make_program(corporate_client, employee_count=2)

    # Add 2 employees and pre-attach member_ids so we can skip match-members.
    add_resp = await corporate_client.post(
        f"/admin/corporate/programs/{program_id}/employees",
        json={
            "employees": [
                {"full_name": "Member One", "email": "one@orch.example.com"},
                {"full_name": "Member Two", "email": "two@orch.example.com"},
            ]
        },
    )
    assert add_resp.status_code == 201, add_resp.text
    emp_ids = [e["id"] for e in add_resp.json()["items"]]

    cohort_id = str(uuid.uuid4())
    session_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    with (
        patch(
            "services.corporate_service.routers.admin_orchestration.get_cohort",
            new=AsyncMock(return_value={"id": cohort_id, "name": "Mock Cohort"}),
        ),
        patch(
            "services.corporate_service.routers.admin_orchestration.provision_corporate_wallet",
            new=AsyncMock(
                return_value={
                    "id": str(uuid.uuid4()),
                    "wallet_id": str(uuid.uuid4()),
                    "company_name": "Mock",
                    "company_email": "x@x",
                    "budget_total": 999,
                    "budget_remaining": 999,
                    "member_bubble_limit": None,
                    "is_active": True,
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            ),
        ),
        patch(
            "services.corporate_service.routers.admin_orchestration.get_cohort_session_ids",
            new=AsyncMock(return_value=session_ids),
        ),
        patch(
            "services.corporate_service.routers.admin_orchestration.bulk_create_bookings",
            new=AsyncMock(
                return_value={
                    "created": 4,  # 2 employees × 2 sessions
                    "skipped": 0,
                    "bookings": [],
                }
            ),
        ),
    ):
        # 1. Link cohort
        r = await corporate_client.post(
            f"/admin/corporate/programs/{program_id}/link-cohort",
            json={"cohort_id": cohort_id},
        )
        assert r.status_code == 200, r.text
        assert r.json()["cohort_id"] == cohort_id
        assert r.json()["status"] == "draft"  # wallet still missing

        # 2. Provision wallet
        r = await corporate_client.post(
            f"/admin/corporate/programs/{program_id}/provision-wallet",
            json={},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ready"
        assert r.json()["corporate_wallet_id"] is not None

        # 3. Enroll-all — fails because employees have no member_id yet
        r = await corporate_client.post(
            f"/admin/corporate/programs/{program_id}/enroll-all",
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enrolled"] == 0
        assert body["skipped_no_member_id"] == 2

        # 4. Set member_id manually via the DB (would normally come from match-members)
        from sqlalchemy import update

        from services.corporate_service.models import CorporateProgramEmployee

        # We need access to the test db_session — corporate_client uses it via
        # the same override, so opening a new connection would be a separate
        # transaction. Instead, hit the API to add member_id directly is not
        # supported in Phase 1 (match-members talks to a live members_service).
        # For this test we patch match logic via the underlying call.

        from unittest.mock import patch as _patch

        async def _fake_find(email: str):
            return {
                "id": str(uuid.uuid4()),
                "auth_id": f"auth-{email}",
                "email": email,
            }

        with _patch(
            "services.corporate_service.routers.admin_employees.find_member_by_email",
            new=AsyncMock(side_effect=_fake_find),
        ):
            r = await corporate_client.post(
                f"/admin/corporate/programs/{program_id}/employees/match-members",
            )
            assert r.status_code == 200, r.text
            assert r.json()["matched"] == 2

        # 5. Now enroll-all succeeds
        r = await corporate_client.post(
            f"/admin/corporate/programs/{program_id}/enroll-all",
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enrolled"] == 4  # 2 emp × 2 sessions
        assert body["skipped_no_member_id"] == 0

        # Program is now ACTIVE
        prog_after = (
            await corporate_client.get(f"/admin/corporate/programs/{program_id}")
        ).json()
        assert prog_after["status"] == "active"
        assert prog_after["actual_start_date"] is not None

    # Suppress F841 unused-name warnings — emp_ids retained for documentation.
    _ = emp_ids
