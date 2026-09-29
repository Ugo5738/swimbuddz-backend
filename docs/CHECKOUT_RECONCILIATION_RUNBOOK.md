# Checkout review follow-up and reconciliation

Updated 29 September 2026. This follow-up changes code only. No production migration, deployment, payment, or backfill was performed during this review pass. It follows the earlier [stabilization handoff](CLUB_CHECKOUT_STABILIZATION_REVIEW.md).

## Guarantees and review entry points

- **One payable attempt per session booking:** `services/payments_service/services/booking_payment_attempts.py` serializes by booking identity, including legacy JSON-only booking IDs. Browser retry keys do not override that identity. Compatible pending attempts resume their existing reference/link; incompatible options or multiple historical links require reconciliation. Paid receipts block another payment while fulfillment is pending. Provider initialization uncertainty retains the original reference. Offline recording takes the same booking lock. Internal booking initialization delegates to the same intent path; callers must use its returned reference, which may resume an existing attempt.
- **Duplicate receipts remain auditable:** callbacks record money received but flag a second booking receipt for Admin reconciliation, without a second Bubbles debit or entitlement. Worker/Admin replay uses the same guard. A late provider failure cannot overwrite an already-paid receipt. This does not refund or erase either receipt.
- **Prepaid seats before payment:** Members reserves all selected plans and asks Sessions to reserve every exact future included session. Sessions locks all session rows in UUID order in one transaction, sharing capacity accounting with normal bookings and guests. The input is the published plan's session IDs, including multiple selected recurring templates and venue overrides.
- **Protection policy chosen by the owner:** unused active holds expire after 30 minutes. Before an online payment request, bank instructions, or zero-cash settlement is exposed, plan/pod and session holds become `protected`. They remain capacity-consuming regardless of expiry until consumed by fulfillment or released after verified provider closure. A browser abandonment, local `FAILED` status, Paystack `abandoned` result, or timeout is **not** proof that payment cannot arrive. Paystack's [transaction API](https://paystack.com/docs/api/transaction/) does not document a hosted-checkout cancellation/expiry operation; this implementation does not invent one.
- **Fulfillment:** protected holds convert to the existing confirmed zero-fee `club_quarter` / `quarterly_prepaid` bookings. Replay preserves explicit cancellations and repairs attendance after interrupted fanout. Legacy paid enrollments without holds use capacity-checked reconciliation. Transition members remain opt-in per swim.
- **Academy:** `src/lib/academyBillingQuotes.ts` in the frontend preloads authoritative prices with and without Bubbles for both billing modes. An oversized selection resets visibly to zero; valid Bubbles survive mode changes, including full installment coverage. Toggling does not issue another quote request.
- **Admin links:** generating the authenticated settlement URL creates neither a Payment nor a provider transaction. `reference` and `payer_email` are nullable; email is required only when initializing an online payment.

## Migration and rollout order

Inspect each owning service's current revision first. Use the repository production migration wrapper; never use reset/seed scripts on production. This runbook is a deployment procedure, not evidence that deployment has happened.

1. Temporarily pause new checkout creation during the coordinated service rollout. Continue preserving incoming provider receipts; reconcile historical exposed links before reopening sales where capacity is contested.
2. Apply the Sessions chain through `e30550028159`. Its parent is `82f070b47973` (template admission JSON defaults), whose parent is `bd99897b8e40`. The new table is `club_session_holds`, unique on `(payment_reference, session_id)`, with capacity and reference indexes and a status constraint. Existing sessions/bookings/payments are not rewritten.
3. Apply the Members chain through `1cbbd94c744f`, parent `e9f1a3b5c726`. This expands `club_enrollment_reservations`' status constraint to include `protected`, without changing rows. Earlier Club plan/session/application migrations remain in the existing single-head chain; do not copy individual DDL fragments or stamp over unapplied migrations.

```sh
./scripts/db/migrate-prod.sh sessions_service .env.prod
./scripts/db/migrate-prod.sh members_service .env.prod
```

4. Roll out Sessions before Members, then Payments and its webhook/reconciliation/fulfillment workers, then frontend. All booking/guest writers must run the version that counts Club holds. Keep the prior Transport/Volunteer operational-repair and Academy preview dependencies from the stabilization handoff.
5. Verify the two new revisions, internal hold endpoints, admin preview authorization, and a test quarter in staging. Re-enable new purchases after all writers use the same capacity predicate.

Both new migrations reject destructive downgrade with live/protected reservations. Do not roll back to old booking writers while protected holds exist: old code ignores those seats. Reconcile first or roll forward. Local migration tests upgrade, preserve pre-existing template/reservation rows, reject unsafe downgrade, then downgrade/re-upgrade in an isolated schema.

## Legacy session payment links

Use the Admin payment list to obtain the booking ID/reference. With an Admin token, gateway paths are:

```http
GET /api/v1/payments/admin/checkout-reconciliation/bookings/{booking_id}
GET /api/v1/payments/admin/checkout-reconciliation/{reference}
```

These reads return immutable-current-state previews and a digest; they do not initialize a provider transaction. The booking preview includes both column-based and legacy metadata-based attempts. For inventory, the following read-only query on the Payments database finds candidates without changing their history:

