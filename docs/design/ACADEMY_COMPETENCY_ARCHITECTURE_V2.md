# SwimBuddz Academy — Competency Architecture V2

**Status:** Proposed implementation contract; replaces the unverified implementation assumptions in `ACADEMY_INDEPENDENT_CURRICULUM_AND_DRILLS_DESIGN.md` after review.  
**Date:** 18 November 2026  
**Scope:** Academy curriculum, evidence, assessment, progression and their interfaces to Club and Stroke Lab.  
**Related:** ADR-004, ADR-009, ADR-010, ADR-013, ADR-016.  
**Explicit non-goal:** Rewriting attendance, payments, classes remaining, bank transfers or the Club payment model.

## 1. Product outcome and guiding rules

Build a **competency-based swimming institution**, not a sequence of 12 attendance checkboxes.

A learner can enrol in a 12-class programme, accelerated pathway, standalone module, or assessment-led pathway. What they *buy* (classes, cohort, commercial terms) must remain distinct from what they *master* (skills and approved evidence). The same competency can be taught in several programmes, assessed at more than one time, and recognized later without falsifying historical records.

The system must answer five questions reliably:

1. What should this learner learn next, considering verified prerequisites and their current programme?
2. Which ordered lesson/drill progression should the coach teach in this physical session?
3. What exact rubric and evidence did the learner pass, in which conditions, and who approved it?
4. Which module/qualification/Club requirements are satisfied **now**, and on what basis?
5. If an assessment is overturned, what derived entitlements, badges and certificates must be reconsidered?

**Safety and assessment principles:** practical claims are not self-certified; a video-backed, independently reviewed verification is the normal basis for a competency credit. A note that a learner attended a class is not an assessment. A quiz does not prove in-water ability. AI recommendations never independently issue practical credentials. A credit is *recognition of demonstrated learning*, not a discount or prepaid-class adjustment.

## 2. Canonical vocabulary and responsibilities

| Concept | Meaning | Version/reuse |
|---|---|---|
| Skill | Atomic capability (e.g. controlled exhalation, balanced supine float) | Global, stable ID; tag relationships |
| Drill | Repeatable exercise to develop skills; equipment, safe setup, steps, single primary focal point and optional prescription | Global versioned blueprint |
| Lesson Blueprint | Reusable teaching plan: objective, blocks, drill sequence, safety, media, assessment opportunities | Global versioned blueprint |
| Knowledge Quiz | Cognitive assessment of safety, technique or rules; separate from in-water competency | Global versioned questions and assignment snapshot |
| Milestone | Independently testable outcome with measurable rubric, required evidence, assessment conditions and validity rules | Global versioned definition |
| Module | Stackable, prerequisite-gated learning unit comprising lessons, theory and assessment outcomes | Global versioned release |
| Programme/Pathway | Published offering assembled from module releases; commerce and pricing separate | Published release |
| Cohort | Operational delivery of a published programme release, with controlled overrides | Frozen baseline + audited revisions |
| Physical Session | Actual pool/time/coach/roster; several enrolled students may follow different individualized plans in one session | Immutable delivery record and attendance |
| Verification | One dated evidence-backed human assessment decision for one learner | Append-only provenance |
| Current competency | Read projection over valid verifications, expiry, revocation, evidence validity and equivalence policy | Recomputable, never sole source of truth |

A lesson is not a physical session. A drill is not a skill. A passed quiz is not a passed practical milestone. A programme week is a scheduling guide, not proof of readiness.

### Example

`SKILL-BREATH-RHYTHM` is developed with `DRILL-WALL-EXHALE` in a reusable `LESSON-BREATH-CONTROL`. `MILESTONE-BREATH-RHYTHMIC-20` defines exactly how to assess it. The milestone can be a requirement in Water-Safe Starter, Beginner-to-50m and an accelerated module without duplication. A swimmer's verified result can be recognized in another programme, subject to recency and context rules. The same 60-minute physical class can serve people who need different drills.

## 3. Data architecture

Keep the existing Academy data and APIs during migration; **expand, backfill, dual-read, switch, then retire**. Introduce typed tables with real foreign keys instead of unvalidated polymorphic `scope_type/scope_id` references.

