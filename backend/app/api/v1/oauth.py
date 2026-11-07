"""The dashboard side of MCP sign-in: the consent page's request and decision, and the
user's connected apps. The OAuth protocol endpoints themselves live in `app.mcp_server`."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.schemas.oauth import ConnectedAppRead, ConsentDecision, ConsentRequestRead
from app.services.oauth import OAuthService

router = APIRouter(prefix="/oauth", tags=["oauth"])


@router.get("/requests/{request_id}")
async def get_consent_request(
    request_id: uuid.UUID, _: CurrentUser, session: SessionDep, settings: SettingsDep
) -> ConsentRequestRead:
    return await OAuthService(session, settings).describe(request_id)


@router.post("/requests/{request_id}/approve")
async def approve_consent_request(
    request_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> ConsentDecision:
    """Let the app act as the signed-in user. Send the browser to `redirect_url`."""
    url = await OAuthService(session, settings).approve(request_id, user)
    return ConsentDecision(redirect_url=url)


@router.post("/requests/{request_id}/deny")
async def deny_consent_request(
    request_id: uuid.UUID, _: CurrentUser, session: SessionDep, settings: SettingsDep
) -> ConsentDecision:
    url = await OAuthService(session, settings).deny(request_id)
    return ConsentDecision(redirect_url=url)


@router.get("/connected-apps")
async def list_connected_apps(
    user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> list[ConnectedAppRead]:
    return await OAuthService(session, settings).connected_apps(user)


@router.delete("/connected-apps/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_app(
    grant_id: uuid.UUID, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> None:
    """Revoke the app's access at once: its tokens stop working on the next call."""
    await OAuthService(session, settings).disconnect(user, grant_id)
