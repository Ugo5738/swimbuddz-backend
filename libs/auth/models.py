from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AuthUser(BaseModel):
    """
    Represents an authenticated user from Supabase.
    """

    user_id: str = Field(..., alias="sub")
    email: Optional[EmailStr] = None
    role: str = "authenticated"
    app_metadata: dict[str, Any] = Field(default_factory=dict)
    user_metadata: dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @property
    def roles(self) -> list[str]:
        """
        Return roles from app_metadata.roles (preferred) or fall back to the token role.
        """
        meta_roles = self.app_metadata.get("roles")
        if isinstance(meta_roles, list):
            return [str(r) for r in meta_roles]
        if isinstance(meta_roles, str):
            return [meta_roles]
        fallback = []
        if self.role:
            fallback.append(self.role)
        return fallback

    def has_role(self, role_name: str) -> bool:
        return role_name in self.roles

    # We might want to map to our internal member_id later,
    # but for now we just trust the Supabase token.
