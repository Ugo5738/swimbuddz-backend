# SwimBuddz Product & Architecture Decision Log (`DECISIONS.md`)

> **MANDATORY FOR ALL AI AGENTS (ChatGPT / Codex, Claude, Gemini / Antigravity, Cursor, Copilot):**
> 1. **Read Before Changing Behavior:** Every decision below (`ADR-001` through `ADR-016`) was reached after thorough product, operational, financial, and architectural deliberation across our GitHub history. **Do NOT revert, bypass, or "simplify" these rules** when fixing bugs or building features.
> 2. **Always Document New Decisions ("Deliberate → Document → Code"):** Whenever you and the owner deliberate on a product flow, pricing rule, accounting boundary, curriculum model, or architecture choice, **you MUST record or update the decision and its rationale ("Why") here and in the relevant domain doc in the same session/PR.**
> 3. **Superseding a Decision:** Only change an existing decision if the owner explicitly instructs you to supersede it. When superseding, document why the old rule was replaced rather than silently deleting context.

---

## ADR-001: Independent Annual Membership vs. Participation Programmes (Club & Academy)
- **Status:** Settled & Enforced (Sep–Oct 2026, PRs `#297`, `#31`)
- **Context & Why:**
  - Early versions modeled `community -> club -> academy` as a single hierarchical rank (`membership_tier`). In reality, a swimmer can be an active Annual SwimBuddz Member (`community`, ₦20,000/year) while participating in **Academy**, **Club**, both, or neither. Treating them as a single rank caused Academy enrollment to overwrite Club status and vice versa, and broke quarterly reporting when `primary_tier` drifted from actual enrollments.
- **Decision & Non-Negotiable Invariants:**
  - **Annual Membership (`community`)** is the base community/access layer (`community_paid_until`).
  - **Club** and **Academy** are independent dated participation programmes.
  - Admin member views (`/admin/members`), member billing (`/account/billing`), and quarterly reports project **Annual Membership**, **Club programme status** (from dated location-specific `ClubEnrollment` rows), and **Academy programme status** independently via `build_membership_status_summary`.
  - Never treat Academy and Club as mutually exclusive ranks or infer active Club placement solely from legacy `primary_tier` strings.
  - **Included Annual Membership Bundling (`membership_policy = 'included'`):**
    - Cohorts configured with `membership_policy = 'included'` (such as Beginner Freestyle and Accelerated Swimming Programme) bundle a 12-month Annual Community Membership (`community_paid_until`) upon tuition settlement.
    - Couple packages (e.g., Chiamaka & Tuyo) entitle both registered partners to the cohort's `included` Annual Community Membership equally, maintaining aligned annual membership coverage even across cohort transfer rectifications.
- **Canonical Docs & Code:**
  - `swimbuddz-backend/services/members_service/services/membership_status.py`
  - `swimbuddz-backend/docs/CLUB_ONBOARDING_GUEST_PASSES_AND_CHARGES.md`
  - `docs/community/TIER_BOUNDARY_POLICY.md`

---

## ADR-002: Negotiated Commercial Terms vs. Promotional Discounts (Academy, Club & Sessions)
- **Status:** Settled & Enforced (Oct 2026, PRs `#334`, `#366`, `#370`, `#382`, `#137`, `#142`, `#155`)
- **Context & Why:**
  - Some swimmers negotiate custom tuition totals or custom installment splits (e.g., ₦100,000 instead of ₦170,000, or a 3-part ₦45k/₦45k/₦35k split on a ₦165k cohort) or a temporary agreed per-session Club rate (e.g., ₦10,000 instead of ₦15,000 on a transition-per-session enrollment).
  - Using promotional discount codes or editing the cohort/session public price corrupts financial reporting, leaks private arrangements, or misstates list vs. contracted revenue.
- **Decision & Non-Negotiable Invariants:**
  - **Academy (`AcademyCommercialAdjustment`):**
    - The student registers, completes onboarding, and creates their cohort enrollment (`pending_approval`) first—**before** paying.
    - Admin opens `/admin/academy/enrollments/[id]` → **Negotiated tuition terms** (`AcademyCommercialTerms.tsx`) and records the agreed total + explicit installment schedule + audit reason (`POST /api/v1/academy/admin/enrollments/{id}/commercial-terms`).
    - Custom installment counts cannot exceed the cohort's configured `installment_count` (increase the cohort's `installment_count` first if needed).
    - Once any tuition payment has been recorded on an enrollment, pre-payment commercial terms are locked (`409 Conflict`).
  - **Club (`ClubCommercialAdjustment`):**
    - Belongs strictly to a **dated `transition_per_session` `ClubEnrollment`** within an explicit validity window (`valid_from` to `valid_until`), never to the member's global identity or public session catalogue.
    - Applies only to new, regular `plan_included` Club practice quotes inside that window. Separately paid add-on swims (`paid_addon`, where all Club members pay), visitor swims (`club_visit`), and already-settled bookings are never repriced by enrollment commercial terms.
  - **Session Extra / Walk-in Price Overrides:**
    - When an admin overrides a session booking fee, the backend validates against the authoritative standard rate, snapshots both `standard_fee_kobo` and `agreed_fee_kobo` on the booking, uses the agreed fee in rosters and confirmation emails, and requires an explicit audit justification.
- **Canonical Docs & Code:**
  - `swimbuddz-backend/docs/CLUB_COMMERCIAL_TERMS_AND_FINANCE.md`
  - `swimbuddz-backend/docs/ACADEMY_ADMIN_COMMERCIAL_TERMS_RUNBOOK.md`
  - `swimbuddz-backend/services/academy_service/routers/enrollments/commercial_terms.py`
  - `swimbuddz-backend/services/members_service/routers/club_commercial_terms.py`

---

## ADR-003: Stage-First Bank Transfer Receipts, Untrusted OCR Hints & Pooled Receipt Allocations
- **Status:** Settled & Enforced (Oct 2026, PRs `#310`, `#312`, `#314`, `#317`, `#383`, `#391`, `#46`, `#47`, `#49`, `#51`, `#156`, `#166`)
- **Context & Why:**
  - Swimmers frequently pay by direct bank transfer (sometimes before creating a checkout, during walk-in attendance, or sending one combined transfer for a couple/multiple items). Previously, conflating "receipt uploaded" with "payment settled" risked granting entitlements on unverified screenshots or double-counting one bank transfer across two enrollments.
- **Decision & Non-Negotiable Invariants:**
  - **Uploading a receipt never settles a payment:** Both customer receipt uploads (`/checkout` or private transfer link) and admin walk-in/offline evidence staging (`stage-bank-evidence` in Attendance or Payments) place the payment/booking into `pending_review` (`proof_submitted`) without collecting or settling payment.
  - **OCR hints are untrusted:** Best-effort OCR extracted from image/PDF receipts is displayed to admins as review hints only and **never** auto-approves settlement.
  - **Verified settlement requires bank credit confirmation:** Only an admin confirming actual bank credit and recording the unique external bank reference settles the payment (`PAID`) and triggers entitlement fulfillment + ledger posting. Idempotent admin retries require a matching settlement reference.
  - **Shared / Pooled Bank Receipts (`AcademyBankReceipt` + `AcademyReceiptAllocation`):**
    - One external bank transfer is registered **once** in `payments_service` (posting a single cash-in ledger entry), including retroactive adoption of already-verified transfer receipts.
    - Individual swimmer portions are reserved and applied via `AcademyReceiptAllocation` → `AcademyFinancialCredit` in `academy_service` without posting duplicate revenue.
  - **Official Bank Transfer Receiving Account (`BANK_TRANSFER_ACCOUNT`):**
    - The official corporate account displayed across the app (Checkout, Member Billing, Session Sign-in, Private Transfer links) is **Stanbic IBTC, Account Name: SWIMBUDDZ LIMITED, Account Number: 0079760600** (historically Moniepoint MFB, `6567710856`).
    - Frontend imports `BANK_TRANSFER_ACCOUNT` from `@/lib/bank-transfer` to guarantee single-source-of-truth display across all payment touchpoints.
- **Canonical Docs & Code:**
  - `swimbuddz-backend/docs/MANUAL_PAYMENT_RECORDING.md`
  - `swimbuddz-backend/docs/ACADEMY_BANK_RECEIPT_RECONCILIATION_RUNBOOK.md`
  - `swimbuddz-backend/docs/SESSION_EXTRA_CLASSES_AND_PAYMENT_RECONCILIATION.md`
  - `swimbuddz-backend/docs/academy/ACADEMY_TRANSFER_PAID_RECEIPT_RUNBOOK.md`

---

## ADR-004: Academy Attendance Defaults, Per-Student Session Participation & Class Consumption
- **Status:** Settled & Enforced (Oct 2026, PRs `#350`, `#372`, `#374`, `#375`, `#378`, `#380`, `#386`, `#396`, `#99`, `#144`, `#151`, `#159`, `#175`, `#177`)
- **Context & Why:**
  - Requiring coaches to manually tick every enrolled Academy student as `Present` for every regular class caused missing attendance records, breaking media access, progress tracking, and class-balance calculations.
  - Furthermore, a single physical pool session can have Student A attending on their prepaid 12-class package (`prepaid`) while Student B attends the exact same physical session as a paid extra class (`paid_extra`). Creating two duplicate sessions for the same pool and time fragmented rosters, media vaults, and capacity.
