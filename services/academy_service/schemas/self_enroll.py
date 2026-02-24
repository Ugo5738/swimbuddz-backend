from uuid import UUID

from pydantic import BaseModel

# from services.academy_service.models import EnrollmentStatus, PaymentStatus


class SelfEnrollRequest(BaseModel):
    cohort_id: UUID
