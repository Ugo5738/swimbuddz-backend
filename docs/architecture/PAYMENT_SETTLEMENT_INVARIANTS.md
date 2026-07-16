# Payment Settlement Invariants

These rules apply to every SwimBuddz checkout and entitlement flow.

## Server-Owned Price

- The service that owns the product owns its unit price and validates the
  selected resource. Payments must obtain a typed server quote.
- A client-supplied total is only a stale-price check. It must never determine
  the amount charged or persisted.
- A bundle quote must contain exactly one validated line for every requested
  session and ride selection. Line identifiers, quantities, non-negative
  amounts, and totals must reconcile before checkout starts.

## Reserve Before Charge

- Capacity-limited sessions are reserved before an external checkout is
  initialized.
- Reservations are bound to the member and payment intent, have a finite TTL,
  and are released when intent creation fails.
- A delayed paid callback may restore an expired reservation only while the
  session is still scheduled, upcoming, and has capacity.
- A cancelled or started session must not be confirmed merely because an old
  payment callback arrives. The payment is retained for operational review and
  refund; fulfillment fails closed.

## Eligibility Before and During Fulfillment

- `sessions_service` owns the authoritative member/session access decision.
  Payments preflight that decision before charging legacy day-of session fees
  or standalone transport.
- Standalone ride share requires a confirmed session booking. Transport checks
  this again when it creates the ride booking.
- A confirmed booking preserves attendance eligibility if membership later
  expires, but it does not override cancellation or the self check-in window.
- Admin, coach, and service walk-ins use explicit operational endpoints or
  overrides. Member endpoints must not infer those privileges.

## Bubbles and External Providers

Mixed Bubbles plus Paystack settlement uses a wallet hold. The hold is created
before provider initialization, reduces the member's available balance, and is
captured only after provider success. Terminal provider or initialization
failure releases it; abandonment is bounded by the hold expiry.

- One Bubble always represents exactly NGN 100. Neither checkout nor a direct
  wallet charge may round that exchange rate.
- Mixed checkout applies at most `floor(amount / NGN 100)` whole Bubbles and
  charges the exact remainder through Paystack. For example, NGN 150 is one
  Bubble plus NGN 50, never two Bubbles.
- A wallet-only direct charge is allowed only when the amount is exactly
  representable in whole Bubbles. Otherwise the member must use mixed or card
  checkout.
- Session, session-bundle, ride-share, and store mixed payments use the same
  hold/capture/release contract.
- Ordinary wallet debits and balance checks use available balance (ledger
  balance minus live holds), so another purchase cannot spend reserved funds.
- If a provider confirms payment after the hold TTL, capture may reacquire the
  amount under the wallet lock only when unreserved funds still cover it.
  Otherwise fulfillment fails closed for reconciliation or refund.
- Refund conversion remains the explicitly named floor conversion. Changing
  historical refund rounding is a separate product and accounting decision.
- Historical mixed-tender payments retain idempotent wallet collection, but a
  failed wallet debit blocks entitlement rather than silently undercharging.

## Transport Passenger Manifests

- Seat capacity is based on the transport passenger manifest, not session
  attendance or swimming guest count.
- Each occupied seat is classified as `member`, `session_guest`, or `observer`;
  a passenger name is optional.
- The manifest must contain exactly one entry per reserved seat and no more
  than one `member` entry for the booking owner.
- Legacy callers that provide only `num_seats` are normalized to one member and
  anonymous observers, preserving existing bookings while callers migrate.

## Idempotency and Audit

- Provider callbacks, wallet debits, booking confirmation, and entitlement
  application must each have a stable idempotency key or ownership check.
- A payment can be marked paid once. Entitlement retries use persisted attempt
  state and must not repeat a successful debit or booking.
- Ambiguous external outcomes are not treated as success. Operational review,
  reconciliation, and refund paths must retain the payment and provider
  references needed to resolve them.