- **Decision & Non-Negotiable Invariants:**
  - **Regular Included Classes (`cohort_fee_mode = included`):**
    - Enrolled, cleared Academy students default to **`Present`** once a started class is reconciled by the Attendance worker (`reconcile_academy_defaults`).
    - Coaches, admins, and students (via **Update my Academy attendance** on their class card before class ends) record exceptions (`Absent`, `Excused`, `Late`).
    - Existing saved exceptions (`Absent`, `Excused`, `Late`, `Cancelled`) **always win** and are never overwritten by the worker.
  - **Class Balance Consumption (`AcademyClassPlan` & `AcademyClassAllocation`):**
    - `Excused` absences (≥24h notice) and `Cancelled` classes do **not** consume a student's class entitlement. Chargeable `Absent` (no-show / short notice) and `Present`/`Late` consume class usage once verified.
    - Historical class consumption requires a verified opening balance cutoff (`opening_consumed` / cutoff timestamp); never silently default existing paid learners to zero previous classes or 12 free new classes.
  - **Per-Student Session Participation on One Physical Session (`ACADEMY_PER_STUDENT_SESSION_PARTICIPATION.md`):**
    - **Never create two sessions** for the same physical cohort class. Create one Session and classify each learner's participation (`prepaid` vs. `paid_extra`) via `AcademyClassAllocation`.
    - For `paid_extra` classes, do not auto-populate unscheduled cohort members as Present; however, each confirmed booked participant defaults to `Present` on the attendance roster unless marked otherwise.
    - Visiting non-cohort members at an Academy session are priced as guests; enrolled cohort learners are blocked from bypassing class entitlement classification via generic member walk-in forms.
    - **Cohort Roster Attendance & Member Self-Service PDF Export:** Enrolled Academy cohort students display "Included in tuition" on session attendance rosters and their status dropdown (`Present`, `Absent`, `Excused`, `Late`) is always editable by coaches/admins regardless of whether an individual drop-in booking row exists. Members can download an official branded PDF attendance record from `/account/attendance/history` at any time.
- **Canonical Docs & Code:**
  - `swimbuddz-backend/docs/ACADEMY_ATTENDANCE_DEFAULTS.md`
  - `swimbuddz-backend/docs/ACADEMY_ATTENDANCE_CLASS_ACCOUNTING_RUNBOOK.md`
  - `swimbuddz-backend/docs/design/ACADEMY_PER_STUDENT_SESSION_PARTICIPATION.md`
  - `swimbuddz-backend/docs/academy/MISSED_CLASS_POLICY.md`

---

## ADR-005: Location-First Club Onboarding, Readiness Assessment Gate & Mobility
- **Status:** Settled & Enforced (Sep–Oct 2026, PRs `#254`, `#255`, `#288`, `#327`, `#8`, `#22`, `#62`)
- **Context & Why:**
  - Club sessions require water safety (25m continuous swim + 30s treading water). Allowing swimmers to pay for a Club quarter before readiness verification created refund/transfer overhead when a swimmer actually needed Academy first.
- **Decision & Non-Negotiable Invariants:**
  - **Readiness Gate Before Checkout:** Prospective Club members complete the 5-question safety pre-assessment (`/upgrade/club/readiness`), select their area, pool location (e.g., Yaba, VI, Ago), and quarter (`/upgrade/club/plan`), and submit a `ClubApplication`.
  - They then choose **Video assessment** (25m continuous swim clip + 30s tread clip, with persistent choice and resumable replacement uploads) or **Short in-pool assessment** (`/upgrade/club/assessment/[applicationId]`).
  - **Checkout is locked** until an admin/coach reviews the application in `/admin/club-applications`, marks the swimmer `club_ready` (or `club_ready_modified`), and enables their approved payment mode (`quarterly_prepaid` and/or `transition_per_session`).
  - **Cross-Location Mobility:** Visiting another Club location resolves as `club_visit` at the host session's rate without changing the swimmer's home Club or Pod. Permanent home-Club moves use the audited Admin Location Transfer flow (`ClubEnrollmentTransfer`, `/api/v1/clubs/admin/enrollments/{id}/location-transfer`).
  - **Onboarding Continuity & Multi-Programme Discovery:**
    - Swimmers registering via social channels (e.g., Instagram) often select basic Annual Membership (`community`) during initial account creation.
    - To prevent registration dead ends, the onboarding review summary (`ReviewStep.tsx`) presents explicit secondary discovery cards ("Join Weekly Club — Start Application →" and "Learn in Academy — Browse Cohorts →") so users can immediately continue to Club readiness assessment or Academy intake without hunting through menus.
  - **Standardized Reviewer Notification Layout:**
    - Operational alerts sent to reviewers (`CLUB_REVIEW_NOTIFICATION_EMAILS`, e.g., `contactugodaniels@gmail.com`) when a swimmer submits a readiness form, in-person assessment request, or video clips use the official branded SwimBuddz layout (`wrap_html`, cyan gradient header, applicant detail box, and direct admin action button).
    - As a safety net, `deliver_operational_emails` automatically wraps any operational email missing an explicit `html_body` into the branded layout before dispatching via the email provider.
- **Canonical Docs & Code:**
  - `swimbuddz-backend/docs/CLUB_ONBOARDING_ACCESS_AND_OPERATIONS.md`
  - `swimbuddz-backend/docs/CLUB_MOBILITY_AND_TRANSFERS_RUNBOOK.md`
  - `swimbuddz-backend/docs/CLUB_LOCATION_MOBILITY.md`

---

## ADR-006: Unified Session Participant Admission, Capacity & Guest Waiver Gate, and Consolidated Settlement
- **Status:** Settled & Enforced (Oct 2026, PRs `#283`, `#286`, `#371`, `#376`, `#394`, `#15`, `#20`, `#148`, `#149`)
- **Context & Why:**
  - Fragmented walk-in forms across Academy, Club, Community, and Guests caused capacity overbooking, skipped guest waivers, and prevented members from settling multiple retrospective session/guest balances in one payment. Additionally, 5-digit random payment references caused `UniqueViolation` collisions with historical checkouts.
- **Decision & Non-Negotiable Invariants:**
  - **Unified Participant Entry:** Admin Attendance uses a single participant manager covering Academy, Member, and Guest walk-in routes, prefilling the authoritative server admission quote (`member-admission-preview`) before recording.
  - **Capacity & Guest Waiver Gate:** Direct guest walk-ins count toward session seat capacity alongside member bookings and guest passes (without double-counting). Unregistered walk-in guests are distinguished as recorded visitors pending waiver completion before attendance admission.
  - **Consolidated Retrospective Settlement:** Retrospective unpaid session and guest balances can be settled together in a single checkout (`SESSION_SETTLEMENT_OPERATIONS_RUNBOOK.md`).
  - **Collision-Safe Payment References:** `Payment.generate_reference` uses UUID4 entropy (preserving existing historical references) so new checkouts never collide with historical failed/abandoned payment intents.
- **Canonical Docs & Code:**
  - `swimbuddz-backend/docs/SESSION_SETTLEMENT_OPERATIONS_RUNBOOK.md`
  - `swimbuddz-backend/docs/CLUB_ONBOARDING_GUEST_PASSES_AND_CHARGES.md`

---

## ADR-007: Payment Settlement Invariants, Server-Owned Pricing & Bubbles Wallet Holds
- **Status:** Settled & Enforced (Aug–Oct 2026, PRs `#328`, `#366`, `#63`, `#69`)
- **Context & Why:**
  - Client-calculated totals, unheld mixed Bubbles + Paystack checkouts, and late webhook callbacks on cancelled/started sessions risked undercharging or overbooking.
