"""Importing this package registers every model on `Base.metadata` (used by Alembic)."""

from app.models.contact import Contact
from app.models.lead import Lead, LeadEvent, LeadScore
from app.models.mailbox import Mailbox
from app.models.message import Message
from app.models.notification import Notification
from app.models.oauth import (
    OAuthAuthorizationCode,
    OAuthAuthorizationRequest,
    OAuthClient,
    OAuthGrant,
    OAuthToken,
)
from app.models.setting import AppSetting
from app.models.user import RefreshToken, User
from app.models.webhook import InboundWebhook

__all__ = [
    "AppSetting",
    "Contact",
    "InboundWebhook",
    "Lead",
    "LeadEvent",
    "LeadScore",
    "Mailbox",
    "Message",
    "Notification",
    "OAuthAuthorizationCode",
    "OAuthAuthorizationRequest",
    "OAuthClient",
    "OAuthGrant",
    "OAuthToken",
    "RefreshToken",
    "User",
]
