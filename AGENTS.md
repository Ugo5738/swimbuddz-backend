# AGENTS.md — Universal AI Agent Instructions for SwimBuddz

> **Applies to ALL AI Assistants:** OpenAI ChatGPT / Codex, Anthropic Claude, Google Gemini / Antigravity, Cursor, GitHub Copilot, and any automated coding or planning agent.

---

## 1. Mandatory Working Habit: "Deliberate → Document (`DECISIONS.md`) → Code"

Many product, financial, curriculum, and operational rules in SwimBuddz were reached after extensive deliberation and real-world production incidents. To prevent any AI agent from accidentally reverting or "simplifying away" a settled decision:

1. **Read Settled Decisions FIRST:**
   - Before proposing, modifying, or refactoring any product workflow, pricing rule, attendance behavior, curriculum structure, or accounting boundary, **read [`docs/DECISIONS.md`](./docs/DECISIONS.md)** (in the root `swimbuddz-docs` repo) and the corresponding domain runbook in [`swimbuddz-backend/docs/`](./swimbuddz-backend/docs/).
2. **Never Silently Override a Settled Decision:**
   - Do **not** change an invariant documented in `docs/DECISIONS.md` (`ADR-001` through `ADR-017`) or in `swimbuddz-backend/docs/*.md` / `swimbuddz-frontend/UI_FLOWS.md` as a side effect of another task.
   - If a user request appears to conflict with an existing ADR in `docs/DECISIONS.md`, point out the existing decision and its rationale, and ask whether the user wants to explicitly supersede it.
3. **Always Update Documentation When Deliberating or Building (Across All 3 Repos):**
   - **During product/feature discussions:** Whenever you and the user think through, debate, or agree on a product rule, edge case, pricing model, curriculum structure, or architecture choice, **immediately record or update the decision and *why* it was made in [`docs/DECISIONS.md`](./docs/DECISIONS.md) (in the root `swimbuddz-docs` repo) AND sync its mirrors [`swimbuddz-backend/docs/DECISIONS.md`](./swimbuddz-backend/docs/DECISIONS.md) and [`swimbuddz-frontend/docs/DECISIONS.md`](./swimbuddz-frontend/docs/DECISIONS.md)** as well as the relevant domain doc.
   - **Definition of Done:** No feature, bugfix, or policy change is complete until the docs (`docs/DECISIONS.md`, domain runbooks, `UI_FLOWS.md`, `API_ENDPOINTS.md`) are updated in the same session/PR.
4. **Strict Git Workflow (`feature/* → develop → main`):**
   - Always work on a feature/fix branch (`feature/*`, `fix/*`, `codex/*`), merge to `develop` first, and then merge `develop` to `main`. Never commit directly to `main` or `develop`, and always clean up temporary worktrees and `.next` caches after use.

---

## 2. Core Settled Invariants (Quick Summary — Full Rationale in [`docs/DECISIONS.md`](./docs/DECISIONS.md))