### 3.1 Catalog and versioning

- Extend `skills` with stable `code`, `status`, tags and governance metadata. Preserve existing IDs.
- Introduce `academy_drills` + `academy_drill_versions`, with focal cue, movement steps, target skills, common errors, equipment, intended context, supervising adult/coach requirement, minimum prerequisites, safe depth/environment, contraindications, media references and an optional default prescription.
- Introduce `academy_lesson_blueprints` + `academy_lesson_versions`. A lesson version contains ordered blocks with duration, objectives, safety briefing, and per-block drill assignments. Avoid copying a blueprint every time it is attached to a module.
- Evolve `milestones` into stable canonical definitions. Add immutable `milestone_versions` with test conditions, success rubric, evidence policy, media references and safety-gate flag. A published version is never modified; correcting a rubric publishes the next version.
- Evolve `academy_quizzes` into reusable quiz definitions with immutable released question sets. Existing `academy_quiz_assignments.questions_snapshot` and attempts remain the historical contract; additional lesson/module/programme context is provided by typed assignment tables, not one `quiz.lesson_id`.
- Introduce `academy_modules` and `academy_module_versions` (including a prerequisite DAG, required milestone versions, knowledge checks and ordered lesson placements).
- Assemble programme releases from specific module versions. Existing `programs`, `program_curricula` and price tables remain, with a migration path from current curriculum weeks/lessons.

**Stable ID versus frozen standard:** stable milestone identity means `can tread water for 30 seconds`; a milestone *version* captures its exact evaluated wording, environment, scoring and evidence policy at the time. Do not use a mutable string `code` as the evidence identity or use only a `module_code` label as the module relationship.

### 3.2 Explicit associations and scoped assignments

Use typed association tables: `milestone_skill_links`, `drill_skill_links`, `lesson_version_drills`, `module_version_lessons`, `module_version_milestone_requirements`, `module_version_quiz_requirements`, `programme_release_modules` and `cohort_requirement_overrides`.

Every association has an explicit owning published version, stable order, optional/required flag where appropriate, and authorization rules. Only allow changing a **draft** release. Published cohort overrides are **new audited revisions**, never destructive edits of a completed cohort's historical contract.

A common **effective-requirements resolver** constructs the list from the cohort's pinned programme/module releases plus authorized overrides, while applying safety invariants. All coach views, student views, reports, eligibility and certificate issuance consume the same result.

- Cohort overrides may move *teaching/target week* or add advanced competencies, but may not silently remove mandatory safety requirements.
- Personal learning plans can recognize prior credits without altering the cohort's canonical safety standard or the number of purchased classes.
- Support `introduced`, `practiced` and `assessed` lesson-to-milestone relationships; these should never imply a student passed the milestone.
- A unit may be sequenced by prerequisites rather than by week. Week numbers are suggested pacing only.

### 3.3 Evidence, assessments, and portable credentials

Keep `student_progress` and `milestone_review_events` for compatibility; introduce:

1. `learner_assessment_submissions`: learner, enrolment (optional for diagnostic), milestone-version ID, source, uploaded media ID, submitted timestamp, capture context, review state.
2. `learner_assessment_events`: **append-only** review/override/rejection/revocation history with reviewer identity, reason, score/rubric answers, evidence references and original event links.
3. `learner_competency_verifications`: immutable successful assessment facts, each with an independent ID; many valid verifications per learner and milestone are permitted.
4. `learner_competency_current`: refreshable projection for one learner and canonical competency, with status, chosen supporting verification IDs, expiry and recheck requirement.

`UNIQUE(member_id,milestone_id)` belongs on the **projection**, not the historical verification table. A revocation invalidates only the targeted verification; recompute whether any other qualifying verification remains. The system must never grant lifetime achievement to an unverified legacy status by assumption.

A practical achievement requires the appropriate published rubric, authorized reviewer, valid and accessible evidence, recognized capture/supervision conditions, and safety prerequisites. For qualification-bearing milestones, the default is **video evidence required and independently reviewed**. An alternate evidence route, if ever allowed for accessibility or technical failure, must be explicitly approved in policy and captured with reason, evidence and privileged independent review.

