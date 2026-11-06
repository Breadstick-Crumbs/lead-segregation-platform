from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.user import MAX_PASSWORD_LENGTH, UserRead


class LoginRequest(BaseModel):
    # Not EmailStr: login only looks the address up, and strict validation (which rejects
    # e.g. `.test` domains) belongs on account creation.
    email: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, max_length=320)]
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class AuthProviders(BaseModel):
    password: bool
    microsoft: bool


class TokenResponse(BaseModel):
    """The access token goes in the body; the refresh token is an httpOnly cookie."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  (OAuth2 token type name)
    expires_in: int = Field(description="Access token lifetime in seconds")
    user: UserRead
