"""Optional merchandise links; never change a published Club's commercial terms."""

import uuid

from pydantic import BaseModel, ConfigDict


class AttachClubExperienceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offering_id: uuid.UUID
