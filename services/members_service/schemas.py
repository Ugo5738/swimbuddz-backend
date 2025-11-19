import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, ConfigDict


class MemberBase(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str


class MemberCreate(MemberBase):
    auth_id: str


class MemberUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    is_active: Optional[bool] = None


class MemberResponse(MemberBase):
    id: uuid.UUID
    auth_id: str
    is_active: bool
    registration_complete: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PendingRegistrationCreate(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    # Add other profile fields as needed, for now just these
    
    
class PendingRegistrationResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    created_at: datetime
