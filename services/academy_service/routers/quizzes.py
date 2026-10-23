"""Academy quiz authoring, cohort-week scheduling and server-side grading."""
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.academy_service.models import Cohort, Enrollment, EnrollmentStatus, Program
from services.academy_service.models.quizzes import AcademyQuiz, AcademyQuizAssignment, AcademyQuizAttempt
from services.academy_service.schemas.quizzes import (
    QuizUpsert, QuizTemplateResponse, QuizAssignmentCreate, QuizAssignmentResponse,
    QuizAttemptSubmit, QuizAttemptResponse, LearnerQuizResponse, QuizQuestionPublic,
    QuizCompletionResponse,
)

router = APIRouter(tags=["academy-quizzes"])


def _assignment_response(a):
    return QuizAssignmentResponse(
        id=a.id, quiz_id=a.quiz_id, cohort_id=a.cohort_id, title=a.title_snapshot,
        week_number=a.week_number, passing_score=a.passing_score,
        max_attempts=a.max_attempts, required=a.required, version=a.quiz_version,
    )


@router.get("/admin/programs/{program_id}/quizzes", response_model=list[QuizTemplateResponse])
async def list_templates(program_id: uuid.UUID, _admin: AuthUser = Depends(require_admin), db: AsyncSession = Depends(get_async_db)):
    quizzes = (await db.execute(select(AcademyQuiz).where(AcademyQuiz.program_id == program_id).order_by(AcademyQuiz.created_at))).scalars().all()
    return [QuizTemplateResponse(id=q.id, program_id=q.program_id, title=q.title, description=q.description,
        questions=q.questions_json, version=q.version) for q in quizzes]


@router.post("/admin/quizzes", response_model=QuizTemplateResponse)
async def create_quiz(body: QuizUpsert, _admin: AuthUser = Depends(require_admin), db: AsyncSession = Depends(get_async_db)):
    if not await db.get(Program, body.program_id):
        raise HTTPException(404, "Program not found")
    quiz = AcademyQuiz(program_id=body.program_id, title=body.title,
        description=body.description, questions_json=[q.model_dump() for q in body.questions])
    db.add(quiz)
    await db.commit()
    await db.refresh(quiz)
    return QuizTemplateResponse(id=quiz.id, version=quiz.version, **body.model_dump())


@router.put("/admin/quizzes/{quiz_id}", response_model=QuizTemplateResponse)
async def update_quiz(quiz_id: uuid.UUID, body: QuizUpsert, _admin: AuthUser = Depends(require_admin), db: AsyncSession = Depends(get_async_db)):
    quiz = await db.get(AcademyQuiz, quiz_id)
    if not quiz: raise HTTPException(404, "Quiz not found")
    if quiz.program_id != body.program_id: raise HTTPException(400, "Cannot move quiz between programs")
    quiz.title, quiz.description = body.title, body.description
    quiz.questions_json = [q.model_dump() for q in body.questions]
    quiz.version += 1
    await db.commit()
    return QuizTemplateResponse(id=quiz.id, version=quiz.version, **body.model_dump())


@router.get("/admin/cohorts/{cohort_id}/quizzes", response_model=list[QuizAssignmentResponse])
async def list_assignments(cohort_id: uuid.UUID, _admin: AuthUser = Depends(require_admin), db: AsyncSession = Depends(get_async_db)):
    items = (await db.execute(select(AcademyQuizAssignment).where(AcademyQuizAssignment.cohort_id == cohort_id).order_by(AcademyQuizAssignment.week_number))).scalars().all()
    return [_assignment_response(a) for a in items]


@router.post("/admin/cohorts/{cohort_id}/quizzes", response_model=QuizAssignmentResponse)
async def assign_quiz(cohort_id: uuid.UUID, body: QuizAssignmentCreate, _admin: AuthUser = Depends(require_admin), db: AsyncSession = Depends(get_async_db)):
    cohort = await db.get(Cohort, cohort_id)
    quiz = await db.get(AcademyQuiz, body.quiz_id)
    if not cohort or not quiz: raise HTTPException(404, "Cohort or quiz not found")
    if quiz.program_id != cohort.program_id: raise HTTPException(400, "Quiz belongs to another program")
    program = await db.get(Program, cohort.program_id)
    if program is None or body.week_number > program.duration_weeks:
        raise HTTPException(400, "Week exceeds program duration")
    existing = await db.scalar(select(AcademyQuizAssignment).where(
        AcademyQuizAssignment.cohort_id == cohort_id, AcademyQuizAssignment.quiz_id == body.quiz_id,
        AcademyQuizAssignment.week_number == body.week_number))
    if existing: raise HTTPException(409, "Quiz already assigned for this week")
    assignment = AcademyQuizAssignment(cohort_id=cohort_id, quiz_id=quiz.id, week_number=body.week_number,
        passing_score=body.passing_score, max_attempts=body.max_attempts,
        required=body.required, quiz_version=quiz.version, title_snapshot=quiz.title,
        questions_snapshot=quiz.questions_json)
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return _assignment_response(assignment)


async def _own_enrollment(enrollment_id, user, db):
    enrollment = await db.get(Enrollment, enrollment_id)
    if not enrollment or str(enrollment.member_auth_id) != str(user.user_id):
        raise HTTPException(404, "Enrollment not found")
    if enrollment.status != EnrollmentStatus.ENROLLED or enrollment.access_suspended:
        raise HTTPException(403, "Active enrollment is required")
    return enrollment


