from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from pydantic import BaseModel
from services.gateway_service.app import clients

router = APIRouter(tags=["admin-cleanup"])
settings = get_settings()


class CleanupMode(str, Enum):
    SOFT = "soft"
    HARD = "hard"


class CleanupRequest(BaseModel):
    mode: CleanupMode = CleanupMode.HARD


class CleanupResponse(BaseModel):
    mode: CleanupMode
    member_id: Optional[uuid.UUID]
    auth_id: Optional[str]
    results: Dict[str, Any]
    errors: List[Dict[str, str]]


class CleanupByEmailRequest(BaseModel):
    email: str
    mode: CleanupMode = CleanupMode.HARD


def _parse_service_json(response: httpx.Response | None) -> Dict[str, Any] | None:
    """
    Normalize httpx responses into JSON payloads while handling empty bodies.
    """
    if response is None:
        return None
    if response.status_code == status.HTTP_204_NO_CONTENT or not response.content:
        return {"deleted": 0}
    try:
        return response.json()
    except ValueError:
        return {"raw_response": response.text or ""}


def _service_role_token() -> str:
    now = int(utc_now().timestamp())
    payload = {
        "sub": "service:gateway",
        "email": settings.ADMIN_EMAIL,
        "role": "service_role",
        "iat": now,
        "exp": now + 300,  # 5 minutes to handle long cleanup operations
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


@router.post("/admin/cleanup/members/by-email", response_model=CleanupResponse)
async def cleanup_member_by_email(
    payload: CleanupByEmailRequest,
    current_user: AuthUser = Depends(require_admin),
):
    """
    Coordinated cleanup for a member by email (Admin only).
    """
    email = payload.email.strip().lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is required.",
        )

    token = _service_role_token()
    headers = {"Authorization": f"Bearer {token}"}
    results: Dict[str, Any] = {}
    errors: List[Dict[str, str]] = []

    member = None
    try:
        member_response = await clients.members_client.get(
            f"/admin/members/by-email/{quote(email)}", headers=headers
        )
        if isinstance(member_response, httpx.Response) and (
            member_response.is_error or member_response.status_code >= 400
        ):
            member_response.raise_for_status()
        member = _parse_service_json(member_response)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != status.HTTP_404_NOT_FOUND:
            errors.append({"service": "members", "error": str(exc)})
    except Exception as exc:
        errors.append({"service": "members", "error": str(exc)})

    if member and member.get("id"):
        cleanup_payload = CleanupRequest(mode=payload.mode)
        return await cleanup_member(
            member_id=uuid.UUID(member["id"]),
            payload=cleanup_payload,
            current_user=current_user,
        )

    async def run_call_allow_404(name: str, coro):
        try:
            result = await coro
            if isinstance(result, httpx.Response) and (
                result.is_error or result.status_code >= 400
            ):
                result.raise_for_status()
            parsed = _parse_service_json(result)
            results[name] = parsed if parsed is not None else {"deleted": 0}
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == status.HTTP_404_NOT_FOUND:
                results[name] = {"deleted": 0}
                return
            errors.append({"service": name, "error": str(exc)})
            results[name] = {"error": str(exc)}
        except Exception as exc:
            errors.append({"service": name, "error": str(exc)})
            results[name] = {"error": str(exc)}

    await run_call_allow_404(
        "pending_registrations",
        clients.members_client.delete(
            f"/pending-registrations/by-email/{quote(email)}", headers=headers
        ),
    )

    return CleanupResponse(
        mode=payload.mode,
        member_id=None,
        auth_id=None,
        results=results,
        errors=errors,
    )


