import uuid
from datetime import datetime

from pydantic import BaseModel


class ConsentRequestRead(BaseModel):
    """What the consent page shows before the user approves an MCP client."""

    id: uuid.UUID
    client_name: str
    # Where the user is sent back to: the one thing about a self-registered client that
    # can't be faked, so the page shows it.
    redirect_host: str
    expires_at: datetime


class ConsentDecision(BaseModel):
    redirect_url: str


class ConnectedAppRead(BaseModel):
    id: uuid.UUID
    client_name: str
    redirect_host: str
    created_at: datetime
    last_used_at: datetime | None
