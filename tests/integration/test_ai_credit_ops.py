"""Tests for the analyzer credit ledger ops (services.credit_ops).

Exercises the in-service atomic credit lifecycle directly against db_session
(no worker / no cv2): free-grant + reserve, the paywall, consume, refund, paid
grant, revoke (clamp + flag), idempotency, and email canonicalization. Emails
are unique per test so the shared dev DB carries no cross-test state.
"""

import uuid

import pytest

from services.ai_service.services import credit_ops


def _email() -> str:
    return f"co-{uuid.uuid4().hex}@example.com"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_free_grant_then_reserve_then_paywall(db_session):
    email = _email()
    entry = await credit_ops.acquire_for_submit(
        db_session, raw_email=email, job_id=uuid.uuid4()
    )
    await db_session.commit()
    assert entry.entry_type.value == "reserve"
    assert entry.balance_after == 0  # free credit granted, then reserved

    bal = await credit_ops.get_balance(db_session, raw_email=email)
    assert bal["free_used"] is True
    assert bal["remaining_credits"] == 0
    assert bal["can_submit_free"] is False

    # Second submit, same email, no purchased credits → paywalled.
    with pytest.raises(credit_ops.NoCreditsError):
        await credit_ops.acquire_for_submit(
            db_session, raw_email=email, job_id=uuid.uuid4()
        )
    await db_session.rollback()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_consume_after_reserve_is_idempotent(db_session):
    email = _email()
    job = uuid.uuid4()
    await credit_ops.acquire_for_submit(db_session, raw_email=email, job_id=job)
    await db_session.commit()

    consumed = await credit_ops.consume_reservation(
        db_session, raw_email=email, job_id=job
    )
    await db_session.commit()
    assert consumed is not None and consumed.entry_type.value == "consume"

    again = await credit_ops.consume_reservation(
        db_session, raw_email=email, job_id=job
    )
    assert again.id == consumed.id  # idempotent — same ledger row


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refund_returns_the_credit(db_session):
    email = _email()
    job = uuid.uuid4()
    await credit_ops.acquire_for_submit(db_session, raw_email=email, job_id=job)
    await db_session.commit()

    refund = await credit_ops.refund_reservation(
        db_session, raw_email=email, job_id=job
    )
    await db_session.commit()
    assert refund is not None and refund.balance_after == 1  # credit returned

    bal = await credit_ops.get_balance(db_session, raw_email=email)
    assert bal["remaining_credits"] == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_grant_paid_is_idempotent_on_sale(db_session):
    email = _email()
    sale = f"sale-{uuid.uuid4().hex}"
    g1 = await credit_ops.grant_paid(
        db_session, raw_email=email, permalink="puxlbz", sale_id=sale
    )
    await db_session.commit()
    assert g1.amount == 10 and g1.balance_after == 10

    g2 = await credit_ops.grant_paid(
        db_session, raw_email=email, permalink="puxlbz", sale_id=sale
    )
    assert g2.id == g1.id  # same sale → no double grant
    bal = await credit_ops.get_balance(db_session, raw_email=email)
    assert bal["remaining_credits"] == 10


@pytest.mark.asyncio
@pytest.mark.integration
async def test_grant_paid_unknown_permalink_is_none(db_session):
    g = await credit_ops.grant_paid(
        db_session, raw_email=_email(), permalink="nope", sale_id="s"
    )
    assert g is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_revoke_clamps_to_zero_and_flags(db_session):
    email = _email()
    sale = f"sale-{uuid.uuid4().hex}"
    await credit_ops.grant_paid(
        db_session, raw_email=email, permalink="vrjec", sale_id=sale
    )  # +1
    await db_session.commit()

    rev = await credit_ops.revoke_sale(db_session, sale_id=sale)
    await db_session.commit()
    assert rev.balance_after == 0  # clamped, no negative debt

    bal = await credit_ops.get_balance(db_session, raw_email=email)
    assert bal["remaining_credits"] == 0
    assert bal["can_submit_free"] is False  # flagged out of the free tier


@pytest.mark.asyncio
@pytest.mark.integration
async def test_canonical_email_collapses_identities(db_session):
    base = uuid.uuid4().hex[:10]
    tagged = f"u.{base}+promo@gmail.com"
    plain = f"u{base}@gmail.com"
    assert credit_ops.canonicalize_email(tagged) == credit_ops.canonicalize_email(plain)

    sale = f"sale-{uuid.uuid4().hex}"
    await credit_ops.grant_paid(
        db_session, raw_email=tagged, permalink="vrjec", sale_id=sale
    )
    await db_session.commit()
    # The plain form sees the same balance — one identity.
    bal = await credit_ops.get_balance(db_session, raw_email=plain)
    assert bal["remaining_credits"] == 1


# ── security-review regressions ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_flagged_account_cannot_use_free_tier(db_session):
    """A refunded (flagged) account loses free-tier re-access — even though it
    never used the free analysis (it only ever bought). Authoritative path."""
    email = _email()
    sale = f"sale-{uuid.uuid4().hex}"
    await credit_ops.grant_paid(
        db_session, raw_email=email, permalink="vrjec", sale_id=sale
    )
    await db_session.commit()
    await credit_ops.revoke_sale(db_session, sale_id=sale)  # flags the account
    await db_session.commit()

    bal = await credit_ops.get_balance(db_session, raw_email=email)
    assert bal["free_used"] is False
    assert bal["can_submit_free"] is False

    with pytest.raises(credit_ops.NoCreditsError):
        await credit_ops.acquire_for_submit(
            db_session, raw_email=email, job_id=uuid.uuid4()
        )
    await db_session.rollback()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_revoke_targets_the_granted_account_not_ping_email(db_session):
    """A redeemed sale credits the analyzer email; the refund must claw back from
    THAT account (resolved by sale_id), not the Ping's buyer email."""
    analyzer_email = _email()
    buyer_email = _email()  # different — the redeem scenario
    sale = f"sale-{uuid.uuid4().hex}"
    await credit_ops.grant_paid(
        db_session, raw_email=analyzer_email, permalink="puxlbz", sale_id=sale
    )
    await db_session.commit()
    assert (await credit_ops.get_balance(db_session, raw_email=analyzer_email))[
        "remaining_credits"
    ] == 10

    await credit_ops.revoke_sale(db_session, sale_id=sale)
    await db_session.commit()

    # The account that got the credits is clawed back...
    assert (await credit_ops.get_balance(db_session, raw_email=analyzer_email))[
        "remaining_credits"
    ] == 0
    # ...and the buyer email (never credited) is untouched / not flagged.
    assert (await credit_ops.get_balance(db_session, raw_email=buyer_email))[
        "can_submit_free"
    ] is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_revoke_without_a_grant_is_noop(db_session):
    """Refund Ping for a sale we never granted → no-op (never flag an innocent
    email)."""
    rev = await credit_ops.revoke_sale(db_session, sale_id=f"sale-{uuid.uuid4().hex}")
    assert rev is None