@router.post("/admin/cleanup/members/{member_id}", response_model=CleanupResponse)
async def cleanup_member(
    member_id: uuid.UUID,
    payload: CleanupRequest,
    current_user: AuthUser = Depends(require_admin),
):
    """
    Coordinated cleanup for a member across services (Admin only).
    """
    token = _service_role_token()
    headers = {"Authorization": f"Bearer {token}"}
    results: Dict[str, Any] = {}
    errors: List[Dict[str, str]] = []

    try:
        member_response = await clients.members_client.get(
            f"/members/{member_id}", headers=headers
        )
        if isinstance(member_response, httpx.Response) and (
            member_response.is_error or member_response.status_code >= 400
        ):
            member_response.raise_for_status()
        member = _parse_service_json(member_response) or {}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Member not found or members service unavailable: {exc}",
        )

    auth_id = member.get("auth_id")

    async def run_call(name: str, coro):
        try:
            result = await coro
            if isinstance(result, httpx.Response) and (
                result.is_error or result.status_code >= 400
            ):
                result.raise_for_status()
            parsed = _parse_service_json(result)
            results[name] = parsed if parsed is not None else {"deleted": 0}
        except httpx.HTTPStatusError as exc:
            detail = str(exc)
            try:
                body = exc.response.json()
                if isinstance(body, dict) and body.get("detail"):
                    detail = str(body["detail"])
            except Exception:
                pass
            errors.append(
                {
                    "service": name,
                    "error": detail,
                    "status_code": str(exc.response.status_code),
                }
            )
            results[name] = {"error": detail}
        except Exception as exc:
            errors.append({"service": name, "error": str(exc)})
            results[name] = {"error": str(exc)}

    def raise_if_members_failed():
        member_err = next((e for e in errors if e.get("service") == "members"), None)
        if member_err:
            code = (
                status.HTTP_409_CONFLICT
                if member_err.get("status_code") == "409"
                else status.HTTP_502_BAD_GATEWAY
            )
            raise HTTPException(
                status_code=code,
                detail=member_err.get("error")
                or "Failed to update/delete member record",
            )

    if payload.mode == CleanupMode.SOFT:
        await run_call(
            "members",
            clients.members_client.patch(
                f"/members/{member_id}",
                json={"is_active": False},
                headers=headers,
            ),
        )
        raise_if_members_failed()
        return CleanupResponse(
            mode=payload.mode,
            member_id=member_id,
            auth_id=auth_id,
            results=results,
            errors=errors,
        )

    if not auth_id:
        raise HTTPException(
            status_code=409,
            detail="Hard-delete requires an auth_id; use Soft Delete instead.",
        )

    # Validate the two known hard-delete blockers *before* deleting any service
    # data. A 409 here must not leave payment or attendance records half-purged.
    for name, client, url in (
        (
            "members",
            clients.members_client,
            f"/members/{member_id}/hard-delete-preflight",
        ),
        (
            "payments",
            clients.payments_client,
            f"/payments/admin/members/by-auth/{auth_id}/hard-delete-preflight",
        ),
    ):
        try:
            check = await client.get(url, headers=headers)
            if isinstance(check, httpx.Response) and check.status_code >= 400:
                check.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = str(exc)
            try:
                detail = str(exc.response.json().get("detail") or detail)
            except Exception:
                pass
            raise HTTPException(
                status_code=409 if exc.response.status_code == 409 else 502,
                detail=f"{name} hard-delete preflight failed: {detail}",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"{name} preflight unavailable: {exc}"
            ) from exc

    if auth_id:
        await run_call(
            "payments",
            clients.payments_client.delete(
                f"/payments/admin/members/by-auth/{auth_id}", headers=headers
            ),
        )
    else:
        errors.append({"service": "payments", "error": "Missing member auth_id"})
        results["payments"] = {"error": "Missing member auth_id"}
    if errors:
        raise HTTPException(
            status_code=502,
            detail=f"Hard-delete stopped after a service failure: {errors}",
        )
    await run_call(
        "attendance",
        clients.attendance_client.delete(
            f"/attendance/admin/members/{member_id}", headers=headers
        ),
    )
    if errors:
        raise HTTPException(
            status_code=502,
            detail=f"Hard-delete stopped after a service failure: {errors}",
        )
    await run_call(
        "academy",
        clients.academy_client.delete(
            f"/academy/admin/members/{member_id}", headers=headers
        ),
    )
    if errors:
        raise HTTPException(
            status_code=502,
            detail=f"Hard-delete stopped after a service failure: {errors}",
        )
    await run_call(
        "communications",
        clients.communications_client.delete(
            f"/admin/members/{member_id}", headers=headers
        ),
    )
    if errors:
        raise HTTPException(
            status_code=502,
            detail=f"Hard-delete stopped after a service failure: {errors}",
        )
    await run_call(
        "events",
        clients.events_client.delete(
            f"/events/admin/members/{member_id}", headers=headers
        ),
    )
    if errors:
        raise HTTPException(
            status_code=502,
            detail=f"Hard-delete stopped after a service failure: {errors}",
        )
    await run_call(
        "transport",
        clients.transport_client.delete(
            f"/transport/admin/members/{member_id}", headers=headers
        ),
    )
    if errors:
        raise HTTPException(
            status_code=502,
            detail=f"Hard-delete stopped after a service failure: {errors}",
        )
    await run_call(
        "media",
        clients.media_client.delete(
            f"/media/admin/members/{member_id}", headers=headers
        ),
    )
    if errors:
        raise HTTPException(
            status_code=502,
            detail=f"Hard-delete stopped after a service failure: {errors}",
        )
    await run_call(
        "members",
        clients.members_client.delete(f"/members/{member_id}", headers=headers),
    )
    raise_if_members_failed()

    return CleanupResponse(
        mode=payload.mode,
        member_id=member_id,
        auth_id=auth_id,
        results=results,
        errors=errors,
    )