Legacy `RequiredEvidence.NONE` records remain recognized under their original historical rules until audited; do not retrospectively claim they satisfied the new video standard. Alumni supplementary videos are not automatically grades.

### 3.4 Record and permission safety

The old generic `POST /academy/progress` direct status path must be deprecated in favour of a single decision service with explicit commands: submit, review, reject, resubmit, overturn, expire and reassess. Clients cannot supply their own reviewer identity or approval timestamp.

- Student: own enrollment, assigned content, uploaded evidence, feedback and credit history.
- Coach: assigned cohort/student and permissible review queue, not arbitrary enrollment data.
- Admin: audited privileged intervention; no silent override.
- AI: recommendation only until an explicit, separately governed assessment feature is approved.
- Media IDs must be checked through the owning media service for ownership, linked enrollment/milestone, correct type, availability and content policy. Signed playback is authorized and short-lived.
- Never permit a student to set an `ACHIEVED` record back to pending through an ordinary self-claim.
- Approval, reward and downstream projections use an idempotency key from the verified **decision**, and the reward recipient is the student.

Consider an explicit status machine (`not_started`, `submitted`, `reviewing`, `needs_practice`, `verified`, `revoked`, `expired`) as a projection; preserve old `pending/achieved` semantics for old API consumers until switched.

### 3.5 Diagnostic credit, class allocation and Club

A diagnostic assessment may credit module outcomes for someone enrolling mid-pathway. The credit is a *pedagogical* decision. Existing payments, fees, installments, `AcademyClassPlan`, `AcademyClassAllocation`, attendance, absence/makeups and Club commercial entitlements are **unchanged** unless a separate explicit commercial action is approved.

Club eligibility is a separate ruleset and assessment decision. A member must meet the existing `25 m continuous swim + 30 s tread` policy (or any later approved version) under appropriate observed conditions and recency. A prior Academy verification can supply supporting evidence, but it does not automatically authorize unsafe Club admission or bypass first-session verification.

### 3.6 Certificates and history

Certificate eligibility requires the **cohort's frozen requirements** satisfied by permitted current verifications, passing assigned knowledge checks, and any programme completion conditions. Issued certificate records retain their issuance snapshot and code, while the verification endpoint shows their later validity/revocation status; do not silently rewrite a historical certificate or erase its issuance.

When an approval is overturned, recompute dependent current competency, module completion, readiness, certificates, rewards and any other downstream flags. Notify the learner and record what changed. A cancellation, refund or transfer does not implicitly rewrite technical mastery.

## 4. Learning experience

### Admin Curriculum Studio

Create independent library navigation for Skills, Drills, Lesson Blueprints, Milestones and Quizzes; version history, draft/publish/retire, search/tagging, and usage impact preview. Build Modules from released components and Programmes from Modules. Show cohort overrides as auditable diffs against their pinned release.

### Coach session deck

For a physical session, show safety checks, attendance/roster, each learner's verified prerequisites and next outcomes, teaching blocks, ordered drill progression with **one focal cue at a time**, and the precise rubric/evidence capture process. Record what was actually delivered, not merely what the timetable suggested. Allow coach-prescribed supervised homework drills, with clear scope and safety guidance.

### Learner workspace

Show next session, lessons and prerequisite progress, assigned drills, quiz availability, submitted evidence, coach feedback, required resubmissions, achievements and prior credits. Display the reason that a prior credit is accepted, expired or requires reassessment. Do not overwhelm a learner with the entire library.

### Stroke Lab AI

AI may identify supported, visible faults and recommend canonical `drill_version_id` values using the existing single-focal-point and camera-angle constraints. Do not claim underwater mechanics from footage that cannot reveal them; do not issue credentials from the recommendation.

## 5. Database migration and API rollout

### Phase 0 — secure current state (blocking)

