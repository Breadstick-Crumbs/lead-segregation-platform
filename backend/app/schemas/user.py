import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.models.enums import Role

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128

Password = Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
NormalizedEmail = Annotated[EmailStr, AfterValidator(str.lower)]


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    has_password: bool
    microsoft_linked: bool
    last_login_at: datetime | None
    created_at: datetime


class UserSummary(BaseModel):
    """What any signed-in user may see about a colleague (e.g. to pick a lead owner)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    role: Role


class UserCreate(BaseModel):
    email: NormalizedEmail
    full_name: FullName
    role: Role
    # Omit for a Microsoft-only account: the user signs in with their work account.
    password: Password | None = None


class UserUpdate(BaseModel):
    """Admin edit. Omitted fields are left unchanged; `password` resets it."""

    full_name: FullName | None = None
    role: Role | None = None
    is_active: bool | None = None
    password: Password | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    new_password: Password
