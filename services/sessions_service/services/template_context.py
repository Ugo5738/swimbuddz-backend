"""Validate template context before any generated Session is written."""

import uuid

import httpx
from fastapi import HTTPException

from libs.common.config import get_settings
from libs.common.service_client import internal_get
from services.sessions_service.models import SessionType


def validate_template_context(
    session_type: SessionType, cohort_id: uuid.UUID | None, cohort_fee_mode: str
) -> None:
    if session_type == SessionType.COHORT_CLASS:
        if cohort_id is None:
            raise ValueError(
                "Edit this Academy template and choose its cohort before generating classes."
            )
    elif cohort_id is not None or cohort_fee_mode != "included":
        raise ValueError(
            "Only Academy class templates can set a cohort or paid-extra billing."
        )
    if session_type == SessionType.EVENT:
        raise ValueError(
            "Create Event sessions from a specific Event so its event_id is preserved."
        )


async def require_template_cohort(cohort_id: uuid.UUID | None) -> None:
    if cohort_id is None:
        return
    try:
        response = await internal_get(
            service_url=get_settings().ACADEMY_SERVICE_URL,
            path=f"/academy/cohorts/{cohort_id}",
            calling_service="sessions",
        )
        if response.status_code == 404:
            raise HTTPException(
                422, "The template's cohort no longer exists. Choose a valid cohort."
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(
                422, "The template's cohort no longer exists. Choose a valid cohort."
            ) from exc
        raise HTTPException(
            503, "Could not validate the cohort. Please try again."
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            503, "Could not validate the cohort. Please try again."
        ) from exc
