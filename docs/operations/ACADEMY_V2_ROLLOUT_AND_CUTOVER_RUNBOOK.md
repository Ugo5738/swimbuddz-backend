# SwimBuddz Academy V2 — Staged Rollout & Cutover Runbook

**Owner:** Academy & Platform Engineering  
**Governing ADRs:** [`ADR-010`](../DECISIONS.md#adr-010-modular-academy-curriculum-versioned-catalog--evidence-backed-competency-passport-v2), [`ADR-005`](../DECISIONS.md#adr-005-location-first-club-onboarding-readiness-assessment-gate--cross-location-mobility), [`ADR-013`](../DECISIONS.md#adr-013-stroke-lab-ai-video-analyzer--total-immersion-ti-coaching-doctrine), [`ADR-015`](../DECISIONS.md#adr-015-member-identity-audit-membernamechange-self-service-guardrails--softhard-delete)  
**Default Production Mode:** `ACADEMY_V2_GRADUATION_MODE=legacy` (zero live behavioral change until Stage 4 sign-off)

---

## Preconditions

Before starting Stage 1 in staging or production:

1. Confirm all Academy migrations are applied and at a single Alembic head (`alembic -c services/academy_service/alembic.ini current`).
2. Confirm `academy-service`, `members-service`, `ai-service`, and the frontend deploy include Phases 1–5B (`ADR-010`).
3. Confirm `ACADEMY_V2_GRADUATION_MODE` is unset or explicitly `legacy` before running Stage 1–2.

---

## Stage 1 — Seed the Idempotent Canonical V2 Curriculum Catalog

The baseline seed script populates canonical **Drills** (including all 6 Stroke Lab TI drills), **Milestones** (with `safety_gate` and `validity_days` policies), **Knowledge Quizzes**, **Lesson Blueprints**, **Modules** (`F1–F4`, `S1`, `A1`), and **Milestone Remedial Drills**.

- **Safety:** Strictly additive and idempotent. Never mutates legacy `programs`, `cohorts`, `enrollments`, `student_progress`, or already-published V2 catalog versions.

```bash
# Run inside the academy-service container or an environment with DATABASE_URL set:
python scripts/seed/academy_curriculum_v2.py
```

**Verify:**
1. Open `/admin/academy/curriculum-library` and confirm published `v1` releases exist for:
   - Drills: `DRL-F1-WALL-WALK`, `DRL-F1-CHIN-TUCK-STAND`, `DRL-F2-HUMMING-BOBS`, `DRL-F3-WEIGHTLESS-HEAD`, `DRL-F4-SUPERMAN-GLIDE`, `DRL-S1-TI-SKATE`, `DRL-S1-6-KICK-SWITCH`, `DRL-S1-ONE-GOGGLE-BREATH`, `DRL-S1-SHARK-FIN-RECOVERY`, `DRL-S1-MAIL-SLOT-ENTRY`, `DRL-A1-SCULLING-TREAD`.
   - Milestones: `MS-F1-SAFE-ENTRY`, `MS-F2-RHYTHMIC-BOBS-20`, `MS-F3-BACK-FLOAT-30S`, `MS-F4-STREAMLINE-GLIDE-7M`, `MS-S1-FREE-25M`, `MS-S1-FREE-50M`, `MS-A1-TREAD-60S`.
   - Modules: `MOD-F1` through `MOD-F4`, `MOD-S1`, `MOD-A1`.
2. Re-running `python scripts/seed/academy_curriculum_v2.py` completes with zero duplicates and zero mutations.

---

## Stage 2 — Attach Modules to Programmes & Freeze Active Cohorts

1. Open `/admin/academy/requirements`.
2. For each active Academy programme (e.g. *Beginner-to-50m Freestyle*, *Water-Safe Starter*):
   - Attach the ordered published V2 modules (and any programme-specific direct milestones).
3. For each active or upcoming cohort:
   - Preview effective requirements (`GET /api/v1/academy/requirements/cohorts/{cohort_id}/effective`).
   - Add any cohort-specific supplementary milestones (with explicit `override_reason`) if needed.
   - Click **Freeze Cohort Requirements** to persist the SHA-256-protected immutable `AcademyCohortRequirementsSnapshot`.
4. Confirm coaches see the V2 milestone assessment & TI drill prescription workspace on `/coach/students/[enrollmentId]`, and learners see their prior credits and remedial drills on `/account/academy/enrollments/[id]`.

---

## Stage 3 — Enable Shadow Telemetry & Achieve Dual-Read Parity

1. Set `ACADEMY_V2_GRADUATION_MODE=shadow_log` on `academy-service` and `academy-worker` (or keep `legacy` and inspect on demand).
   - In `shadow_log`, legacy milestone completion continues to govern all certificates and PDF downloads, while the daily certificate job emits non-PII parity summaries (`cohort_id`, `total`, `aligned`, `legacy_only`, `v2_only`, `integrity_err`).
2. Open `/admin/academy/competencies` → **Graduation parity (shadow)** for every active cohort.
3. For any learner flagged **`Legacy-Only Eligible`**:
   - Click **Review in Promotion Queue →**.
   - Review the original student/coach video evidence, attest every published rubric criterion, and record a substantive `equivalence_reason` (`>= 30` chars) to issue their evidence-backed V2 verification (or note why video re-verification is genuinely required).
4. **Exit Gate for Stage 3:**
   - Every active frozen cohort reports `snapshot_integrity_valid = true`, `legacy_only_eligible_count = 0` (`zero_legacy_regression = true`), and human review confirms any `v2_only_eligible` learners legitimately earned native V2 or diagnostic credits.
   - Club reviewers confirm the advisory V2 competency card in `/admin/club-applications` displays accurate safety gates and expiry/revocation alerts while preserving human approval (`ADR-005`).

---

## Stage 4 — Owner-Approved V2 Graduation Consumer Switchover

Only after explicit owner approval confirming Stage 3 parity holds:

1. Set environment variable on `academy-service` AND `academy-worker`:
   ```env
   ACADEMY_V2_GRADUATION_MODE=v2_enforced
   ```
2. Restart `academy-service` and `academy-worker`.
3. Verify `GET /api/v1/academy/competencies/admin/graduation-mode` (and the banner inside `/admin/academy/competencies` → **Graduation parity**) reports **`v2_enforced`**.

**Behaviour in `v2_enforced`:**
- **Cohorts WITH a frozen V2 snapshot:** Both `check_and_issue_certificates()` and `GET /academy/enrollments/{id}/certificate.pdf` require:
  1. Valid SHA-256 snapshot integrity (fails closed if tampered).
  2. Live `verified` V2 credentials (non-expired, non-revoked) for every required milestone and `safety_gate` (recognizing prior cohort and diagnostic clinic credit).
  3. Passing scores on all required cohort knowledge quizzes (`AcademyQuizAssignment.required = true`).
- **Cohorts WITHOUT a frozen V2 snapshot (historical cohorts):** Automatically fall back to legacy milestone completion so historical certificates and downloads never break.
- **Revocation / Expiry enforcement:** Revoking or expiring a required V2 safety gate immediately suspends certificate PDF download (`409 Conflict`) without altering the historical issuance record.

---

## Instant Zero-Migration Rollback

If an unexpected operational discrepancy is discovered after Stage 4:

1. Set `ACADEMY_V2_GRADUATION_MODE=legacy` (or `shadow_log`) on `academy-service` and `academy-worker`.
2. Restart both containers.
3. Legacy graduation and certificate PDF downloads resume immediately with zero database migration or data loss. Append-only V2 verifications and frozen snapshots remain intact for inspection.