- **Decision & Non-Negotiable Invariants:**
  - **Server-Owned Pricing (`PAYMENT_SETTLEMENT_INVARIANTS.md`):** The domain service that owns a product owns its unit price; client-supplied totals are stale-price checks only and never determine the amount charged.
  - **Reserve Before Charge:** Capacity-limited sessions reserve seats with a finite TTL before initializing external checkout. Delayed paid callbacks on cancelled or already-started sessions fail closed for operational review/refund.
  - **Bubbles Exchange Rate & Wallet Holds:** `1 Bubble = ₦100` exact (never rounded). Mixed Bubbles + Paystack checkouts place a wallet hold for at most `floor(amount / ₦100)` whole Bubbles before provider initialization and capture only after provider success. Wallet-only direct charges are allowed only when the amount is an exact multiple of ₦100.
  - **Learner Payment Links & Installment Dropout Recovery:** Admins can copy a learner-specific checkout link (`academyPaymentPath`), which requires the learner to sign in and fetches a live server quote (admins never impersonate or charge the learner's card). Enrollments in `dropout_pending` can cure missed installments via checkout without reopening confirmed dropouts.
  - **Open Booking Payment Resumption & Instant Unpaid Switch (`GET /payments/bookings/{booking_id}/open-payment`, `POST .../switch`):** Single-session bookings allow at most one open payable attempt at a time (`existing_booking_attempt`). When a member backs out of an in-flight `PENDING` Paystack popup without completing payment (e.g. mobile Browser Back skipping the callback URL), `/account/billing/sessions/[bookingId]` surfaces the locked attempt's exact options with **Resume online payment** and **Switch payment method or Bubbles**. Clicking Switch verifies live with Paystack under the booking lock: if unpaid (`abandoned`/`failed`), it transitions the old link to `FAILED` immediately and releases any Bubbles hold so the member can switch to Bubbles/transfer without waiting for the 2-minute background worker; if Paystack already succeeded, it marks paid and fulfills rather than risking double-payment. Manual bank transfers (`PENDING`/`PENDING_REVIEW`) and combined `session_bundle` links remain protected against self-service cancellation.
- **Canonical Docs & Code:**
  - `swimbuddz-backend/docs/architecture/PAYMENT_SETTLEMENT_INVARIANTS.md`
  - `swimbuddz-backend/docs/ACADEMY_PAYMENT_RECOVERY_RUNBOOK.md`
  - `swimbuddz-frontend/src/lib/academy/paymentEligibility.ts`

---

## ADR-008: Double-Entry Ledger Service (`ledger_service`) & Financial Obligations Aggregator
- **Status:** Settled & Enforced (May–Oct 2026, PRs `#199`, `#332`, `#69`, `#71`)
- **Context & Why:**
  - Operational payment tables alone are not an audit-grade accounting system, and member billing previously made scattered requests that flashed empty states while loading.
- **Decision & Non-Negotiable Invariants:**
  - **Immutable Double-Entry Ledger (`ledger_service`):** Multi-tenant, FIRS-ready double-entry ledger receiving idempotent event emitters from `payments`, `wallet`, `store`, `academy`, and `sessions`. Ledger entries are immutable; corrections use reversing/adjusting journal entries.
  - **Reviewed store preorder correction (12 Nov 2026):** Correcting an unsettled preorder to a more expensive SKU preserves the original frozen payment and receipt, creates a separate payment for the server-priced difference, and audits the item/supplier snapshots and revised order total. A reconciliation hold prevents either partial receipt from fulfilling the whole order. Only after both payments are independently bank-verified and their sum equals the corrected order total may the primary payment's fulfillment be replayed. The scoped operator repair is not a general exchange/refund API, never rewrites posted journals, and never posts the revised full total as an additional cash receipt.
  - **Reviewed Bubble settlement reallocation (16 Nov 2026):** A bank-free session payment settled entirely with Bubbles may be reversed only after an operator validates the original debit, refund, booking and wallet audit. Preserve the original paid receipt, prices and wallet transaction; add a linked refund and a new debit for the correct swim, reopen the original booking debt, and prohibit replay of its reversed settlement. A private admin review marker plus exact zero-cash/full-Bubbles proof allows a replacement checkout; member-supplied metadata alone never does. Post missing and correcting journals idempotently without inventing cash or changing posted entries. The Amara repair is a scoped maintenance transaction, not a general refund endpoint.
  - **Store service isolation and Finance authorization (19 Nov 2026):** Store procurement approvals remain Finance Owner-only with independent approval. The Store container must start without importing Ledger service code or models. It checks the authenticated caller through Ledger’s existing `/admin/finance/users/me` HTTP endpoint, forwarding that caller’s bearer token; missing authority or an unavailable Finance service denies the protected action without blocking public catalogue startup. Application admin and procurement grants never substitute for Finance Owner authority. Regression checks import Store with Ledger packages unavailable.
  - **Ledger startup isolation (20 Nov 2026):** The isolated Ledger image must start without Academy, Members, Payments or Store packages. Keep Finance Intelligence role-gated and use read-only aggregate queries against its existing shared database projection. Test startup with other service packages unavailable.
  - **Reviewed stocked catalogue and Fatima handover reconciliation (24 Nov 2026):** The owner requested Stocked sourcing for the eight products with thirteen previously purchased colour/size variants and confirmed five units remaining per purchased variant after Fatima collected both items. Record this as an audited, scoped inventory-count correction: establish five units for each purchased variant, temporarily include the two collected units, and reserve those units against Fatima’s verified paid order before its normal Picked Up transition consumes them. Preserve original preorder order-line snapshots, all prices, bank receipts and payment journals; do not reinterpret old completed orders or restock again on retries. Unpurchased variants retain their counts. Acquisition costs remain unknown/unvalued until supported procurement evidence and independent valuation approval; retail prices are never acquisition costs. This reviewed repair replaces the unmerged draft preorder bypass; no global preorder fulfilment bypass is deployed.
  - **Member Financial Obligations Aggregator:** `/account/billing` aggregates obligations across Annual Membership, Club, Academy installments, and unpaid session/guest balances into a single trust-first overview, preserving loading states so active enrollments are never misreported as empty.
- **Canonical Docs & Code:**
  - `docs/design/LEDGER_SERVICE_DESIGN.md`
  - `docs/guides/FINANCE_SYSTEM_GUIDE.md`
  - `swimbuddz-backend/docs/ADMIN_FINANCE_RECONCILIATION_GUIDE.md`

---

## ADR-009: Academy Knowledge Quizzes, Milestone Evidence & Coach/Admin Review Overrides
- **Status:** Settled & Enforced (May–Oct 2026, PRs `#174`, `#367`, `#368`, `#2`, `#138`, `#139`)
- **Context & Why:**
  - Academy progression needed structured theory/water-safety knowledge checks alongside practical pool milestones, coach-uploaded evidence support, mandatory rejection feedback, and a way for admins (or AI) to override a coach's milestone review without erasing the coach's original attribution.
- **Decision & Non-Negotiable Invariants:**
  - **Reusable Quizzes & Frozen Cohort Snapshots (`AcademyQuiz` → `AcademyQuizAssignment` → `AcademyQuizAttempt`):**
    - Quizzes are authored at the curriculum/program level (`AcademyQuiz`) and assigned to specific cohort weeks during cohort creation or editing (`AcademyQuizAssignment`).
    - Assigning a quiz freezes `questions_snapshot`, `title_snapshot`, and `quiz_version` on the assignment so future edits to the master quiz never corrupt historical student attempts (`AcademyQuizAttempt`).
  - **Milestone Evidence & Mandatory Rejection Feedback:**
    - Video/image evidence is tagged with enrollment and milestone context; video is not universally forced on milestones whose `required_evidence` does not require it; coaches can upload coach-recorded evidence directly.
    - Rejecting a milestone claim requires mandatory coach feedback across all review surfaces.
    - **Transactional Milestone Review Notifications:** Whenever a coach or admin approves or rejects a milestone claim, the learner receives an immediate transactional email notification (`milestone_approved` with celebratory copy, score, and link to dashboard; or `milestone_rejected` highlighting coach technique feedback and next practice steps).
    - **Milestone Evidence & Review Video Access Boundaries (`_require_original_access`):** Milestone completion evidence (`milestone_evidence`) and Club assessment videos (`club_assessment_video`) are private assets viewable by the uploader (student/applicant), coaches (who evaluate technique and review milestones), and admins. Unauthenticated users and unrelated members are denied access (404). For browser HTML5 video streaming (`/play`), an authenticated media lookup issues a media-ID-bound, HMAC-signed playback URL expiring within 15 minutes; knowing a media UUID alone must never authorize a presigned S3 redirect. Financial receipts (`payment_proof`) and uncropped originals (`presentation_original`) remain strictly restricted to the uploader and admins.
  - **Admin Override Preserves Original Coach Attribution (`MilestoneReviewEvent`):**
    - `StudentProgress.reviewed_by_coach_id` records the **original** reviewer and is never overwritten by subsequent admin overrides.
    - Admin overrides (`POST /api/v1/academy/admin/progress/override`) append an `OVERRIDE_COACH` event with a required `override_reason` and `override_of_event_id` chain.
    - Every admin list/view/download of student milestone media is logged in `media_audit_logs` per item accessed (`ACADEMY_ADMIN_CONTROLS_DESIGN.md`).
- **Canonical Docs & Code:**
  - `docs/design/ACADEMY_ADMIN_CONTROLS_DESIGN.md`
  - `swimbuddz-backend/services/academy_service/models/quizzes.py`
  - `swimbuddz-backend/services/academy_service/models/progress.py`

---

## ADR-010: Modular Academy Curriculum, Versioned Catalog & Evidence-Backed Competency Passport (V2)
- **Status:** Settled & Enforced — V2 Implementation Contract Accepted (Nov 2026; PRs `#424`, `#442`, `#445`, `#446`, `#217`, `#220`, `#222`, hardening/ops follow-up). Supersedes the V1 schema/auto-backfill clauses of `ACADEMY_INDEPENDENT_CURRICULUM_AND_DRILLS_DESIGN.md` while preserving its pedagogical pathway taxonomy and TI coaching goals.
- **Context & Why:**
  - Selling only a monolithic 12-week Beginner-to-50m course forced swimmers with partial skills to repeat basics and limited entry points to three intakes a year. Conversely, an unguided à-la-carte lesson menu causes choice overload and lets learners skip water safety.
  - The earlier V1 draft (`ACADEMY_INDEPENDENT_CURRICULUM_AND_DRILLS_DESIGN.md`) proposed mutating legacy `milestones.program_id` to nullable, attaching mutable milestone rows via `program_milestones`/`cohort_milestones`, and auto-backfilling a mutable `learner_milestone_credits` table from historical `student_progress` rows. Review showed two fatal risks in V1: (1) editing a rubric later would silently alter historical cohort/graduate standards, and (2) auto-backfilling legacy `student_progress` rows of uneven rigor or missing video evidence would inflate trust in portable safety credentials.
- **Decision & Non-Negotiable Invariants:**
  - **Sell Outcomes/Pathways, Deliver Competency-Gated Modules:** Customers buy clear outcome pathways (e.g., *Beginner-to-50m Freestyle*, *Water-Safe Starter*, *Breaststroke Pathway*) composed of stackable Foundation (`F1–F4`), Stroke (`S1–S4`), and Deep-Water/Distance (`A1–A2`) modules. Commercial terms, attendance, and class balances remain strictly separate from competency mastery (`ADR-002`, `ADR-004`).
  - **Assessment Grants Prerequisite Credit (Evidence-Backed Only):** Diagnostic clinics and prior assessments can grant recognized prerequisite credit so learners enter at the right module boundary without repeating verified basics—**never** as automatic trust inflation from unreviewed legacy records, class attendance, or theory quizzes alone.
  - **Group Intake Realities vs. Private Coaching Demand Indicators:**
    - All standard Academy seasonal intakes operate as small-group cohort classes (4–6 students per coach).
    - The onboarding readiness questionnaire (`AcademyDetailsStep.tsx`) collects format preferences (group vs. private 1-on-1). To avoid misleading new registrants into expecting private 1-on-1 classes at standard group tuition, the form clearly disclaims that all cohort intakes are group-based, and that 1-on-1 indicators are recorded for personalized recommendations and future private coaching demand.
  - **Versioned Independent Curriculum Catalog (Additive Sidecar — `ACADEMY_COMPETENCY_ARCHITECTURE_V2.md`):**
    - Five independent catalog entities separate stable identity (`code`, `title`, `is_retired`) from immutable published versions (`state IN ('draft','published')`, `published_at`): `AcademyMilestoneDefinition`/`Version`, `AcademyDrill`/`Version` (with one `primary_focal_cue` aligned to `ADR-013`), `AcademyLessonBlueprint`/`Version`, `AcademyKnowledgeQuiz`/`Version`, and `AcademyModule`/`Version`, linked via typed join tables (`academy_lesson_drills`, `academy_module_lessons`, `academy_module_milestones`, `academy_module_quizzes`, `academy_milestone_skills`, `academy_drill_skills`).
    - Draft versions may be edited in place and composed (`PATCH` / draft-only join edits); once `published`, versions are immutable. New changes require publishing version `N+1`.
    - **Publish-Time Milestone Contract:** Publishing an `AcademyMilestoneVersion` fails closed unless `rubric_json.criteria` is a non-empty list of unique non-empty criterion codes/strings, any `assessment_context_json.validity_days` is a positive integer, and every `safety_gate = true` milestone defines a positive `validity_days` recheck interval.
  - **Deterministic Cohort Requirements & Immutable Frozen Snapshots:**
    - Programs assemble published modules (`AcademyProgramModule`) and optional direct milestones (`AcademyProgramMilestoneAssignment`); cohorts may add audited overrides (`AcademyCohortMilestoneAssignment` with mandatory `override_reason`) before freezing, and may never remove or downgrade a `safety_gate` milestone.
    - `build_cohort_requirements` resolves modules + program assignments + cohort additions deterministically (failing closed on unpublished or conflicting versions of the same canonical milestone). Freezing persists an append-only `AcademyCohortRequirementsSnapshot` protected by a SHA-256 checksum and a Postgres `BEFORE UPDATE OR DELETE` trigger (`guard_academy_requirements_snapshot`). Pre-freeze edits/removals are allowed; post-freeze snapshots are immutable.
  - **Append-Only Evidence-Backed Competency Verification Ledger (`academy_competency_verifications` + `academy_competency_revocations`):**
    - Both ledger tables are protected by Postgres `BEFORE UPDATE OR DELETE` triggers (`guard_academy_competency_ledger`). Verifications are never edited in place; invalidations append an `AcademyCompetencyRevocation` with a substantive audit reason.
    - **No Automatic Legacy Backfill:** A historical `StudentProgress` achievement becomes a portable competency credit **only** after a human administrator (never `service_role` or AI) confirms equivalence against a frozen cohort V2 milestone standard, attests every published criterion in `criteria_passed`, provides a substantive `equivalence_reason`, and backend verification confirms the private video evidence exists (`HeadObject` via `media_service` `/internal/media/alumni-evidence/{id}`), belongs to the original claimant/coach uploader, and has not changed since approval.
    - One source review event (`source_review_event_id`) can issue at most one credential. Validity/expiry anchors to the **original assessment date** (`review.created_at`), never the later administrative import date.
    - Read projections (`/competencies/me`, `/competencies/admin/members/{member_auth_id}`) dynamically check revocations, expiry, and whether the underlying source `StudentProgress` / latest review decision remains `ACHIEVED` with unchanged evidence — failing closed to `recheck_required` if overridden or missing, and surfacing `revoked` when all supporting verifications have been withdrawn.
  - **Controlled Rollout & Legacy Isolation:** Legacy graduation, certificates, Club readiness (`ADR-005`), attendance (`ADR-004`), and commercial terms remain isolated until dual-read parity, native coach V2 assessments, and remedial/personalized drill prescriptions are explicitly promoted in later phases.
- **Canonical Docs & Code:**
  - `docs/design/ACADEMY_COMPETENCY_ARCHITECTURE_V2.md`
  - `swimbuddz-backend/docs/design/ACADEMY_COMPETENCY_ARCHITECTURE_V2.md`
  - `docs/design/ACADEMY_INDEPENDENT_CURRICULUM_AND_DRILLS_DESIGN.md` *(Superseded V1 reference — pedagogical taxonomy & seed catalogue)*
  - `docs/academy/MODULAR_CURRICULUM_PATHWAY.md`
  - `swimbuddz-backend/services/academy_service/models/curriculum_library.py`
  - `swimbuddz-backend/services/academy_service/models/requirements.py`
  - `swimbuddz-backend/services/academy_service/models/competency.py`
  - `swimbuddz-backend/services/academy_service/routers/curriculum_library.py`
  - `swimbuddz-backend/services/academy_service/routers/requirements.py`
  - `swimbuddz-backend/services/academy_service/routers/competencies.py`
  - `swimbuddz-backend/services/academy_service/services/requirements_resolver.py`
  - `swimbuddz-frontend/src/app/(admin)/admin/academy/curriculum-library/page.tsx`
  - `swimbuddz-frontend/src/app/(admin)/admin/academy/requirements/page.tsx`
  - `swimbuddz-frontend/src/app/(admin)/admin/academy/competencies/page.tsx`
  - `swimbuddz-frontend/src/app/(member)/account/academy/passport/page.tsx`

---

## ADR-011: Calendar Activity Projection, Event-Backed Sessions & Consolidated "My Swims" IA
- **Status:** Settled & Enforced (Oct 2026, PRs `#279`, `#281`, `#282`, `#101`, `#104`, `#106`, `#108`, `#110`, `#169`, `#172`)
- **Context & Why:**
  - Splitting calendar discovery, booked sessions, and attendance history across separate sidebar routes confused members, and hiding published upcoming sessions behind a mandatory "Recommended" filter made members think no swims were scheduled. Meanwhile, Community Swims needed both Event discovery and Session operations (capacity, payment, ride-share, volunteers, attendance).
- **Decision & Non-Negotiable Invariants:**
  - **Calendar Projection (`CALENDAR_ACTIVITY_PROJECTION.md`):** `gateway_service` projects activities from `sessions_service` and `events_service` keeping `source`, `activity_type`, `primary_audience`, `audiences`, `visibility`, and `tier_access` separate.
  - **Event-Backed Sessions (`community_swim`):** `events_service` owns shared title, description, start/end, venue, capacity, and visibility (synchronizing edits to linked Sessions); `sessions_service` owns pricing, guest policy, booking, payment, attendance, volunteers, and ride-share. Event RSVP fails closed whenever a Session is linked.
  - **My Swims Single Workspace (`/account/swims`):** Provides four clear tabs (**Upcoming**, **Booked**, **History**, **All**) with published upcoming sessions visible by default and **Recommended for you** as an opt-in filter.
- **Canonical Docs & Code:**
  - `swimbuddz-backend/docs/architecture/CALENDAR_ACTIVITY_PROJECTION.md`
  - `swimbuddz-frontend/UI_FLOWS.md`

---

## ADR-012: Session Media Vault, Immutable S3 Originals, Attendee Collections & Member Clip Editor
- **Status:** Settled & Enforced (Oct 2026, PRs `#336`, `#339`, `#340`, `#342`, `#347`, `#362`, `#96`, `#134`)
- **Context & Why:**
  - Buffering large iPhone MOV/HEIC files through FastAPI exhausted server memory/disk, and exposing raw uncurated session footage publicly violated member consent.
- **Decision & Non-Negotiable Invariants:**
  - **Direct-to-S3 Multipart Originals (`MEDIA_VAULT.md`):** Uploads go directly to the private S3 bucket via signed multipart URLs (capped at 2 GiB/video and 20 GiB/vault for member self-uploads) and are verified via `HeadObject`. Original files are immutable and never overwritten.
  - **Automatic Review Proxies & Hover Previews:** `media-worker` generates browser-ready H.264/AAC review proxies and muted hover quick-previews under `vault-derivatives/`. Publication to the public gallery copies only approved + consent-cleared items.
  - **Attendee Media Collections & Clip Editor:** Verified session attendees (including default-Present Academy learners) can view their session's media collection and create personal video clips with isolated per-recording timeline/job state.
  - **Student Self-Upload Visibility in Session Vaults:**
    - In session vaults (`MediaVault.session_id` populated, `owner_member_id` is null), uploaded items are indexed with `MediaItem.uploaded_by == actor.auth_id`.
    - Both personal upload lists (`list_my_swim_uploads` at `/vaults/my-swim-uploads`) and session video collections (`my_session_videos` at `/vaults/sessions/{id}/my-videos`) explicitly include `MediaItem.uploaded_by == actor.auth_id` in their query filters. Student contributors can always view the footage they submitted even when default session visibility is set to `selected_members` prior to manual tagging.
  - **Universal Clip Editor Mounting & Admin Rate-Limit Exemption:**
    - The video trimmer (`MemberClipTool.tsx`) is mounted directly within `VaultVideoDialog.tsx` so admins and members can review and cut clips from any vault dialog (Admin Media Vault, Session Vaults, and Library) without navigating away.
    - Admins (`actor.is_admin`) are exempt from the `item.allow_member_clips` gate, attendance verification requirements, and the 5-clip daily rate limit when curating or creating highlight clips.
  - **Mobile Background Upload UX & Queue Minimization:**
    - When uploading large multi-file batches on mobile devices, users can minimize the upload panel into a floating bottom progress pill displaying overall progress percentage, byte count, and completion tally.
    - Screen wake-lock is automatically requested and re-acquired on browser `visibilitychange` (tab reactivation) so long-running uploads proceed reliably without screen sleep interruptions.
  - **Bandwidth Ledger (`media_transfer_logs`):** Reconciles presigned download authorizations against S3 server access logs.
  - **Bounded reconciliation and database traffic (16 Nov 2026):** Preserve immutable S3 delivery evidence and bytes, classify preview/derivative requests as unattributed, and retry originals only in bounded due batches with exponential backoff and terminal expired/exhausted states. Effective S3 bandwidth includes derivative delivery separately from matched originals and pending original estimates; it is not Supabase egress. Member safety checks fetch only account activity, swimming background and emergency-contact completeness, without changing clearance rules or the Attendance worker cadence. Reporting reads suppress unrelated ORM relationships while retaining the hourly refresh and domain-owned definitions. Hidden admin tabs suspend queue polling and refresh on return. Connection reuse remains an opt-in media-worker-only pilot capped at two connections with no overflow; evaluate query/auth-event deltas and actual daily egress/log ingestion before wider adoption. The 100 MB/day egress and 20 MB/day log targets are validation targets, not established results.
  - **Measured follow-up and polling failures (17 Nov 2026):** Connection churn must be attributed by container before expanding the scoped pool pilot. Preserve row locks and membership/financial semantics while loading only membership for membership mutations, then joining the complete response once after commit. Announcement audience filters accept only the existing community/club/academy enum values: prospect or unknown membership labels must not cause repeated unread-count failures or grant additional access. Keep the existing public-community and programme announcement visibility rules unchanged. Passive observation stores only aggregate header/byte counts; diagnostics must not retain credentials, packet payloads or member data.
  - **Approved Members/Chat canary scope (17 Nov 2026):** The owner explicitly superseded the media-worker-only configuration limit after a passive six-minute sample attributed about 86% of received pooler TCP payload to Members, Chat and Communications, and a bounded read-only probe showed connection reuse reduced receive traffic for identical reads. Prepare independent default-off Members API and Chat API switches, each hard-capped at one retained connection per process with zero overflow; tag their application names and retain disabled prepared statements/pre-ping. This authorizes the reviewable canary PR, not enabling or deploying it automatically. Existing media-worker settings and every other service stay unchanged. Measure equivalent windows and provider egress before any wider rollout.
  - **Members-only production activation (19 Nov 2026):** The owner explicitly approved activating the prepared Members API canary, persisting its production deployment flag and recreating only that API. Keep the existing application image, one retained connection per process and zero overflow; leave Chat, media and every other service unchanged. Record a rollback point and compare actual connections, response errors/latency and scheduled workloads. Initial socket/request checks do not prove a full-day egress reduction; require a complete provider-usage day before evaluating the next role. No schema, financial-record or billing-plan change is authorized by this activation.
  - **Chat-only production activation (22 Nov 2026):** After the Members canary remained healthy across complete provider-usage days, the owner explicitly approved enabling the existing Chat API reuse switch, persisting its production deployment flag and recreating only Chat with its current application image. Keep one retained connection per process with zero overflow; Members stays unchanged and media reuse stays off. Preserve chat authorization, role reconciliation and moderation configuration. Save rollback metadata, compare matched traffic/request windows and check errors before retaining the test. Daily pooler egress remained above the remaining free-tier budget before this step; short socket/traffic checks cannot establish sustainable provider-billed savings. No schema, financial-record or billing-plan changes are part of this activation.
  - **Background worker & reporting egress controls (23 Nov 2026):** After Members and Chat pooling alone still left Shared Pooler egress at 245–254 MB/day against ~124 MB/day remaining budget, 24-hour container logs, 3.5-day container network totals, passive TCP captures, and `pg_stat_statements` attributed ~75–85% of pooler traffic to two October background changes (the Oct 18 hourly 44-member cross-service quarterly reporting rebuild and the Oct 20 every-5-minute unbounded Academy attendance history rescan) plus unpooled idle polling workers. With owner approval, this supersedes the 16 Nov cadence freeze while preserving all ADR-004, ADR-012, and ADR-019 invariants:
    - *Reporting:* Refresh current-quarter member and community reports twice daily (`02:10` and `19:10 UTC`, `run_at_startup=False`) instead of hourly; cache cohort sessions across students within a batch; skip the duplicate `/internal/wallet/member-summary` call when `/internal/wallet/reporting-summary` already supplies wallet totals; and skip/scope `Club` and `Pod` table queries on per-member `/internal/members/reporting-summary` requests.
    - *Attendance:* Split `reconcile_academy_defaults` into a bounded 48-hour lookback every 15 minutes (`task_reconcile_recent_academy_defaults`, fetching cohort sessions first and skipping cohorts with no eligible started included class before student/clearance/DB lookups) plus a full active-cohort historical safety repair once daily (`02:15 UTC`) and at startup. Schedule ADR-019 `task_sync_swim_rewards` every 10 minutes + startup instead of every 1 minute.
    - *Idle & reconciliation workers:* Deduplicate `reconcile_pending_membership_payment_state` via `payment_metadata["pending_tier_projection_set_at"]` so unchanged pending payments do not `PATCH` Members every run; skip volunteer grant refreshes on closed/review media vaults and run `task_sync_session_vaults` every 4 hours (`task_reconcile_vault_bandwidth` and `task_backfill_vault_video_previews` hourly); relax empty-queue/fallback sweeps (`operational_email_outbox` 5m, pending notifications/payments/session retries/event reminders 10–30m); and run cross-service chat/projection reconciliation safety nets every 6 hours + startup instead of hourly. No schema migration, financial-record mutation, or wider DB pool rollout is included.
- **Canonical Docs & Code:**
  - `swimbuddz-backend/docs/design/MEDIA_VAULT.md`
  - `swimbuddz-backend/docs/operations/MEDIA_VAULT_BANDWIDTH_RECONCILIATION.md`

---

## ADR-013: Stroke Lab AI Video Analyzer & Total Immersion (TI) Coaching Doctrine
- **Status:** Settled & Enforced (Jul–Nov 2026, PRs `#208`, `#210`, `#232`, `#439`, v2 reliability upgrade)
- **Context & Why:**
  - Generic LLM swim advice often tells adult learners to "kick harder" or "pull harder" (increasing drag and exhaustion) or hallucinates underwater catch/crossover faults that are impossible to see from an above-water side camera.
  - Furthermore, default 1 FPS static video processing misses ~0.8–1.0s arm recoveries, Gemini 2.5/3.x thinking tokens truncate JSON when `max_tokens` is tight, newer late-2026 model IDs (e.g. `gemini-3.8-flash`, `gemini-3.5-flash-lite`) can silently return `$0.00` from LiteLLM pricing tables (blinding cost circuit breakers), and `ultralytics` `yolov8-pose` carries AGPL commercial licensing obligations.
- **Decision & Non-Negotiable Invariants:**
  - **TI-Informed Priority Order (`STROKELAB_TI_COACHING_KNOWLEDGE_BANK.md`):** Stroke Lab coaches efficiency-first in strict order: **1. Balance & Comfort → 2. Drag Reduction & Streamline (Shape the Vessel) → 3. Whole-Body Rotation & Propulsion**, prescribing **one focal point / drill at a time**.
  - **Strict Camera-Angle Visibility Rules ("Honesty Over Helpfulness"):** Never diagnose underwater catch/pull from above-water clips, and never claim midline crossover from side-on footage. Return `unclear` / `unavailable` when an aspect is not visible.
  - **Controlled Staged Pipeline Over Autonomous Agent Swarms (25 Nov 2026):** Keep fixed, deterministic ARQ-orchestrated stages (`Gate → Segment/Pose → Chunk/Aspect Coach → Verify → Grade → Aggregator → Compare`) with bounded per-attempt call/spend circuit breakers rather than free-roaming multi-agent debates.
  - **Explicit Video FPS, Hybrid Key Stills & Thinking-Token Headroom:** Pass explicit `video_metadata.fps` (default `4.0` FPS on ~4s stroke chunks, configurable via `STROKELAB_COACH_VIDEO_FPS`) and optional high-resolution key frames alongside chunk video (`STROKELAB_COACH_CHUNK_HYBRID_STILLS`) for Gemini 3+ video calls; enforce `reasoning_effort="low"` and `max_tokens >= 4096` headroom for thinking-model JSON responses, plus resilient JSON object extraction.
  - **Fail-Safe Cost Accounting for New Models:** Maintain a conservative fallback token-rate table (anchored on standard post-promo Jan 2027 rates, e.g., `gemini-3.8-flash` `$1.50/$7.50` per 1M tokens) in `providers/base.py` so per-attempt and daily AI cost circuit breakers never report `$0.00` on models absent from LiteLLM's bundled pricing JSON.
  - **Graded 3-Tier Gate, Runtime Verification & Safe TI Drill Grounding:** The gate emits graded `profile_quality` (`clean | partial | poor`) plus explicit `catastrophic_flags` while preserving 3-tier `clean / borderline / refuse` UX. Deterministic post-VLM verification (`pipeline/verify.py`) enforces closed-enum verdicts, runtime ADR-013 honesty/visibility bans (including overhead body-line sink suppression and side-on crossover rejection), cross-stroke consensus across sampled chunks, and optional selective second-model adjudication (`STROKELAB_COACH_ADJUDICATOR_MODEL`) only for uncertain top findings. Drills map deterministically to the curated beginner-safe TI drill catalog (`analysis/drills.py`, aligned with `ADR-010`) rather than unvetted free-text LLM advice.
  - **Permissive Pose Engine & Zero-Cost Longitudinal Progress:** Support an Apache-2.0 `rtmlib` / `onnxruntime` (`RTMPose`) pose recovery backend (`STROKELAB_POSE_ENGINE`) alongside `yolov8` to remove AGPL exposure, and attach deterministic zero-VLM-cost longitudinal progression diffs (`pipeline/compare.py`) comparing verified aspect verdicts against a swimmer's previous completed freestyle analysis.
  - **Coach-Labeled Multi-Config Bake-Off Before Model Swaps:** Benchmark candidate models and input modes (starting with 25–30 stratified coach-labeled clips, scaling to 100+) across repeated runs via `validation/bakeoff_eval.py`, tracking coach inter-rater agreement, repeatability, refusal accuracy, honesty violations, and Jan-2027 unit cost before changing production defaults.
  - **Public & Member Analyzer:** Public guest analyzer enforces compute isolation, verified email ownership, credit checks, cost circuit breakers, and worker ready-emails (`STROKELAB_PUBLIC_ANALYZER_DESIGN.md`).
- **Canonical Docs & Code:**
  - `docs/design/STROKELAB_TI_COACHING_KNOWLEDGE_BANK.md`
  - `docs/design/STROKELAB_VLM_COACH_DESIGN.md`
  - `docs/design/STROKELAB_PUBLIC_ANALYZER_DESIGN.md`
  - `swimbuddz-backend/docs/design/STROKELAB_MVP_ENGINE_EVALUATION_20261121.md`
  - `swimbuddz-backend/services/ai_service/pipeline/verify.py`
  - `swimbuddz-backend/services/ai_service/pipeline/compare.py`
  - `swimbuddz-backend/services/ai_service/validation/bakeoff_eval.py`

---

## ADR-014: Public Pool Directory, Pool-Specific Assessments & Weather Integration
- **Status:** Settled & Enforced (Aug–Oct 2026, PRs `#202`, `#348`)
- **Context & Why:**
  - Swimmers in Lagos need a trustworthy directory of pools with verified depth, hygiene, and safety ratings, while distinguishing community-contributed pool submissions/corrections from official SwimBuddz inspections.
- **Decision & Non-Negotiable Invariants:**
  - `pools_service` manages the Public Pool Directory (`/pools`), separating community pool submissions vs. existing-pool correction moderation from official **SwimBuddz Pool Assessments**, requiring approved public cover photos, and integrating pool weather forecasts.
- **Canonical Docs & Code:**
  - `docs/design/WEATHER_SERVICE_DESIGN.md`
  - `swimbuddz-backend/services/pools_service/`

---

## ADR-015: Member Identity Audit (`MemberNameChange`), Self-Service Guardrails & Soft/Hard Delete
- **Status:** Settled & Enforced (Oct–Nov 2026, PRs `#389`, `#392`)
- **Context & Why:**
  - Self-service name edits must be auditable to prevent identity swapping on paid enrollments, and Admin member deletion must support both reversible account deactivation (Soft Delete) and full GDPR/test-account purge (Hard Delete) without silent failures or orphaned auth records.
- **Decision & Non-Negotiable Invariants:**
  - **Self-Service Name Changes:** Recorded immutably in `member_name_changes` (`MemberNameChange`) with actor and before/after values.
  - **Soft Delete (`mode = "soft"`):** Sets `Member.is_active = False`, hides the member from active tabs on `/admin/members`, displays them under the **Deactivated** tab with a `Deactivated` badge, and allows one-click **Reactivate Account**.
  - **Hard Delete (`mode = "hard"`):** Purges member records across all services (`payments`, `attendance`, `academy`, `communications`, `events`, `transport`, `media`, `members`) respecting FK constraints (`RESTRICT`/`NO ACTION` child rows cleaned up first; Pod Leads blocked with `409 Conflict` until reassigned), commits the database transaction first, and **only then** deletes the Supabase Auth user. Before cross-service destructive operations the gateway must preflight Pod Lead references and reject deletion of payments referenced by shared Academy bank receipts allocated to other learners. Stop at the first service failure; this is still not a distributed transaction, so operators must review partial failures before retrying. Any downstream failure is surfaced to the admin UI—never masked with a false success toast.
  - **Append-Only Competency Ledger Retention on Hard Delete (Nov 2026):** `academy_competency_verifications` and `academy_competency_revocations` (`ADR-010`) store untied source UUIDs (no FK to `enrollments` or `student_progress`) and enforce `BEFORE UPDATE OR DELETE` immutability triggers (`guard_academy_competency_ledger`). Standard admin hard delete removes operational enrollments and `StudentProgress` cleanly without FK failure while intentionally retaining append-only competency audit records (whose read projection automatically degrades to `recheck_required` once source `StudentProgress` is purged). Formal statutory/GDPR erasure of immutable ledger rows requires an explicit out-of-band compliance procedure and must never silently bypass the runtime immutability trigger.
- **Canonical Docs & Code:**
  - `swimbuddz-backend/services/gateway_service/app/routers/cleanup.py`
  - `swimbuddz-backend/services/members_service/routers/members/admin.py`
  - `swimbuddz-frontend/src/app/(admin)/admin/members/page.tsx`

---

## ADR-016: Git Branching Flow (`feature/* → develop → main`), Migration Discipline & Clean Worktrees
- **Status:** Settled & Enforced (Ongoing, PRs `#321`, `#363`)
- **Context & Why:**
  - Committing directly to `main` or `develop`, hand-editing Alembic migrations, or leaving dozens of merged git worktrees with `.next` build caches caused branch drift, multi-head Alembic failures in production, and disk exhaustion.
- **Decision & Non-Negotiable Invariants:**
  - **Branching Flow:** All changes MUST be developed on a feature/fix branch (`feature/*`, `fix/*`, `codex/*`), merged via PR to `develop`, and promoted via PR from `develop` to `main`. Keep `main` and `develop` synced between local and `origin`.
  - **Migrations:** Always generate migrations via `./scripts/db/migrate.sh <service>_service "description"`, register new models in `alembic/env.py` (`SERVICE_TABLES`), and ensure a single Alembic head per service.
  - **Verified recovery before production migrations (16 Nov 2026):** Deployment must first capture a private logical backup of application `public` and Supabase `auth` schemas, restore that archive into an isolated PostgreSQL instance, and compare all table counts from the same exported source snapshot. A failed dump, restore or comparison stops deployment before migrations or service replacement. Retain checksums, migration versions, the prior running-image manifest and private runtime configuration outside the checkout. Keep multiple recovery points; never publish dumps or secrets as CI artifacts. This is a migration recovery backup, not a complete Supabase project/S3 disaster-recovery copy. Restore is an explicit maintenance operation after pausing writers and reconciling post-backup transactions; never automatically restore a live database on deployment failure.
  - **Production Docker disk recovery (20 Nov 2026):** A release can fill the host while unpacking images because Docker keeps the running stack and tagged rollback layers. Check free blocks and inodes on Docker's storage filesystem, clear unused build cache, and review retention before retrying through the normal release job. Keep at least five verified recovery points and incident pins. Retire older local image tags only after operator review. The verified database backup gate remains required.
  - **Separated DB Backup vs. Local Docker Image Retention & Shared Base Layer (23 Nov 2026 — supersedes the 20 Nov 2026 local-image retention rule):** Verified database dumps + release manifests (~6 MB/release) and unpacked local Docker rollback images (~10.8 GB/release before layer sharing) have vastly different storage costs and must be managed separately on the 96 GB production host.
    - **Database backups & manifests:** Keep at least 5+ verified logical backups (`database.dump`, `verified.json`, `.env.prod`, `compose-shell.env`, `release.json`) on disk; automated image pruning must **never** delete database backups, verification records, or release directories.
    - **Local Docker rollback image sets (`prune_rollback_images.py`):** Retain all **currently running container images**, **active single-service canary/incident pins** (`operational-backups/canaries/*/image-pin.json` and any release directory containing a `.keep-images` marker), **required infrastructure images** (`postgres:17-alpine`, `redis:7-alpine`, `traefik:v3`), the **in-flight deployment's newly captured recovery point**, and the **latest 2 distinct verified rollback image sets** (`ROLLBACK_IMAGE_RETENTION_COUNT=2`, configurable). Automatically retire older `swimbuddz-rollback:*` tags, superseded unverified/failed attempt tags, and orphaned non-running `ugodaniels/swimbuddz-*:latest` leftovers (recording a `retired-images.json` receipt in each retired verified point directory) before pulling and after successful deployment.
    - **Pre-pull disk headroom gate:** After pre-pull pruning and before `compose pull`, require at least `MIN_DEPLOY_FREE_GIB` (default `15` GiB) free on Docker's storage mount and fail fast with diagnostics rather than filling the root disk mid-pull.
    - **Shared Python dependency base layer (`Dockerfile.python-base`):** Build one content-addressed multi-arch base image (`swimbuddz-python-base:<deps_hash>` from `Dockerfile.python-base`, `pyproject.toml`, and `constraints.txt`) once per dependency change and base all 18 deployed service images on it, while keeping a local multi-stage fallback (`ARG BASE_IMAGE=deps-base`) so local `docker compose build` works standalone. Sharing the ~540 MB Python dependency layer across all services and releases reduces per-release disk growth from ~10.8 GB to ~0.1–1 GB.
  - **Worktree & Cache Hygiene:** Prune merged worktrees and temporary `.next` / Postgres test clusters immediately after merging so local disk space stays clean.

---

## ADR-017: Swimwear Size Chart Acknowledgment & Store Purchase Experience
- **Status:** Settled & Enforced (Nov 2026)
- **Context & Why:**
  - Members purchasing swimwear (such as *Full Coverage 2-Piece Swim Set*) were getting blocked at checkout with `400 "Size chart acknowledgment required for swimwear products"` because the size chart acknowledgment was only checked locally on the product page and was never sent in `CheckoutStartRequest` or surfaced on the `/store/checkout` page.
  - Additionally, purchasing single gear items required cumbersome multi-step navigation (Add to Cart → find cart link → Cart page → Checkout), while multi-item purchasers lacked clear feedback on cart contents and post-add next steps.
- **Decision & Non-Negotiable Invariants:**
  - **Cart Enrichment & Size Chart Invariants:** Backend `CartResponse` and `CartItemResponse` must explicitly report `requires_size_chart_ack` and `size_chart_url`. When any item in the cart requires size chart acknowledgment, the `/store/checkout` page renders a prominent amber acknowledgment checkbox above the payment action, and passes `size_chart_acknowledged: true` in `CheckoutStartRequest`. If not acknowledged, checkout is cleanly prevented client-side before calling the API.
  - **Single-Item "Buy Now" & Multi-Item Actions:** **Buy Now** starts a one-SKU checkout for the selected variant and quantity without adding to, converting, or clearing the member's saved cart; the active cart, its coupon, and other saved items remain available for a later purchase. **Add to Cart → Cart → Checkout** accumulates multiple SKUs and purchases them together in one order/payment attempt. The server accepts a validated variant UUID and quantity (1–50) using backwards-compatible `/store/checkout/start?buy_now_variant_id=...&buy_now_quantity=...`, snapshots current server-owned price and actual Store tier discount, checks stock and swimwear size acknowledgment, and converts only an isolated Buy Now cart. Saved cart coupons do not automatically apply to an unrelated Buy Now checkout. The frontend must distinguish these actions and preserve Buy Now selections through login redirects.
  - **Store Navigation & Return Paths:** In `StoreHeader`, the top-left return arrow (`ArrowLeft`) is strictly context-aware: when inside the store (`/store/product/*`, `/store/cart`, `/store/checkout`), it links back to `/store` ("← Store") so shoppers browsing multiple items never get dumped on the main website homepage (`/`). The main site link ("← Home") is only shown on the root store catalog (`/store`). Furthermore, the product detail page provides an explicit "Back to Store" link; successful Add to Cart shows a temporary confirmation with a "View Cart" action rather than a permanent multi-action banner. The header cart remains available, while header login preserves the current store path in `?redirect=`.
  - **Post-Auth Store Checkout & Deep-Link Return Invariant:** Explicit safe return paths passed via `/login?redirect=...` or `/confirm?next=...` — especially standalone public/storefront routes (`isStandaloneReturnPath`: `/store/*`, `/pool-access/*`, `/experiences/*`, `/guest-pass/*`, `/tips/*`) — must return the user directly to their destination after authentication and pending-registration completion. Pending Academy upgrade requests (`requested_tiers: ["academy"]` with inactive/expired Academy), unfinished swim-profile onboarding, admin login routing (`/admin/dashboard`), and active coach routing (`/coach/dashboard`) only apply default fallbacks when no explicit safe destination is present (or for non-standalone member routes requiring onboarding), and must never hijack a shopper clicking **Login to Checkout** (`/store/checkout`).
- **Canonical Docs & Code:**
  - `swimbuddz-backend/services/store_service/routers/orders.py`
  - `swimbuddz-backend/services/store_service/routers/cart.py`
  - `swimbuddz-backend/services/store_service/schemas/main/cart.py`
  - `swimbuddz-frontend/src/app/(public)/store/checkout/page.tsx`
  - `swimbuddz-frontend/src/app/(public)/store/product/[slug]/page.tsx`
  - `swimbuddz-frontend/src/app/(public)/store/cart/page.tsx`
  - `swimbuddz-frontend/src/app/(auth)/login/page.tsx`
  - `swimbuddz-frontend/src/lib/registration.ts`
  - `swimbuddz-frontend/src/lib/returnPath.ts`

---

## ADR-019: One Bubble per confirmed swim day

- **Status:** Settled & Enforced (19 November 2026; automatic reward-trigger revision approved 22 November 2026). Earn one spendable Bubble per eligible Club, Academy, or organised community swim day, up to three different days in each Lagos Monday–Sunday week. This replaces earlier four/eight-session and streak offers. Existing rewards from other sources remain separate.
- **Automatic attendance and rewards (supersedes manual verification):** Confirmed bookings and regular included Academy classes default to Present; coaches/admins record exceptions. After the session ends, Attendance reconciles qualifying Present/Late records into Wallet automatically. There is **no extra confirm-swimmers action**. Do not treat pending bookings, guest participation without qualifying recorded attendance, or a student's self-report as independent proof of a reward. No automatic financial backfill for sessions before 22 November 2026; manually verified historical awards remain preserved.
- Count every qualifying physical session toward achievements, including repeat same-day swims and swims over the weekly cap. Wallet credits promotional Bubbles at ₦100 each. If a member is subsequently marked Absent/Excused/Cancelled or the session is cancelled, immediately reverse **unspent swim promotional Bubbles** with an auditable compensating transaction. Never take purchased Bubbles, spend through an active checkout hold, or make wallet balances negative. Already spent rewards create an offset against future swim rewards. All recalculations and deliveries must be idempotent and replay-safe.
- The member dashboard shows this week’s progress and links to the full My Swims view. My Swims shows confirmed activity, swim-specific Bubbles, wallet value, streaks and achievements. Both surfaces use the same Wallet summary. This makes a repeat visit understandable even for prepaid learners whose classes do not involve a checkout.
- **Canonical Docs & Code:**
  - `swimbuddz-backend/services/attendance_service/services/swim_rewards.py`
  - `swimbuddz-backend/services/wallet_service/services/swim_rewards.py`
  - `swimbuddz-backend/services/attendance_service/alembic/versions/73b8ec042d91_auto_queue_swim_attendance.py`
  - `swimbuddz-frontend/src/components/swims/SwimRewards.tsx`
  - `swimbuddz-frontend/src/lib/my-swims.ts`

---

## ADR-020: Member Dashboard Redesign, Simplified Navigation & Unified Rewards Hub
- **Status:** Settled & Enforced (23 November 2026)
- **Context & Why:**
  - Feature accretion stacked 10+ full-width banners on `/account`, duplicated the member's greeting across the top bar and `GreetingHero`, buried Quick Actions below passive analytics, and exposed five overlapping top-level swim links (`Book My Next Swim`, `Find a Swim`, `My Swims`, `SwimBuddz Calendar`, `Explore Pools`).
  - Meanwhile, the full `SwimRewards` panel on `/account/bookings` pushed actual upcoming swims off-screen, derived swim counts and `"First swim"` badges solely from post–22 Nov `wallet_verified_swims` rows (contradicting existing members' recorded `Year to Date` attendance), left `/account/wallet/rewards` disconnected with a 10-row sum bug and a stale `requires_admin_confirmation = true` flag on `verified_swim_day`, and formatted swim reward reversals with raw session UUIDs outside Reward History.
- **Decision & Non-Negotiable Invariants:**
  - **All-Time Swimming Milestones vs. Post-22 Nov Swim Bubbles:** Swimming milestone counts (`all_time_swims`, `all_time_active_weeks`, `all_time_best_streak`, `all_time_current_streak`) and swim badges (`First swim`, `10 swims`, `50 swims`, `100 swims`) count **all-time attended swimmer sessions** (`PRESENT`/`LATE` on started/completed non-cancelled sessions from `attendance_service`), matching Dashboard attendance totals. Financial **Swim Bubbles** (`week_days`, `weekly_limit`, `bubbles_earned`, `bubbles_credited`, `reward_value_naira`) remain strictly scoped to the `ADR-019` programme starting **22 November 2026** (no financial backfill). The lock-holding `reconcile_swim` settlement path must never perform cross-service HTTP calls; all-time milestone enrichment runs strictly on the read-only `GET /api/v1/wallet/rewards/swims` path with graceful fallback.
  - **Rewards Placement Across Surfaces:**
    - **Dashboard (`/account`):** Compact weekly reward progress (`0 of 3 days`) is unified with live wallet balance inside one **Wallet & Rewards** hero card, paired with an inline `+1 🫧` cue on the Next Swim card when eligible.
    - **My Swims (`/account/bookings`):** Renders a slim, collapsible 1-row swim rewards strip so **Upcoming / Calendar / History** remain immediately visible above the fold.
    - **Wallet → Rewards (`/account/wallet/rewards`):** Serves as the single full rewards hub displaying All-Time Swimming Milestones, Weekly Swim Bubbles Programme, accurate server-computed lifetime `total_bubbles_awarded` (across full history, net of swim reversals), automatic rule metadata (`verified_swim_day.requires_admin_confirmation = false`), and plain-English Reward History including swim reward reversals (never exposing raw session UUIDs).
  - **Simplified Sidebar & Action-First Dashboard IA:**
    - Fold `Book My Next Swim` out of the top-level sidebar into the Dashboard and My Swims primary booking CTA (preserving `/account/book-next-swim` for deep links). Keep top-level swim links focused on **Dashboard**, **My Swims**, **Find a Swim**, and **Explore Pools**, keep **SwimBuddz Calendar** accessible without competing as a 5th top-level swim link, and surface **Rewards** directly under **Wallet & Payments**.
    - Replace duplicate top-bar + `GreetingHero` greetings with a single clean header showing independent programme pills (`ADR-001`) and live Wallet balance.
    - Use an action-first 2-column desktop bento layout (Next Swim + Wallet & Rewards hero → Quick Actions → Updates & Community + compact 2026 Swim Summary without the ambiguous YTD `"Bubbles"` tile and linking `Sessions` to `/account/bookings?view=history`).
- **Canonical Docs & Code:**
  - `swimbuddz-backend/services/attendance_service/routers/internal.py`
  - `swimbuddz-backend/services/attendance_service/services/swim_rewards.py`
  - `swimbuddz-backend/services/wallet_service/services/swim_rewards.py`
  - `swimbuddz-backend/services/wallet_service/routers/rewards_member.py`
  - `swimbuddz-frontend/src/components/layout/MemberLayout.tsx`
  - `swimbuddz-frontend/src/app/(member)/account/page.tsx`
  - `swimbuddz-frontend/src/app/(member)/account/bookings/page.tsx`
  - `swimbuddz-frontend/src/app/(member)/account/wallet/rewards/page.tsx`
  - `swimbuddz-frontend/src/components/swims/SwimRewards.tsx`

---

## ADR-021: Independent Account Registration, Modular User Journeys & Store-First Access
- **Status:** Settled & Enforced (24 November 2026)
- **Context & Why:**
  - Requiring every new registrant to pick a swimming programme (`Community` / `Club` / `Academy`), enter full contact/address details, and declare a swimming level at `/register` created unnecessary friction for Store-only shoppers, Pool Access visitors, and browsers who simply want an account first.
  - Conversely, hiding contact fields while leaving them in `isStepValid()` or bouncing already-authenticated users from `/explore` → `/club` / `/community` / `/academy` back into `/register` broke signup and trapped logged-in users in registration loops. Similarly, overly broad post-auth heuristics risked sending returning unpaid programme applicants (`approved_unpaid` / `payment_pending`) back to `/explore` or hijacking active Academy students away from `/account/academy`.
- **Decision & Non-Negotiable Invariants:**
  - **Identity-Only General Account Registration (`/register`):**
    - Regular (non-coach, non-upgrade) account creation is decoupled from swimming programme selection and requires only core identity credentials (`firstName`, `lastName`, `email`, `password >= 8 chars`) plus terms confirmation. Phone, location, and swimming level are collected later at programme onboarding or Store delivery checkout.
    - Coach signup (`?coach=true`) still collects and validates contact details (`phone`, `country`, `state`, `city`).
    - *"How did you hear about SwimBuddz?"* (`acquisition_source`) stays available on regular signup as an **optional** non-blocking field so flywheel attribution is preserved without slowing down checkout signups.
    - Omitted optional fields must be sent as `undefined` (never `""` or a default `"Nigeria"` country) when calling `POST /api/v1/pending-registrations/`.
  - **Authenticated Pass-Through on `/register` & Store Header Preservation:**
    - When an already-authenticated user visits `/register` (excluding `?upgrade=true` and `?coach=true`), the page immediately forwards them to `safeReturnPath(next) ?? destinationForGoal(goal)` (or `getPostAuthRedirectPath()` / `/admin/dashboard` for admins) so marketing CTAs reached from `/explore` (`/club` → `/register?goal=club`, `/community` → `/register?goal=community`, `/academy` → `/register?goal=academy`) never trap signed-in users on the signup form.
    - `StoreHeader` preserves the current store path on both **Login** (`/login?redirect=...`) and **Join** (`/register?next=...`).
  - **Modular Post-Auth Routing (`src/lib/journeys.ts` + `src/lib/registration.ts`):**
    - Explicit standalone storefront/offering destinations resume immediately (`ADR-017`); stale programme requests never hijack explicit session or profile deep links.
    - Active Academy learners (`academyActive`) keep precedence over secondary `club: requested` status so they still land on `/account/academy` on plain login.
    - Accounts with pending/unpaid programme obligations (`payment_pending`, `approved_unpaid`, `expired` on Academy/Club, or `payment_pending`/`expired` on Community) are never classified as `isFreshRegistration` and route directly to `/account/billing?required=<tier>` rather than `/explore`.
    - Truly fresh identity-only accounts with no destination, no requested programme, and no pending obligation land on `/explore` (which surfaces Academy, Club, Community, Store, Pools, `/account`, and `/account/orders`).
  - **Store-Only Order Access (`middlewareAccess.ts`):**
    - `/account/orders` is included in `paywallAllowedPrefixes` so registered Store customers can track orders without purchasing an annual swimming membership, while swimming programme routes (`/club/*`, `/academy/*`, `/sessions/*`) remain gated by server-backed tier entitlements (`ADR-001`).
- **Canonical Docs & Code:**
  - `swimbuddz-frontend/src/app/(auth)/register/page.tsx`
  - `swimbuddz-frontend/src/app/(auth)/register/__tests__/RegisterPage.test.tsx`
  - `swimbuddz-frontend/src/components/registration/RegistrationEssentialsStep.tsx`
  - `swimbuddz-frontend/src/app/(public)/explore/page.tsx`
  - `swimbuddz-frontend/src/lib/journeys.ts`
  - `swimbuddz-frontend/src/lib/registration.ts`
  - `swimbuddz-frontend/src/lib/middlewareAccess.ts`
  - `swimbuddz-frontend/src/components/store/StoreHeader.tsx`
  - `swimbuddz-frontend/UI_FLOWS.md`
  - `swimbuddz-frontend/ROUTES_AND_PAGES.md`

---

## ADR-022: Payments & Credits Streamlined Layout, All-Swim YTD Summary & Dashboard Polish
- **Status:** Settled & Enforced (25 November 2026)
- **Context & Why:**
  - Review of the member portal surfaced five UX and data accuracy gaps:
    1. On `/account/wallet/rewards`, the Weekly Swim Bubbles card repeated the same earning rule three times in a row (and a fourth time in the rules list directly below).
    2. On `/account`, the Next Swim card repeated `My Swims` and `Other swims` secondary links even though Quick Actions and the sidebar already surface both directly underneath/beside it.
    3. The Dashboard's **2026 Swim Summary** (`fetchYTDStats`) fired four separate quarterly report requests (`Q1–Q4`) per page load, counted only one `primary_segment` per quarter while `pool_hours` counted all swims, truncated streaks across quarter boundaries (`Math.max`), silently dropped quarters without snapshots, and failed (`403`) for members without active annual community membership.
    4. The Dashboard's `Latest updates` block occupied prime real estate even when empty (`No new announcements at the moment.`) or pinned indefinitely.
    5. On `/account/billing` (**Payments & Credits**), paid-up members had to scroll past three empty `"Nothing here right now."` obligation cards, a high-urgency black `₦0` box, a dead `#bank-transfer-receipts` anchor when no transfer was pending, duplicate `/api/v1/members/me` fetches, and four collapsed `<details>` toggles — two of which could expand into empty blank boxes (`OutstandingSessionFeesCard` returning `null`, and `ProductPaymentReceipts` filtering out all paid payments lacking `checkout_quote` metadata).
- **Decision & Non-Negotiable Invariants:**
  - **Single-Pass Rewards Copy (`SwimRewards.tsx`):** Show dynamic weekly status text only when it conveys live state (1+ earned days, today counted, weekly cap reached, or reversal offset). Keep one concise 1-line rule footnote and a single-sentence launch note when `bubbles_earned === 0 && all_time_swims > 0`.
  - **Focused Next Swim Hero (`NextSwimDashboardFocus.tsx`):** Present one primary CTA button + inline `+1 🫧` reward cue (plus the attention link only when bookings need action), omitting redundant `My Swims` / `Other swims` links.
  - **All-Swim YTD Attendance Summary (`GET /api/v1/attendance/me/ytd-summary`):** Provide one lightweight Attendance endpoint computing YTD `sessions_attended` (all `PRESENT`/`LATE` swimmer sessions across Club, Academy, and Community in Lagos time), `sessions_recorded` (`PRESENT + LATE + ABSENT`, excluding `EXCUSED`/`CANCELLED` per `ADR-004`), `attendance_rate`, `pool_hours`, and true cross-quarter Lagos-week `streak_longest` / `streak_current` for any authenticated member. The Dashboard uses this single endpoint rather than fanning out 4 quarterly report requests.
  - **Conditional Dismissible Updates Strip (`/account`):** Never render an empty announcements box on the Dashboard. Surface a slim dismissible banner only when an active published announcement is pinned or published within the last 14 days (remembering dismissal per announcement ID in `localStorage`).
  - **Streamlined Payments & Credits (`/account/billing`, extending `ADR-008`):**
    - Preserve all `ADR-008` trust invariants (server-aggregated obligations, incomplete-source warning banner, Bubbles kept separate from Store credit, double-payment protection for pending transfers).
    - Reuse `useMemberProfile()` instead of firing a duplicate `/api/v1/members/me` request.
    - Pair **Payment Status** (calm emerald `All caught up` at `₦0`; amber/rose urgency only when `> ₦0`) beside **Your Credits** in a 2-column top overview, and hide empty obligation groups (`Needs payment`, `Coming up`, `We're checking your payment`) when `serverOverview` loaded cleanly.
    - Render `PendingTransfersCard` with human-readable purpose labels only when pending transfers exist, and never render a dead `#bank-transfer-receipts` anchor when none exist.
    - Replace the four collapsed `<details>` sections with clean visible tabs (**Memberships & Plans** showing Annual Membership, Club, Academy, and Membership History; **Payment History** showing *all* payments with expandable quote breakdowns whenever `checkout_quote` metadata is present). Render `OutstandingSessionFeesCard` only when unpaid session bookings actually exist.
- **Canonical Docs & Code:**
  - `swimbuddz-backend/services/attendance_service/routers/member/lists.py`
  - `swimbuddz-backend/services/attendance_service/schemas/main.py`
  - `swimbuddz-frontend/src/components/swims/SwimRewards.tsx`
  - `swimbuddz-frontend/src/components/dashboard/NextSwimDashboardFocus.tsx`
  - `swimbuddz-frontend/src/components/dashboard/YTDStatsOverview.tsx`
  - `swimbuddz-frontend/src/app/(member)/account/page.tsx`
  - `swimbuddz-frontend/src/app/(member)/account/billing/page.tsx`
  - `swimbuddz-frontend/src/app/(member)/account/billing/_billing/FinancialOverviewCard.tsx`
  - `swimbuddz-frontend/src/app/(member)/account/billing/_billing/ProductPaymentReceipts.tsx`
  - `swimbuddz-frontend/src/app/(member)/account/billing/_billing/PendingTransfersCard.tsx`

---

## Template for Adding New Decisions

Whenever a new product, pricing, accounting, curriculum, or architectural decision is made during a conversation, append a new `ADR-XXX` entry using this structure:

```markdown
## ADR-XXX: <Short Title>
- **Status:** Settled & Enforced (<Month Year>)
- **Context & Why:** <Why was this discussed? What problem or edge case did simpler alternatives fail to solve?>
- **Decision & Non-Negotiable Invariants:** <Exact rules that code and future AI agents must preserve>
- **Canonical Docs & Code:** <Paths to runbooks, routers, models, and frontend pages>
```
