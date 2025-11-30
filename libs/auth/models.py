from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class AuthUser(BaseModel):
    """
    Represents an authenticated user from Supabase.
    """

    user_id: str = Field(..., alias="sub")
    email: Optional[EmailStr] = None
    role: str = "authenticated"
    # Add other claims as needed

    # We might want to map to our internal member_id later,
    # but for now we just trust the Supabase token.
