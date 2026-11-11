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

## ADR-010: Modular Academy Curriculum, Independent Milestones, Drills, Lessons & Competency Ledger
- **Status:** Settled Architecture & Blueprint (Aug–Nov 2026, `MODULAR_CURRICULUM_PATHWAY.md`, `ACADEMY_INDEPENDENT_CURRICULUM_AND_DRILLS_DESIGN.md`)
- **Context & Why:**
  - Selling only a monolithic 12-week Beginner-to-50m course forced swimmers with partial skills to repeat basics and limited entry points to three intakes a year. Conversely, an unguided à-la-carte lesson menu causes choice overload and lets learners skip water safety.
  - Furthermore, hard-locking `Milestone` and `AcademyQuiz` rows to a single `program_id` forced curriculum duplication across pathways (*Water-Safe Starter*, *Beginner-to-50m*, *Accelerated Programme*), prevented cohorts from customizing milestone schedules without mutating the parent Program, prevented learner milestone credit from carrying across programs, and lacked a first-class **Drills** entity (*how* to practice a skill in the water with a single TI focal point).
- **Decision & Non-Negotiable Invariants:**
  - **Sell Outcomes/Pathways, Deliver Competency-Gated Modules:** Customers buy clear outcome pathways (e.g., *Beginner-to-50m Freestyle*, *Water-Safe Starter*, *Breaststroke Pathway*) composed of stackable Foundation (`F1–F4`), Stroke (`S1–S4`), and Deep-Water/Distance (`A1–A2`) modules.
  - **Assessment Grants Prerequisite Credit:** Free Intro-to-Water clinics are diagnostic assessments (not free lessons) that grant demonstrated competency credits so learners enter at the right module boundary without paying twice for passed skills.
  - **Group Intake Realities vs. Private Coaching Demand Indicators:**
    - All standard Academy seasonal intakes operate as small-group cohort classes (4–6 students per coach).
    - The onboarding readiness questionnaire (`AcademyDetailsStep.tsx`) collects format preferences (group vs. private 1-on-1). To avoid misleading new registrants into expecting private 1-on-1 classes at standard group tuition, the form clearly disclaims that all cohort intakes are group-based, and that 1-on-1 indicators are recorded for personalized recommendations and future private coaching demand.
  - **Independent Reusable Curriculum Library (`ACADEMY_INDEPENDENT_CURRICULUM_AND_DRILLS_DESIGN.md`):**
    - **Canonical Milestones (`milestones` + `program_milestones` + `cohort_milestones` + `lesson_milestones`):** `milestones.program_id` is nullable; canonical milestones carry a unique `code` (`MS-F1-...`), `category`, `module_code`, and `is_safety_gate`. Programs attach canonical milestones via `program_milestones`; individual cohorts customize their effective milestone set (adding, overriding target week/order, or excluding non-safety-gate milestones) via `cohort_milestones` without mutating the parent Program.
    - **Portable Learner Competency Ledger (`learner_milestone_credits`):** Keyed by `(member_id, milestone_id)`. Achieving a canonical milestone in any cohort or diagnostic assessment syncs to `learner_milestone_credits` so subsequent cohort enrollments automatically recognize **Prior Credit** without re-testing mastered competencies.
    - **TI-Aligned Drills Library (`academy_drills` + `lesson_drills` + `skill_drills` + `milestone_remedial_drills` + `student_drill_assignments`):** Every drill defines a `ti_stage` (`water_safety`, `breathing_control`, `balance_comfort`, `streamline_drag`, `propulsion_timing`, `recovery`), **one `primary_focal_point`**, `common_mistakes_json`, `equipment_json`, `default_prescription_json` (sets × distance + rest), and a demo video. Pending or rejected milestones automatically surface linked remedial drills, and coaches/Stroke Lab AI can assign personalized drills to a student's enrollment.
    - **Library Quizzes (`academy_quizzes`):** `program_id` is nullable so water-safety and hydrodynamic quizzes live in the reusable library while `AcademyQuizAssignment` continues to freeze cohort-week question snapshots (`ADR-009`).
- **Canonical Docs & Code:**
  - `docs/design/ACADEMY_INDEPENDENT_CURRICULUM_AND_DRILLS_DESIGN.md`
  - `swimbuddz-backend/docs/design/ACADEMY_INDEPENDENT_CURRICULUM_AND_DRILLS_DESIGN.md`
  - `docs/academy/MODULAR_CURRICULUM_PATHWAY.md`
  - `swimbuddz-backend/services/academy_service/models/program.py`
  - `swimbuddz-backend/services/academy_service/models/progress.py`

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
- **Canonical Docs & Code:**
  - `swimbuddz-backend/docs/design/MEDIA_VAULT.md`
  - `swimbuddz-backend/docs/operations/MEDIA_VAULT_BANDWIDTH_RECONCILIATION.md`

---

## ADR-013: Stroke Lab AI Video Analyzer & Total Immersion (TI) Coaching Doctrine
- **Status:** Settled & Enforced (Jul–Sep 2026, PRs `#208`, `#210`, `#232`)
- **Context & Why:**
  - Generic LLM swim advice often tells adult learners to "kick harder" or "pull harder" (increasing drag and exhaustion) or hallucinates underwater catch/crossover faults that are impossible to see from an above-water side camera.
- **Decision & Non-Negotiable Invariants:**
  - **TI-Informed Priority Order (`STROKELAB_TI_COACHING_KNOWLEDGE_BANK.md`):** Stroke Lab coaches efficiency-first in strict order: **1. Balance & Comfort → 2. Drag Reduction & Streamline (Shape the Vessel) → 3. Whole-Body Rotation & Propulsion**, prescribing **one focal point / drill at a time**.
  - **Strict Camera-Angle Visibility Rules ("Honesty Over Helpfulness"):** Never diagnose underwater catch/pull from above-water clips, and never claim midline crossover from side-on footage. Return `unclear` / `unavailable` when an aspect is not visible.
  - **Public & Member Analyzer:** Public guest analyzer enforces compute isolation, credit checks, and worker ready-emails (`STROKELAB_PUBLIC_ANALYZER_DESIGN.md`).
- **Canonical Docs & Code:**
  - `docs/design/STROKELAB_TI_COACHING_KNOWLEDGE_BANK.md`
  - `docs/design/STROKELAB_VLM_COACH_DESIGN.md`
  - `docs/design/STROKELAB_PUBLIC_ANALYZER_DESIGN.md`

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
  - **Store Navigation & Return Paths:** In `StoreHeader`, the top-left return arrow (`ArrowLeft`) is strictly context-aware: when inside the store (`/store/product/*`, `/store/cart`, `/store/checkout`), it links back to `/store` ("← Store") so shoppers browsing multiple items never get dumped on the main website homepage (`/`). The main site link ("← Home") is only shown on the root store catalog (`/store`). Furthermore, the product detail page provides an explicit "Back to Store" link and a "Keep Shopping" action on post-add banners, while header login preserves the current store path in `?redirect=`.
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

## Template for Adding New Decisions

Whenever a new product, pricing, accounting, curriculum, or architectural decision is made during a conversation, append a new `ADR-XXX` entry using this structure:

```markdown
## ADR-XXX: <Short Title>
- **Status:** Settled & Enforced (<Month Year>)
- **Context & Why:** <Why was this discussed? What problem or edge case did simpler alternatives fail to solve?>
- **Decision & Non-Negotiable Invariants:** <Exact rules that code and future AI agents must preserve>
- **Canonical Docs & Code:** <Paths to runbooks, routers, models, and frontend pages>
```
