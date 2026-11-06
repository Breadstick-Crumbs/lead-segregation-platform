"""Read admin-editable settings, falling back to the documented defaults."""

from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting
from app.schemas.app_settings import SettingsModel

T = TypeVar("T", bound=SettingsModel)


async def load_settings(session: AsyncSession, model: type[T]) -> T:
    row = await session.get(AppSetting, model.KEY)
    return model.model_validate(row.value) if row is not None else model()
