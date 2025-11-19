from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from pydantic import ValidationError

from libs.common.config import get_settings
from libs.auth.models import AuthUser

settings = get_settings()
security = HTTPBearer()


async def get_current_user(
    token: Annotated[HTTPAuthorizationCredentials, Depends(security)]
) -> AuthUser:
    """
    Validate Supabase JWT and return the authenticated user.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # Decode the token using the Supabase JWT secret
        # Supabase uses HS256 by default
        payload = jwt.decode(
            token.credentials,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated", # Optional: validate audience if needed
            options={"verify_aud": False} # Supabase tokens might vary in aud, disabling for MVP simplicity
        )
        
        user = AuthUser(**payload)
        return user
        
    except (JWTError, ValidationError):
        raise credentials_exception


async def require_admin(
    current_user: Annotated[AuthUser, Depends(get_current_user)]
) -> AuthUser:
    """
    Ensure the user has the 'admin' role (or specific claim).
    For now, we might check a custom claim or just the 'service_role' if using that for admin.
    Or we can check if the email is in a hardcoded admin list for MVP.
    
    Let's assume for now we check a custom 'app_metadata' claim or similar.
    But Supabase 'role' claim is usually 'authenticated'.
    
    TODO: Refine admin check based on actual Supabase RBAC or 'admin' table.
    For this bootstrap, let's allow if role is 'service_role' OR if we add a temporary check.
    """
    # Placeholder: strictly enforce 'service_role' or a specific admin flag
    if current_user.role != "service_role":
         # Real implementation would check DB or custom claims
         raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )
    return current_user
