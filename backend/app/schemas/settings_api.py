"""Settings screens: the documents, the scoring preview and integration status."""

import uuid
from typing import Any

from pydantic import BaseModel

from app.models.enums import Priority
from app.schemas.app_settings import ScoringThresholds, ScoringWeights


class SettingsDocument(BaseModel):
    key: str
    value: dict[str, Any]


class ScoringPreviewRequest(BaseModel):
    weights: ScoringWeights
    thresholds: ScoringThresholds


class ScoringPreviewItem(BaseModel):
    lead_id: uuid.UUID
    display_name: str
    current_score: int
    current_priority: Priority | None
    new_score: int
    new_priority: Priority


class ScoringPreview(BaseModel):
    items: list[ScoringPreviewItem]
    changed: int  # leads whose priority band would change


class IntegrationStatus(BaseModel):
    openai: bool
    whatsapp: bool
    whatsapp_phone_number_id: str | None
    microsoft_sign_in: bool
    slack: bool
    teams: bool
    secrets_key: bool
    active_mailboxes: int
