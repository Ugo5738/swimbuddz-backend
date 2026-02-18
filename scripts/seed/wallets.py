#!/usr/bin/env python3
"""
Seed wallet data for development/testing.

Creates test wallets with sample transactions, topups, and grants.
Wallets use standalone auth IDs (not tied to real Supabase users) since
actual wallet creation happens via the API when a user registers.

Idempotent: checks if wallets already exist before creating.
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

# Add project root to path
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, project_root)

from libs.db.config import AsyncSessionLocal
from services.wallet_service.models import (
    GrantType,
    PromotionalBubbleGrant,
    TopupStatus,
    TransactionDirection,
    TransactionStatus,
    TransactionType,
    PaymentMethod,
    Wallet,
    WalletAuditLog,
    WalletStatus,
    WalletTier,
    WalletTopup,
    WalletTransaction,
    AuditAction,
)
from sqlalchemy.future import select

# ---------------------------------------------------------------------------
# Test wallet definitions
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)

SEED_WALLETS = [
    {
        "id": uuid.UUID("00000000-0000-0000-0000-000000000101"),
        "member_id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "member_auth_id": "seed-wallet-active-user",
        "balance": 485,
        "status": WalletStatus.ACTIVE,
        "wallet_tier": WalletTier.STANDARD,
        "lifetime_bubbles_purchased": 500,
        "lifetime_bubbles_spent": 25,
        "lifetime_bubbles_received": 10,
        "label": "Active member wallet",
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-000000000102"),
        "member_id": uuid.UUID("00000000-0000-0000-0000-000000000002"),
        "member_auth_id": "seed-wallet-empty-user",
        "balance": 10,
        "status": WalletStatus.ACTIVE,
        "wallet_tier": WalletTier.STANDARD,
        "lifetime_bubbles_purchased": 0,
        "lifetime_bubbles_spent": 0,
        "lifetime_bubbles_received": 10,
        "label": "New member wallet (welcome bonus only)",
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-000000000103"),
        "member_id": uuid.UUID("00000000-0000-0000-0000-000000000003"),
        "member_auth_id": "seed-wallet-frozen-user",
        "balance": 200,
        "status": WalletStatus.FROZEN,
        "wallet_tier": WalletTier.STANDARD,
        "lifetime_bubbles_purchased": 200,
        "lifetime_bubbles_spent": 10,
        "lifetime_bubbles_received": 10,
        "frozen_reason": "Suspicious activity flagged by automated check",
        "frozen_at": NOW - timedelta(days=3),
        "label": "Frozen wallet for testing",
    },
]


async def seed_wallets():
    """Create test wallets with sample transactions."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            for wallet_data in SEED_WALLETS:
                label = wallet_data.pop("label")

                # Check if wallet already exists
                stmt = select(Wallet).where(Wallet.id == wallet_data["id"])
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    print(f"  Wallet '{label}' already exists, skipping...")
                    continue

                wallet = Wallet(**wallet_data)
                session.add(wallet)
                await session.flush()

                # Add welcome bonus grant + transaction for every wallet
                grant = PromotionalBubbleGrant(
                    wallet_id=wallet.id,
                    member_auth_id=wallet.member_auth_id,
                    grant_type=GrantType.WELCOME_BONUS,
                    bubbles_amount=10,
                    bubbles_remaining=10,
                    reason="Welcome bonus — thanks for joining SwimBuddz!",
                    granted_by="system",
                )
                session.add(grant)
                await session.flush()

                welcome_txn = WalletTransaction(
                    wallet_id=wallet.id,
                    idempotency_key=f"welcome-bonus-{wallet.member_auth_id}",
                    transaction_type=TransactionType.WELCOME_BONUS,
                    direction=TransactionDirection.CREDIT,
                    amount=10,
                    balance_before=0,
                    balance_after=10,
                    status=TransactionStatus.COMPLETED,
                    description="Welcome bonus — thanks for joining SwimBuddz!",
                    service_source="wallet_service",
                    reference_type="grant",
                    reference_id=str(grant.id),
                    initiated_by="system",
                    created_at=NOW - timedelta(days=30),
                )
                session.add(welcome_txn)
                await session.flush()

                grant.transaction_id = welcome_txn.id

                print(f"  ✓ Created wallet: {label}")
                print(f"    - Balance: {wallet.balance} Bubbles")
                print(f"    - Status: {wallet.status.value}")

            # --- Add sample transactions for the active wallet ---
            active_wallet_id = uuid.UUID("00000000-0000-0000-0000-000000000101")
            active_auth_id = "seed-wallet-active-user"

            stmt = select(WalletTransaction).where(
                WalletTransaction.wallet_id == active_wallet_id,
                WalletTransaction.transaction_type == TransactionType.TOPUP,
            )
            result = await session.execute(stmt)
            if not result.scalar_one_or_none():
                # Completed topup
                topup = WalletTopup(
                    wallet_id=active_wallet_id,
                    member_auth_id=active_auth_id,
                    reference="TOP-SEED1",
                    bubbles_amount=500,
                    naira_amount=50000,
                    payment_method=PaymentMethod.PAYSTACK,
                    status=TopupStatus.COMPLETED,
                    payment_reference="PAY-SEED-123",
                    completed_at=NOW - timedelta(days=25),
                )
                session.add(topup)

                topup_txn = WalletTransaction(
                    wallet_id=active_wallet_id,
                    idempotency_key=f"topup-seed-1",
                    transaction_type=TransactionType.TOPUP,
                    direction=TransactionDirection.CREDIT,
                    amount=500,
                    balance_before=10,
                    balance_after=510,
                    status=TransactionStatus.COMPLETED,
                    description="Topped up 500 Bubbles (₦50,000)",
                    service_source="wallet_service",
                    reference_type="topup",
                    reference_id="TOP-SEED1",
                    initiated_by=active_auth_id,
                    created_at=NOW - timedelta(days=25),
                )
                session.add(topup_txn)

                # A purchase debit
                session.add(
                    WalletTransaction(
                        wallet_id=active_wallet_id,
                        idempotency_key=f"purchase-seed-1",
                        transaction_type=TransactionType.PURCHASE,
                        direction=TransactionDirection.DEBIT,
                        amount=15,
                        balance_before=510,
                        balance_after=495,
                        status=TransactionStatus.COMPLETED,
                        description="Club session fee — Saturday Morning Swim",
                        service_source="sessions_service",
                        reference_type="session",
                        reference_id="session-seed-123",
                        initiated_by=active_auth_id,
                        created_at=NOW - timedelta(days=20),
                    )
                )

                # Another purchase
                session.add(
                    WalletTransaction(
                        wallet_id=active_wallet_id,
                        idempotency_key=f"purchase-seed-2",
                        transaction_type=TransactionType.PURCHASE,
                        direction=TransactionDirection.DEBIT,
                        amount=10,
                        balance_before=495,
                        balance_after=485,
                        status=TransactionStatus.COMPLETED,
                        description="Swim cap from SwimBuddz Store",
                        service_source="store_service",
                        reference_type="order",
                        reference_id="order-seed-456",
                        initiated_by=active_auth_id,
                        created_at=NOW - timedelta(days=15),
                    )
                )

                print("  ✓ Added sample transactions for active wallet")

            # --- Add audit log entry for frozen wallet ---
            frozen_wallet_id = uuid.UUID("00000000-0000-0000-0000-000000000103")
            stmt = select(WalletAuditLog).where(
                WalletAuditLog.wallet_id == frozen_wallet_id
            )
            result = await session.execute(stmt)
            if not result.scalar_one_or_none():
                session.add(
                    WalletAuditLog(
                        wallet_id=frozen_wallet_id,
                        action=AuditAction.FREEZE,
                        performed_by="seed-admin",
                        reason="Suspicious activity flagged by automated check",
                        old_value={"status": "active"},
                        new_value={"status": "frozen"},
                        created_at=NOW - timedelta(days=3),
                    )
                )
                print("  ✓ Added audit log for frozen wallet")

            print("\n✓ Wallet seed data complete!")


if __name__ == "__main__":
    # Wallet seeds are dev-only — real wallets are created via the API
    db_url = os.environ.get("DATABASE_URL", "")
    env_file = os.environ.get("ENV_FILE", "")
    if ".prod" in env_file or "prod" in env_file:
        print(
            "  Skipping wallet seed data (dev-only — real wallets are created via the API)"
        )
        sys.exit(0)

    print("Seeding wallet data...")
    asyncio.run(seed_wallets())
