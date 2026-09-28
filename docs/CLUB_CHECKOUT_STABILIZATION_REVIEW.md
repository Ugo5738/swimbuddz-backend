# Club, checkout, settlement, and preorder review

Updated: 28 September 2026. Changes are prepared for review on `develop` in `swimbuddz-backend` and `swimbuddz-frontend`. Application code has **not** been deployed. The Yaba session and starter-kit data repairs described below **have** been applied and verified on production.

## Requirements and implementation

| Requirement | Implementation and review entry points |
| --- | --- |
| Complete Club template inheritance | Sessions `schemas/template_admission.py`, `services/template_operations.py`, `services/club_generation.py`, and `routers/club_schedule.py`. Pool registry supplies venue name/address. Independent guest and Community prices, admission flags/mode, cutoff relative to each occurrence, privacy, and reconciliation defaults are copied to sessions. Volunteers and transport are materialized after commit, including on generation replay. Transport preserves existing operational routes. |
| Reviewable repair of existing sessions | Sessions `routers/template_sync.py`; frontend `TemplateAdmissionFields.tsx` and `TemplatesDrawer.tsx`. Preview includes before/after values, volunteer slots, and transport configuration. Apply must supply the current preview digest. Future sessions retain dates, member prices, capacity, and published identities. Interrupted operational fanout produces explicit retry warnings. |
| Multiple recurring schedules per quarter | Members `routers/club_plan_admin.py`, Sessions `routers/club_schedule.py`, frontend `ClubQuarterRecommendation.tsx`. Select multiple active, primary, included templates belonging to the chosen Club/home pool. Duplicate/conflicting selections are rejected. Next-quarter generation recovers all templates from the actual linked sessions. No VI records or weekdays are hardcoded. |
| Automatically reserve prepaid swims | Members `services/club_reservations.py` and activation in `routers/clubs.py`; Sessions `routers/club_reservations.py`. Exact purchased plan inclusions, future dates, enrollment dates, and Club identity determine eligibility. Bookings are confirmed with zero member/session fee, `booking_source=club_quarter`, and `access_source=quarterly_prepaid`. Transition enrollments are excluded. |
| Capacity, retries, attendance, cancellation | Reservations lock sessions in stable order using the existing booking-capacity rules. Replay preserves confirmed bookings and canceled attendance. Attendance synchronization runs after the durable booking commit with bounded concurrency; failure enters existing paid-entitlement retry handling. Canceling an included reservation uses existing booking cancellation and cannot refund the separate quarter purchase. Conflicting live payments/guest parties require reconciliation rather than silent replacement. |
| Member attendance and existing purchaser repair | `/account/club` displays future prepaid swims and cancellation/rebooking actions. Admin Club Pricing has a prepaid-reservation sync action for published plans, reporting successful and failed enrollment IDs. |
| Stable Club/Academy checkout | Frontend `lib/checkoutPreparation.ts`, Academy cohort page, and checkout page. Academy enrollment is prepared before navigation; duplicate enrollment responses recover the member's matching payable/waitlisted enrollment. URL/backend identities are authoritative. Network/5xx preparation failures retry with bounds and stage logging; business/auth failures retain their explanation. Payment stays disabled during preparation/repricing. |
| Immediate installment switching | Checkout preloads independent server-confirmed full/installment quotes and preserves `billing` in the URL. Inactive quote failure does not discard the valid selected quote. Academy `payment-preview` is read-only: merely viewing installment prices no longer creates installment records. Actual payment materializes the schedule under lock and reloads the ORM relationship with `populate_existing`, fixing the stale empty-schedule/full-price fallback. Full payment sums the remaining unpaid installments. |
| Bubbles for outstanding fees | Admin payment-link generation returns `/account/billing/sessions/{bookingId}` without creating a Payment or contacting Paystack. Billing uses that same authenticated page. Booking owner and amount are verified by the backend. Full/partial Bubbles use existing session-payment settlement; cash remainder goes through Paystack. Manual bank transfer remains cash-only. Stable attempt keys also cover session settlement retries. |
| Starter-kit preorder failure | The live simple preorder product had **no SKU**, while the frontend asked for nonexistent options. Store admin creation/update now supplies a default SKU and zero-stock inventory for simple products without any existing variants, while preserving deliberately inactive variants. Frontend `lib/storeVariants.ts` and the product page derive valid choices from active SKUs, clear invalid selections, support ambiguous named variants, permit zero-stock preorders, and show actionable unavailable states. Desktop and mobile CTAs share the validated SKU. |