```sql
SELECT id, reference, status, amount, provider, provider_reference,
       COALESCE(session_booking_id::text, payment_metadata->>'booking_id') AS booking_id,
       entitlement_applied_at, entitlement_error
FROM payments
WHERE lower(purpose::text) = 'session_booking'
ORDER BY created_at, id;
```

Review each booking against the provider and the Sessions booking. A single compatible pending link is reused automatically by member checkout. Changing an idempotency key cannot create another attempt. If a receipt is paid but fulfillment is pending, use the existing entitlement replay/retry process; do not ask the member to pay again. Multiple historical payable links block new checkout until reviewed. If more than one actually collected money, retain both receipts and resolve the duplicate financially using the normal audited refund process; entitlement replay intentionally cannot clear the duplicate marker.

### Close an unpaid attempt only after verified non-payability

The endpoint below does **not** cancel Paystack. Obtain evidence from the provider that the specific reference cannot collect money, or verify and document the corresponding manual-transfer closure. If that cannot be established, keep the seats/payment protected. Do not submit a mere local timeout or abandoned status as evidence.

Copy the token from the **single-reference** GET, then preview:

```http
POST /api/v1/payments/admin/checkout-reconciliation/{reference}/close-unpaid
Content-Type: application/json

{
  "preview_token": "TOKEN_FROM_SINGLE_REFERENCE_PREVIEW",
  "provider_closure_evidence": "Provider support case and verified closure details",
  "note": "Operator explanation and evidence location",
  "apply": false
}
```

After review, send the same body with `apply: true`. The endpoint records actor/time/evidence in `checkout_closed_unpaid`, marks only the unpaid attempt failed, and releases Club capacity where applicable. Wallet release is best effort and should be checked in the Wallet service if the payment held Bubbles. A stale token or paid/fulfilled receipt is rejected. Repeating the same applied request is safe; if remote capacity release fails, the audit remains committed and the same request finishes the release on retry. Never delete the payment or edit its amount to achieve this.

A receipt arriving despite documented closure is still recorded as paid with `checkout_reconciliation`, blocked from automatic fulfillment, and left for financial review. Refresh the Admin payment/fulfillment queue after applying repairs.

## Historical Club holds and purchases

New checkouts acquire holds automatically. Expired unused `active` rows stop counting without a cleanup job; retain rows for audit. `protected` rows continue counting. Read-only inventory in the owning Sessions database:

```sql
SELECT payment_reference, application_id, member_id, status,
       min(expires_at) AS expires_at, count(*) AS sessions
FROM club_session_holds
GROUP BY payment_reference, application_id, member_id, status
ORDER BY payment_reference, status;
```

Cross-check exposed references with their Payments previews. Never bulk expire protected rows. Legacy pending Club links created before this feature have no retrospective seat guarantee: resuming them reacquires exact seats before returning their URL. During rollout, reconcile those links with the provider and available capacity. Owning-service operators can idempotently POST the original reference/mode to `/clubs/internal/applications/{application_id}/reservation` and then `/reservation/protect` with service credentials to protect a still-payable legacy checkout. Preserve any original Experience selection/fee. If capacity cannot be acquired, stop selling the conflicting seats and resolve the historical promise; do not fabricate a hold, mark the payment paid, or replace the published plan.

For paid historical enrollments, preview the existing repair:

```http
POST /api/v1/clubs/admin/plans/{plan_id}/sync-prepaid-reservations?dry_run=true
```

It returns candidate active prepaid enrollment IDs and exact included session IDs, without bookings, attendance calls, or money changes. It is an inventory preview, not a capacity reservation. Review the plan schedule and candidates, then call the same path with `dry_run=false` (also the backward-compatible default used by Admin Club Pricing). Review both `synced_enrollment_ids` and `failed_enrollment_ids`. Retry is idempotent. Past swims and deliberate attendance cancellations remain excluded. Existing live session payments/guest parties or unavailable capacity require review instead of overwriting another commercial transaction.

## Generated-session operational repair

Save reviewed admission/volunteer settings on each selected template. Use Admin Session Templates → **Repair generated sessions**, or POST `/api/v1/sessions/templates/{template_id}/sync-operations` with `from_date` and `to_date`. The first call previews changes. Apply by repeating with the returned `preview_token`; refresh the preview after any template/session change. Prices, dates, capacity, and sold identities stay intact. Review volunteer/transport warnings and repeat after fixing any downstream failure. The earlier Yaba values remain public guest ₦10,000 and Community drop-in ₦7,000; they are data settings, not domain-code constants. No VI schedules are hardcoded.

## Validation

- 335 backend regression tests in the main stabilization manifest, including independent PostgreSQL transactions for competing quarter purchases and booking payment attempts, internal initialization, offline receipts, two-template generation/holds, callbacks, Bubbles retries, and migration guards.
- 26 separate Members/Academy integration tests, including protected plan expiry and read-only historical preview.
- 105 frontend tests in 18 files; TypeScript and targeted lint passed (existing file-length warnings only).
- OpenAPI and frontend types regenerated together. Providers and downstream services are mocked in automated tests; no real payment or production browser checkout was performed.

Workspace evidence: `outputs/checkout-review-followup-2026-09-29/validation/`. Tests live in both repositories. The last verification counts should be read with the saved manifests; repeated targeted runs are not additional unique tests.
