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

Mixed Bubbles plus Paystack settlement is disabled until `wallet_service`
supports atomic holds with capture and release. Subtracting Bubbles from the
Paystack amount and debiting the wallet only after the callback creates an
underpayment race when the wallet balance changes.

Current supported behavior:

- A single session or ride may be paid fully with Bubbles through the direct
  booking flow, which uses an idempotent server-side wallet debit.
- Direct wallet charges convert kobo with `kobo_to_bubbles_for_charge`, rounding
  a legacy non-whole-Bubble price up so a purchase can never be under-collected.
  Product prices should still be configured in whole-Bubble increments.
- Refund conversion remains the explicitly named floor conversion. Changing
  historical refund rounding is a separate product and accounting decision.
- A payment intent charges the full provider amount and rejects
  `bubbles_to_apply` for session and ride purposes.
- Session bundles are provider-only until wallet holds are implemented.
- Store checkout still permits mixed Bubbles and Paystack. It must be migrated
  to wallet hold/capture/release before that path can claim the same atomic
  settlement guarantee as session checkout.
- Historical mixed-tender payments retain idempotent wallet collection, but a
  failed wallet debit blocks entitlement rather than silently undercharging.

## Idempotency and Audit

- Provider callbacks, wallet debits, booking confirmation, and entitlement
  application must each have a stable idempotency key or ownership check.
- A payment can be marked paid once. Entitlement retries use persisted attempt
  state and must not repeat a successful debit or booking.
- Ambiguous external outcomes are not treated as success. Operational review,
  reconciliation, and refund paths must retain the payment and provider
  references needed to resolve them.