## Production data repairs already completed

Evidence and replay scripts remain in the shared workspace at `outputs/club-checkout-stabilization-2026-09-27/`; live audit data is not included in this repository. Repairs used owning-service APIs, without direct production SQL.

**Yaba Q4:** Club `08695c20-4bc6-4525-8eaf-f5b7d2678215`, plan `5cacdf96-df70-4f24-bf7e-17f988b8ea6f`, generated template `6789714f-e7cd-5268-a7fa-80bd1131ab84`.

- All 13 published swims, 3 October–26 December 2026, now carry Rowe Park Pool's canonical name/address, public guest rate **₦10,000**, and enabled Community drop-in rate **₦7,000**.
- Protected fields were compared before/after: session IDs, template/Club/pool IDs, times, member price (₦5,200), capacity (20), pricing configuration, publication timestamp, and status.
- Missing template volunteer roles were copied from the newer Yaba template `d9dbfc70-8837-49bb-9078-41e42b9abe32`, preserving its detailed settings: Check-in (1), Welcome (2), Gallery (1), and Media (2). This was the stated assumption after no answer to the optional volunteer question.
- Materialization created **52 opportunities** across the 13 swims. Replay created **zero** duplicates; both runs reported no warnings. All 13 sessions were verified in the admin guest-options API.
- The old production schema cannot yet persist `admission_settings` on the template. The session repair is live; **rerun the repair after the additive migration/code rollout to persist future template defaults**. See `yaba-repair-applied.jsonl`, especially `template_admission_persisted: false`.
- Existing purchasers' new automatic bookings have **not** been backfilled on production: the new endpoint requires deployment first.

**Starter kit:** product `f826dab6-521c-4c25-ac3f-cb67976e92ab`, slug `academy-beginner-starter-kit`.

- Both public and admin APIs confirmed zero variants, rather than an intentionally inactive variant.
- Added its default variant `115c6ceb-8762-4ab5-94e1-3889196f9256` through the existing Store admin endpoint; price remains **₦70,000**.
- Verified the public product exposes that SKU, then added one item to an isolated anonymous cart and checked its ₦70,000 total. Removed the cart item afterward. No order, checkout, payment, or customer message was created.
- Evidence: `store-preorder-admin-audit.jsonl`, `store-preorder-repair.jsonl`; replay script `repair_starter_kit.py`.

## Validation

Saved logs and local-only test harnesses are under the evidence folder's `validation/` directory.

| Check | Result |
| --- | --- |
| Backend regression suite across Club, templates, Store/cart, settlement, Academy pricing, capacity, entitlement, attendance, and volunteers | **230 passed** |
| Academy internal integration, in a separate process | **20 passed** |
| Final targeted checks after timeout/concurrency and multi-template validation work, including transport preservation | **31 passed**; overlaps the preceding suites |
| Frontend regressions across checkout, billing, Club admin/member selection, template/session editors, preorder, and product variants | **102 passed in 18 files** |
| Additive migration on disposable PostgreSQL | Upgrade, existing-row `{}` default, downgrade, and re-upgrade passed |
| Python formatting/lint | All 39 changed/new Python files passed; unrelated pre-existing scripts excluded |
| TypeScript | `tsc --noEmit` passed |
| Targeted frontend lint | No errors; file-length warnings remain in large existing components |
| OpenAPI/types and whitespace | Regenerated schema/types; backend and frontend schema copies match; both repository diffs pass whitespace checks |