- Correct Academy enrollment/progress ownership checks and invalid milestone claims.
- Eliminate generic direct-achievement bypass or route it through the same evidence validator as coach review.
- Harden media validation, replay/duplicate review concurrency and event auditing.
- Fix mismatched `CurriculumLesson.video_url` versus `video_media_id` handlers and synchronization.
- Establish integration tests and baseline performance/DB traffic budgets.
- Confirm `main/develop` ancestry and ongoing work; avoid overwriting other agents' changes.

### Phase 1 — catalog and versioned requirements (additive only)

Add tables, indices, FK constraints, immutable-release validations and models; update `services/academy_service/alembic/env.py` explicit `SERVICE_TABLES` and schema registry. Do not drop legacy columns yet. Prefer small single-head migrations; add indexes concurrently where appropriate outside normal transaction and measure lock impact.

### Phase 2 — historical reconciliation

Inventory duplicate milestones and programmes. Map old IDs to canonical IDs + version, retaining all old IDs and source events. Backfill **provisional** legacy verification candidates into a staging table with provenance and confidence. Review mismatched, missing-evidence and duplicate cases; promote only eligible verified evidence. Produce counts, checksums and dry-run rollback reports. No silent merges or automatic trust inflation.

### Phase 3 — authoritative services and dual read

Introduce the requirements resolver, evidence decision service, history ledger and projection. Shadow-compare old vs new results for real cohorts without changing existing eligibility. Log discrepancies without student media or PII. All write events must be idempotent and atomically persist decision+outbox; avoid best-effort write-after-commit inconsistencies.

### Phase 4 — authoring, teaching, learning

Release Curriculum Studio for admins, coach session deck and learner workspace behind feature flags. Add quiz reuse, remedial prescriptions, assignment revisions, diagnostic assessments and batch APIs. Budget SQL queries and media presigning to avoid Supabase limits, especially on coach roster screens.

### Phase 5 — credentials, Club, AI and retirement

Switch graduation and certificate consumers, then Club readiness, then optional Stroke Lab recommendations. Decommission legacy paths only once there are zero old-client consumers, dual-read parity holds and retention requirements are satisfied.

## 6. Acceptance tests (release blocking)

- Unauthorized/other-member enrollment, progress, evidence, answers and certificate access is denied; assigned coach/admin access is correct.
- Invalid cross-programme or non-assigned milestone claims fail.
- A verified achievement cannot be reset by student resubmission; concurrent reviewers cannot both approve one submission.
- Missing, wrong-owner, wrong-type, broken or differently linked video cannot establish a competency.
- A new rubric version does not mutate old cohort/verification history.
- A prior valid verification can satisfy another programme's equivalent requirement; independently valid evidence survives revocation of one source.
- Safety gates cannot be bypassed by cohort override or prior credit from unsuitable conditions.
- Quiz answer keys remain inaccessible before legitimate grading; attempts and published question snapshots cannot be rewritten.
- Makeups, shared physical sessions, prepaid/extra classes, fee history, memberships and attendance retain existing semantics.
- Coach/student/admin UI and report/certificate endpoints agree on one effective requirements result.
- Migration and rollback rehearsals preserve IDs, references, audit events and media access. OpenAPI, Ruff, unit, integration, schema and frontend CI pass.
- Baseline and rollout measurements include SQL count per roster, connection/egress impact, media-presign request volumes and stage gates.

## 7. Explicit decisions and non-decisions

**Recommended product decisions:** reusable and versioned Skills/Drills/Lessons/Milestones/Quizzes/Modules; competency-first progression with adaptable schedules; independent video-backed practical verification; prior credit only when trusted; strict safety and role boundaries; no AI award of mastery; keep commerce and attendance independent.

**Do not infer from this draft:** universal video requirements for all historical legacy milestones, automatic credit equivalence for every skill with a similar name, automatic certificate issuance, automatic Club admission, tuition credit/refund, or permission to enable new migrations in production.

**Before implementation:** turn these rules into a reviewed ADR (superseding only the relevant clauses in ADR-009/010, not unrelated business policies), inspect existing data realities, specify assessment validity/recency per milestone category and authorize specific migration releases. The previous design document should be labeled superseded only after the replacement ADR is accepted, not merely because a draft exists.