async def _attempts(db, assignment_id, enrollment_id):
    return (await db.execute(select(AcademyQuizAttempt).where(
        AcademyQuizAttempt.assignment_id == assignment_id,
        AcademyQuizAttempt.enrollment_id == enrollment_id).order_by(AcademyQuizAttempt.attempt_number))).scalars().all()


@router.get("/enrollments/{enrollment_id}/quizzes/completion", response_model=QuizCompletionResponse)
async def quiz_completion(enrollment_id: uuid.UUID, user: AuthUser = Depends(get_current_user), db: AsyncSession = Depends(get_async_db)):
    enrollment = await _own_enrollment(enrollment_id, user, db)
    assignments = (await db.execute(select(AcademyQuizAssignment).where(
        AcademyQuizAssignment.cohort_id == enrollment.cohort_id, AcademyQuizAssignment.required.is_(True)))).scalars().all()
    completed = 0
    for a in assignments:
        if any(t.passed for t in await _attempts(db, a.id, enrollment_id)): completed += 1
    return QuizCompletionResponse(required_total=len(assignments), required_passed=completed,
        knowledge_complete=completed == len(assignments))


@router.get("/enrollments/{enrollment_id}/quizzes", response_model=list[LearnerQuizResponse])
async def learner_quizzes(enrollment_id: uuid.UUID, user: AuthUser = Depends(get_current_user), db: AsyncSession = Depends(get_async_db)):
    enrollment = await _own_enrollment(enrollment_id, user, db)
    cohort = await db.get(Cohort, enrollment.cohort_id)
    if not cohort: return []
    assignments = (await db.execute(select(AcademyQuizAssignment).where(AcademyQuizAssignment.cohort_id == cohort.id).order_by(AcademyQuizAssignment.week_number))).scalars().all()
    now = utc_now()
    result = []
    for a in assignments:
        unlock = cohort.start_date + timedelta(weeks=a.week_number - 1)
        unlocked = now >= unlock
        history = await _attempts(db, a.id, enrollment_id)
        passed = any(item.passed for item in history)
        remaining = None if a.max_attempts is None else max(0, a.max_attempts - len(history))
        latest = history[-1] if history else None
        response = _assignment_response(a).model_dump()
        result.append(LearnerQuizResponse(**response, available_at=unlock, is_unlocked=unlocked,
            passed=passed, attempts_used=len(history), attempts_remaining=remaining,
            questions=[QuizQuestionPublic.model_validate(q) for q in a.questions_snapshot] if unlocked else None,
            latest_result=QuizAttemptResponse(attempt_number=latest.attempt_number, score_percent=latest.score_percent,
                passed=latest.passed, feedback=latest.feedback_snapshot, submitted_at=latest.created_at) if latest else None))
    return result


@router.post("/enrollments/{enrollment_id}/quizzes/{assignment_id}/attempt", response_model=QuizAttemptResponse)
async def submit_attempt(enrollment_id: uuid.UUID, assignment_id: uuid.UUID, body: QuizAttemptSubmit,
    user: AuthUser = Depends(get_current_user), db: AsyncSession = Depends(get_async_db)):
    enrollment = await _own_enrollment(enrollment_id, user, db)
    assignment = await db.get(AcademyQuizAssignment, assignment_id)
    if not assignment or assignment.cohort_id != enrollment.cohort_id:
        raise HTTPException(404, "Quiz assignment not found")
    cohort = await db.get(Cohort, enrollment.cohort_id)
    if utc_now() < cohort.start_date + timedelta(weeks=assignment.week_number - 1):
        raise HTTPException(403, "Quiz is not unlocked yet")
    # Serialise attempts on the enrollment row to protect the retry limit.
    await db.execute(select(Enrollment.id).where(Enrollment.id == enrollment_id).with_for_update())
    history = await _attempts(db, assignment_id, enrollment_id)
    if any(attempt.passed for attempt in history):
        raise HTTPException(409, "This quiz has already been passed")
    if assignment.max_attempts is not None and len(history) >= assignment.max_attempts:
        raise HTTPException(403, "No attempts remaining")
    questions = assignment.questions_snapshot
    if set(body.answers) != {q["id"] for q in questions}:
        raise HTTPException(422, "Answer every question exactly once")
    feedback = []
    for q in questions:
        answers = body.answers[q["id"]]
        permitted = {opt["id"] for opt in q["options"]}
        if not answers or len(answers) != len(set(answers)) or not set(answers) <= permitted:
            raise HTTPException(422, "Invalid answer selection")
        if q["kind"] != "multiple" and len(answers) != 1:
            raise HTTPException(422, "Select one answer")
        correct = set(answers) == set(q["correct_option_ids"])
        feedback.append({"question_id": q["id"], "correct": correct,
            "correct_option_ids": q["correct_option_ids"], "explanation": q["explanation"]})
    score = round(100 * sum(f["correct"] for f in feedback) / len(feedback))
    attempt = AcademyQuizAttempt(assignment_id=assignment_id, enrollment_id=enrollment_id,
        attempt_number=len(history)+1, answers_json=body.answers, feedback_snapshot=feedback,
        score_percent=score, passed=score >= assignment.passing_score)
    db.add(attempt)
    await db.commit()
    await db.refresh(attempt)
    return QuizAttemptResponse(attempt_number=attempt.attempt_number, score_percent=score,
        passed=attempt.passed, feedback=feedback, submitted_at=attempt.created_at)


