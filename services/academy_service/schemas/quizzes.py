"""Question validation and API contracts for automatically graded Academy quizzes."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class QuizOption(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=500)


class QuizQuestion(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=1000)
    kind: Literal["single", "multiple", "true_false"] = "single"
    options: list[QuizOption] = Field(min_length=2, max_length=8)
    correct_option_ids: list[str] = Field(min_length=1)
    explanation: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_answers(self):
        ids = [o.id for o in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("Option IDs must be unique within a question")
        if len(set(self.correct_option_ids)) != len(self.correct_option_ids):
            raise ValueError("Correct answer IDs cannot repeat")
        if not set(self.correct_option_ids) <= set(ids):
            raise ValueError("Correct answers must reference existing options")
        if self.kind != "multiple" and len(self.correct_option_ids) != 1:
            raise ValueError("Single/true-false questions require exactly one correct option")
        if self.kind == "true_false" and len(ids) != 2:
            raise ValueError("True/false questions require exactly two options")
        return self


class QuizUpsert(BaseModel):
    program_id: UUID
    title: str = Field(min_length=3, max_length=200)
    description: str | None = None
    questions: list[QuizQuestion] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def unique_question_ids(self):
        ids = [q.id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("Question IDs must be unique")
        return self


class QuizTemplateResponse(QuizUpsert):
    id: UUID
    version: int


class QuizAssignmentCreate(BaseModel):
    quiz_id: UUID
    week_number: int = Field(ge=1, le=52)
    passing_score: int = Field(default=80, ge=1, le=100)
    max_attempts: int | None = Field(default=3, ge=1, le=20)
    required: bool = True


class QuizAssignmentResponse(BaseModel):
    id: UUID
    quiz_id: UUID
    cohort_id: UUID
    title: str
    week_number: int
    passing_score: int
    max_attempts: int | None
    required: bool
    version: int


class QuizQuestionPublic(BaseModel):
    id: str
    text: str
    kind: Literal["single", "multiple", "true_false"]
    options: list[QuizOption]


class QuizAttemptSubmit(BaseModel):
    answers: dict[str, list[str]]


class QuizFeedback(BaseModel):
    question_id: str
    correct: bool
    correct_option_ids: list[str]
    explanation: str


class QuizAttemptResponse(BaseModel):
    attempt_number: int
    score_percent: int
    passed: bool
    feedback: list[QuizFeedback]
    submitted_at: datetime


class LearnerQuizResponse(QuizAssignmentResponse):
    available_at: datetime
    is_unlocked: bool
    passed: bool
    attempts_used: int
    attempts_remaining: int | None
    questions: list[QuizQuestionPublic] | None = None
    latest_result: QuizAttemptResponse | None = None


class QuizCompletionResponse(BaseModel):
    required_total: int
    required_passed: int
    knowledge_complete: bool