- **ADR-001 (Independent Membership vs. Programmes):** Annual SwimBuddz Membership (`community`, ₦20,000/yr) is independent of **Club** and **Academy** participation programmes (`build_membership_status_summary`). Never collapse them into a single hierarchical `membership_tier` rank.
- **ADR-002 (Negotiated Commercial Terms vs. Discounts):** Custom Academy tuition totals/installment schedules (`AcademyCommercialAdjustment`, set pre-payment) and temporary agreed Club session fees (`ClubCommercialAdjustment` on dated `transition_per_session` enrollments for regular `plan_included` practices only—excluding `paid_addon` and `club_visit`) are stored as auditable commercial adjustments—never as ad-hoc discount codes or edits to public prices.
- **ADR-003 (Stage-First Bank Receipts & Pooled Allocations):** Uploading a bank receipt (member checkout or admin walk-in) stages evidence (`pending_review`) and extracts untrusted OCR hints; it **never** settles a payment (`PAID`) or grants entitlement until an admin verifies bank credit with a bank reference. Shared transfers use `AcademyBankReceipt` + `AcademyReceiptAllocation` so cash-in is posted once.
- **ADR-004 (Academy Attendance Defaults & Per-Student Session Participation):** Regular `included` Academy classes default enrolled students to `Present` via the Attendance worker; saved exceptions (`Absent`, `Excused` ≥24h, `Late`, `Cancelled`) always win. Excused absences do not consume class entitlement; unexcused absences do. A single physical session supports mixed learner participation (`prepaid` vs. `paid_extra`) via `AcademyClassAllocation` after a verified opening-balance cutoff—**never create two duplicate sessions for the same pool/time**.
- **ADR-005 (Location-First Club Readiness & Mobility):** Club checkout is locked until a `ClubApplication` is approved (`club_ready` / `club_ready_modified`) after video or in-pool assessment. Visiting another Club resolves as `club_visit` without changing home Club/Pod; permanent moves use `ClubEnrollmentTransfer`.
- **ADR-006 (Unified Participant Admission, Guest Waivers & Consolidated Settlement):** Admin Attendance uses a single participant manager with authoritative server fee quotes, counts guest walk-ins toward capacity, gates unregistered walk-in attendance on waiver completion, supports consolidated retrospective session/guest balance checkout, and uses UUID4 payment references.
- **ADR-007 (Payment Settlement Invariants, Wallet Holds & Learner Payment Links):** Owning services own unit prices; capacity is reserved with TTL before checkout; `1 Bubble = ₦100` exact with wallet holds for mixed checkout; `academyPaymentPath` shares learner-authenticated checkout links and allows `dropout_pending` installment recovery.
- **ADR-008 (Double-Entry Ledger & Financial Obligations Aggregator):** `ledger_service` maintains immutable double-entry accounting across services; `/account/billing` aggregates all programme and session obligations without flashing empty states while loading.
- **ADR-009 (Academy Quizzes, Milestone Evidence & Coach/Admin Overrides):** `AcademyQuizAssignment` freezes question snapshots per cohort week; milestone rejections require coach feedback; admin overrides (`OVERRIDE_COACH`) require `override_reason` and preserve the original coach's `reviewed_by_coach_id`, while `media_audit_logs` records every admin view/download of student evidence.
- **ADR-010 (Modular Academy Curriculum, Independent Milestones, Drills & Competency Ledger):** Sell outcome pathways (*Beginner-to-50m*, *Water-Safe Starter*) backed by competency-gated modules (`F1–F4`, `S1–S4`, `A1–A2`) and an independent library of reusable **Milestones** (`program_milestones` + `cohort_milestones` overlays), **TI-Aligned Drills** (`academy_drills` with 1 primary focal point + `milestone_remedial_drills`), **Skills**, **Lessons**, **Quizzes**, and a portable **Learner Competency Ledger** (`learner_milestone_credits`) (`ACADEMY_INDEPENDENT_CURRICULUM_AND_DRILLS_DESIGN.md`).
- **ADR-011 (Calendar Projection, Event-Backed Sessions & My Swims IA):** Gateway projects `session` and `event` activities with independent dimensions; `community_swim` pairs an Event with an operational Session (closing Event RSVP while linked); `/account/swims` unifies **Upcoming**, **Booked**, **History**, and **All** with published upcoming sessions shown by default.
- **ADR-012 (Session Media Vault, Immutable Originals & Clip Editor):** Direct-to-S3 multipart uploads (2 GiB/video, 20 GiB/vault cap) keep originals immutable, generate H.264 review proxies + hover previews via `media-worker`, grant attendees access to their session collection + clip editor, and reconcile S3 access-log bandwidth.
- **ADR-013 (Stroke Lab TI Coaching & Visibility Rules):** Coaches in Total Immersion order (**Balance → Streamline/Drag → Propulsion**, one focal point at a time) and never hallucinates camera-invisible aspects (no underwater catch or side-view crossover claims from above-water side clips).
- **ADR-014 (Public Pool Directory & Weather):** Separates community pool submissions/corrections from official SwimBuddz Pool Assessments with weather integration.
- **ADR-015 (Member Identity Audit & Soft/Hard Delete):** Self-service name changes log immutable `MemberNameChange` rows. Soft delete sets `is_active = False` (moving the member to the `Deactivated` tab on `/admin/members` with reactivation support); Hard delete cleans up FK-dependent rows across all services first and deletes the Supabase Auth user only after DB commit.
- **ADR-016 (Git Branching, Migrations & Worktree Hygiene):** Work strictly on `feature/* → develop → main`, generate Alembic migrations via `./scripts/db/migrate.sh` (single head), and prune worktrees/caches promptly.
- **ADR-017 (Swimwear Size Chart Acknowledgment & Store Purchase Experience):** Store cart items surface `requires_size_chart_ack`; `/store/checkout` requires acknowledgment when applicable; product pages support 1-click **Buy Now**; store back-links stay within `/store`; and `/login?redirect=` / `/confirm?next=` preserve standalone store/public return paths after authentication.

---

## 3. Repository Map & Required Reading

- **Master Decision Log:** [`docs/DECISIONS.md`](./docs/DECISIONS.md)
- **Master Documentation Index:** [`DOCUMENTATION_INDEX.md`](./DOCUMENTATION_INDEX.md)
- **Full AI Assistant Guide:** [`CLAUDE.MD`](./CLAUDE.MD)
- **Backend Repo (`swimbuddz-backend/`):**
  - [`swimbuddz-backend/AGENTS.md`](./swimbuddz-backend/AGENTS.md) & [`swimbuddz-backend/docs/AGENT_INSTRUCTIONS.md`](./swimbuddz-backend/docs/AGENT_INSTRUCTIONS.md)
  - [`swimbuddz-backend/docs/CONVENTIONS.md`](./swimbuddz-backend/docs/CONVENTIONS.md)
  - [`swimbuddz-backend/docs/DECISIONS.md`](./swimbuddz-backend/docs/DECISIONS.md)
- **Frontend Repo (`swimbuddz-frontend/`):**
  - [`swimbuddz-frontend/AGENTS.md`](./swimbuddz-frontend/AGENTS.md) & [`swimbuddz-frontend/AGENT_INSTRUCTIONS.md`](./swimbuddz-frontend/AGENT_INSTRUCTIONS.md)
  - [`swimbuddz-frontend/CONVENTIONS.md`](./swimbuddz-frontend/CONVENTIONS.md)
  - [`swimbuddz-frontend/UI_FLOWS.md`](./swimbuddz-frontend/UI_FLOWS.md)
