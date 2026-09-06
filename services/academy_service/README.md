# Academy Service

## Editing cohort commercial settings

Admin can edit the published price override, annual Membership policy, and
installment defaults after creation. Both self-service and Admin enrollment
creation freeze `Enrollment.price_snapshot_amount` (kobo), `currency_snapshot`,
and `membership_policy_snapshot`, including unpaid enrollments and program-only
requests. The policy resolves the cohort override, then program policy, then
`open`. Later cohort/program edits never change those stored terms.

Full-payment quotes and newly generated installment schedules use the tuition
snapshot. Both full and installment quotes use the stored Membership policy, so
an `included` enrollment cannot acquire a separate annual Membership fee because
an Admin later changes the cohort to `active_required`. Existing installment rows
are preserved when defaults change or installments are disabled.

Migration `f6c8e0a2b413` adds the nullable Membership policy snapshot; apply it
before deploying the new Academy code. Existing rows are intentionally not
backfilled with a guessed historical policy. Only missing legacy snapshots fall
back to current cohort/program terms. The existing store variant-cost migration
is separate; neither migration is executed by these code changes.

Waitlisted learners cannot pay until admitted. A checkout lookup requesting
installments cannot opt a waitlisted enrollment into a plan or create its
schedule. The payments quote rejects waitlisted enrollments with HTTP 409.

SwimBuddz Academy Service manages structured learning programs, cohorts, and student progress.

## Features

- Academy program management
- Cohort scheduling and enrollment
- Student progress tracking
- Milestone completion
- Coach assignment
- Skill assessments
- Graduation tracking

## API Endpoints

### Programs
- `GET /academy/programs` - List programs
- `GET /academy/programs/{id}` - Get program details
- `POST /academy/programs` - Create program
- `PATCH /academy/programs/{id}` - Update program
- `DELETE /academy/programs/{id}` - Delete program

### Cohorts
- `GET /academy/cohorts` - List cohorts
- `GET /academy/cohorts/{id}` - Get cohort details (with students)
- `POST /academy/cohorts` - Create cohort
- `PATCH /academy/cohorts/{id}` - Update cohort
- `DELETE /academy/cohorts/{id}` - Delete cohort

### Enrollments
- `GET /academy/enrollments` - List enrollments
- `POST /academy/enrollments` - Enroll student
- `PATCH /academy/enrollments/{id}` - Update enrollment
- `DELETE /academy/enrollments/{id}` - Withdraw student

### Progress Tracking
- `GET /academy/enrollments/{id}/progress` - Get student progress
- `POST /academy/enrollments/{id}/milestones` - Update milestone
- `GET /academy/cohorts/{id}/progress-summary` - Cohort progress overview

## Database Tables

- `academy_programs` - Learning program definitions
- `academy_cohorts` - Scheduled cohort instances
- `cohort_enrollments` - Student enrollments
- `program_milestones` - Learning milestones per program
- `student_milestone_progress` - Individual progress tracking

## Key Features

### Learning Pathways
- **Beginner**: Water confidence, basic strokes
- **Intermediate**: Technique refinement, endurance
- **Advanced**: Competitive training, open water prep

### Cohort Management
- Fixed-duration programs (8-12 weeks typical)
- Maximum capacity limits
- Coach assignment
- Session scheduling
- Payment integration

### Progress Tracking
- Milestone-based progression
- Coach notes and feedback
- Skill assessment updates
- Graduation criteria

## Environment Variables

See `.env.dev` for required configuration:
- `DATABASE_URL` - PostgreSQL connection string

## Running

```bash
# Via Docker
docker-compose up academy-service

# Standalone (dev)
cd services/academy_service
uvicorn app.main:app --host 0.0.0.0 --port 8006 --reload
```

## Port

Default: `8006`
