"""Schemas for service-owned article and image generation."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


ContentTier = Literal["community", "club", "academy"]


class ContentDraftRequest(BaseModel):
    title: str = Field(min_length=4, max_length=180)
    brief: Optional[str] = Field(default=None, max_length=2000)
    category: str = Field(default="swimming_tips", min_length=2, max_length=80)
    tier_access: ContentTier = "community"


class ContentDraftSection(BaseModel):
    heading: str = Field(default="", max_length=100)
    paragraphs: list[str] = Field(default_factory=list, max_length=3)
    bullets: list[str] = Field(default_factory=list, max_length=5)


class ContentDraftPayload(BaseModel):
    summary: str = Field(min_length=20, max_length=320)
    sections: list[ContentDraftSection] = Field(min_length=1, max_length=7)
    closing: str = Field(default="", max_length=900)
    featured_image_prompt: str = Field(min_length=20, max_length=1200)


class ContentDraftResponse(ContentDraftPayload):
    ai_request_id: str
    model_used: str
    context_version: str


class ContentImageRequest(BaseModel):
    prompt: str = Field(min_length=20, max_length=1600)
    title: str = Field(min_length=1, max_length=180)


class ContentImageResponse(BaseModel):
    image_url: str
    ai_request_id: str
    model_used: str
