from typing import Annotated

from fastapi import APIRouter, Cookie, Response, status

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import PasswordChange, UserRead
from app.services.auth import AuthService, IssuedTokens
from app.services.users import UserService

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
# Scoped to the auth endpoints so the cookie isn't sent with every API call.
REFRESH_COOKIE_PATH = "/api/v1/auth"

RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


def _token_response(response: Response, tokens: IssuedTokens, settings: Settings) -> TokenResponse:
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        expires=tokens.refresh_expires_at,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    return TokenResponse(
        access_token=tokens.access_token,
        expires_in=tokens.access_expires_in,
        user=UserRead.model_validate(tokens.user),
    )


@router.post("/login")
async def login(
    body: LoginRequest, response: Response, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    tokens = await AuthService(session, settings).login(body.email, body.password)
    return _token_response(response, tokens, settings)


@router.post("/refresh")
async def refresh(
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> TokenResponse:
    """Exchange the refresh cookie for a new access token; the refresh token rotates."""
    if not refresh_token:
        raise AuthenticationError("Missing refresh token")
    tokens = await AuthService(session, settings).refresh(refresh_token)
    return _token_response(response, tokens, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> None:
    if refresh_token:
        await AuthService(session, settings).logout(refresh_token)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


@router.get("/me")
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> None:
    """Change your password. Other sessions are signed out; this one stays signed in."""
    current_family = (
        await AuthService(session, settings).family_of(refresh_token) if refresh_token else None
    )
    await UserService(session).change_own_password(user, body, keep_session_family=current_family)