Backend integration ran against disposable local PostgreSQL on port 55439. The harness disables dotenv loading so the repository's test configuration cannot redirect these runs to its configured remote development database. Academy integration runs separately because importing certain full Member models together with service-local Member reference stubs causes mapper conflicts in the existing multi-service test setup. The local `validation/README.md` records commands and scope. Regression tests themselves are committed under `tests/` and in the frontend repository.

Payment providers and downstream service failures were mocked in automated tests. The storefront check above exercised the live product/cart APIs. No real money transaction or browser end-to-end checkout was performed. Frontend mobile/desktop preorder tests exercise the corresponding CTA logic; they are component tests, not visual browser screenshots.

## Rollout sequence and remaining production steps

1. Review the working-tree changes and existing deployment configuration. The pre-existing edit to backend `.github/workflows/deploy.yml` belongs to other work and was left unchanged by this task.
2. Apply Sessions migration `82f070b47973` (parent `bd99897b8e40`) before running the new Sessions code. The production migration wrapper is `./scripts/db/migrate-prod.sh sessions_service .env.prod`; it upgrades existing migrations and does not reset data. The migration adds only `session_templates.admission_settings JSONB NOT NULL DEFAULT '{}'`. It was handwritten because the configured development database was unreachable during generation, then tested on disposable PostgreSQL.
3. Roll out the Transport `preserve_existing` handling and Volunteer locking before new Sessions operational fanout. Roll out Academy's read-only `payment-preview` endpoint before the Payments caller. Roll out Sessions' reservation endpoint before Members' prepaid activation. Update corresponding payment/fulfillment workers along with their service code.
4. Roll out the remaining Members, Payments, Store, and frontend changes after their dependencies. Keep existing payment retry/reconciliation workers enabled. Application-level Club activation now allows 120 seconds; Members' reservation call allows 90 seconds; attendance fanout is bounded to eight concurrent requests.
5. Run `repair_yaba_q4.py` from the Sessions service environment first in preview mode, then with `APPLY_REPAIR=yes`. Confirm its final verification reports `template_admission_persisted: true`, 13 eligible guest sessions, and zero duplicate volunteer creations on replay.
6. Use Admin Club Pricing's **Sync prepaid reservations** for Yaba Q4 (or POST `/clubs/admin/plans/5cacdf96-df70-4f24-bf7e-17f988b8ea6f/sync-prepaid-reservations` with admin authorization). Check both returned enrollment-ID lists. Resolve any capacity or live-payment/guest-party conflicts before retrying failures. This task has not run that new endpoint against old production code.
7. Smoke-check Club checkout, Academy full/installment switching and refresh, outstanding-fee Bubbles/online/manual choices, a prepaid member's swim list/cancellation, and the starter kit on mobile and desktop. Generate a VI draft using the administrator's selected recurring templates when ready; no VI data was invented during this task.

Reservations are fulfilled after payment through the existing entitlement mechanism. This change does not add a new cross-service pre-purchase seat-hold system for every session. A capacity or incompatible existing-booking conflict remains a visible paid-fulfillment reconciliation case; the implementation does not overbook or overwrite someone else's payment to hide it.

## Reviewer focus

Read the new Club fulfillment integration tests, installment preview/relationship-refresh tests, settlement ownership/idempotency tests, transport preservation test, and preorder component tests alongside the implementations. Verify money units (template/admin fees in naira, stored booking/session charges in kobo), exact purchased session inclusion, cancellation replay behavior, independent guest/Community prices, and API rollout compatibility.

Unrelated pre-existing deployment edits, payment-adjustment scripts/logs, and root `docs/API_ENDPOINTS_GENERATED.md` changes remain outside this task. Publishing the review commits to `develop` is separate from application deployment; no application deployment was made.
