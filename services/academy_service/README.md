# Academy Service

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
