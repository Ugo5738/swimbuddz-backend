"""
Paystack API client for transfers and bank account verification.

Provides async methods for:
- Listing Nigerian banks
- Resolving/verifying bank accounts
- Creating transfer recipients
- Initiating transfers
- Verifying transfer status
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

import httpx
from libs.common.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

PAYSTACK_BASE_URL = "https://api.paystack.co"


@dataclass
class Bank:
    """Nigerian bank info from Paystack."""

    name: str
    code: str
    slug: str
    is_active: bool


@dataclass
class ResolvedAccount:
    """Result of bank account verification."""

    account_number: str
    account_name: str
    bank_code: str


@dataclass
class TransferRecipient:
    """Paystack transfer recipient."""

    recipient_code: str
    name: str
    account_number: str
    bank_code: str
    bank_name: str


@dataclass
class TransferResult:
    """Result of initiating a transfer."""

    transfer_code: str
    reference: str
    status: str  # pending, success, failed
    amount: int  # in kobo
    currency: str


class PaystackError(Exception):
    """Base exception for Paystack API errors."""

    def __init__(
        self, message: str, status_code: int = None, response_data: dict = None
    ):
        self.message = message
        self.status_code = status_code
        self.response_data = response_data or {}
        super().__init__(message)


class PaystackClient:
    """Async client for Paystack Transfer and Verification APIs."""

    def __init__(self, secret_key: str = None):
        self.secret_key = secret_key or settings.PAYSTACK_SECRET_KEY
        if not self.secret_key:
            raise ValueError("PAYSTACK_SECRET_KEY is required")
        self._headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict = None,
        json_data: dict = None,
    ) -> dict:
        """Make an async request to Paystack API."""
        url = f"{PAYSTACK_BASE_URL}{endpoint}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=self._headers,
                params=params,
                json=json_data,
            )

            data = response.json()

            if not response.is_success:
                logger.error(f"Paystack API error: {response.status_code} - {data}")
                raise PaystackError(
                    message=data.get("message", "Unknown Paystack error"),
                    status_code=response.status_code,
                    response_data=data,
                )

            if not data.get("status"):
                raise PaystackError(
                    message=data.get("message", "Paystack request failed"),
                    response_data=data,
                )

            return data

    # =========================================================================
    # Bank Methods
    # =========================================================================

    async def list_banks(self, country: str = "nigeria") -> List[Bank]:
        """
        Get list of banks supported by Paystack.

        Args:
            country: Country code (nigeria, ghana, south_africa, kenya)

        Returns:
            List of Bank objects with name, code, slug
        """
        data = await self._request(
            "GET",
            "/bank",
            params={"country": country, "perPage": 100},
        )

        banks = []
        for bank_data in data.get("data", []):
            banks.append(
                Bank(
                    name=bank_data["name"],
                    code=bank_data["code"],
                    slug=bank_data.get("slug", ""),
                    is_active=bank_data.get("active", True),
                )
            )

        return banks

    async def resolve_account(
        self,
        account_number: str,
        bank_code: str,
    ) -> ResolvedAccount:
        """
        Verify a bank account and get the account holder's name.

        This is FREE to use and should be called before saving bank details.

        Args:
            account_number: 10-digit NUBAN account number
            bank_code: Bank code from list_banks()

        Returns:
            ResolvedAccount with verified account_name

        Raises:
            PaystackError: If account cannot be verified
        """
        data = await self._request(
            "GET",
            "/bank/resolve",
            params={
                "account_number": account_number,
                "bank_code": bank_code,
            },
        )

        account_data = data.get("data", {})
        return ResolvedAccount(
            account_number=account_data.get("account_number", account_number),
            account_name=account_data.get("account_name", ""),
            bank_code=bank_code,
        )

    # =========================================================================
    # Transfer Recipient Methods
    # =========================================================================

    async def create_transfer_recipient(
        self,
        account_number: str,
        bank_code: str,
        name: str,
        description: str = None,
    ) -> TransferRecipient:
        """
        Create a transfer recipient for future payouts.

        The recipient_code returned should be stored and reused for transfers.

        Args:
            account_number: 10-digit NUBAN
            bank_code: Bank code
            name: Recipient name (usually verified account_name)
            description: Optional description

        Returns:
            TransferRecipient with recipient_code
        """
        data = await self._request(
            "POST",
            "/transferrecipient",
            json_data={
                "type": "nuban",
                "name": name,
                "account_number": account_number,
                "bank_code": bank_code,
                "currency": "NGN",
                "description": description or f"Coach payout recipient: {name}",
            },
        )

        recipient = data.get("data", {})
        details = recipient.get("details", {})

        return TransferRecipient(
            recipient_code=recipient.get("recipient_code", ""),
            name=recipient.get("name", name),
            account_number=details.get("account_number", account_number),
            bank_code=details.get("bank_code", bank_code),
            bank_name=details.get("bank_name", ""),
        )

    # =========================================================================
    # Transfer Methods
    # =========================================================================

    async def initiate_transfer(
        self,
        recipient_code: str,
        amount_kobo: int,
        reason: str,
        reference: str = None,
    ) -> TransferResult:
        """
        Initiate a transfer to a recipient.

        The transfer status will be sent via webhook (transfer.success/transfer.failed).

        Args:
            recipient_code: From create_transfer_recipient()
            amount_kobo: Amount in kobo (Naira * 100)
            reason: Description for the transfer
            reference: Optional unique reference (auto-generated if not provided)

        Returns:
            TransferResult with transfer_code and initial status
        """
        payload = {
            "source": "balance",
            "recipient": recipient_code,
            "amount": amount_kobo,
            "reason": reason,
        }

        if reference:
            payload["reference"] = reference

        data = await self._request("POST", "/transfer", json_data=payload)

        transfer = data.get("data", {})
        return TransferResult(
            transfer_code=transfer.get("transfer_code", ""),
            reference=transfer.get("reference", ""),
            status=transfer.get("status", "pending"),
            amount=transfer.get("amount", amount_kobo),
            currency=transfer.get("currency", "NGN"),
        )

    async def verify_transfer(self, reference: str) -> TransferResult:
        """
        Check the status of a transfer by reference.

        Args:
            reference: Transfer reference from initiate_transfer()

        Returns:
            TransferResult with current status
        """
        data = await self._request("GET", f"/transfer/verify/{reference}")

        transfer = data.get("data", {})
        return TransferResult(
            transfer_code=transfer.get("transfer_code", ""),
            reference=transfer.get("reference", reference),
            status=transfer.get("status", "pending"),
            amount=transfer.get("amount", 0),
            currency=transfer.get("currency", "NGN"),
        )

    async def get_balance(self) -> int:
        """
        Get current Paystack balance in kobo.

        Returns:
            Balance in kobo (divide by 100 for Naira)
        """
        data = await self._request("GET", "/balance")

        balances = data.get("data", [])
        for bal in balances:
            if bal.get("currency") == "NGN":
                return bal.get("balance", 0)

        return 0


# Singleton instance for convenience
def get_paystack_client() -> PaystackClient:
    """Get a PaystackClient instance."""
    return PaystackClient()
