"""Read and save admin-editable settings, falling back to the documented defaults."""

from typing import TypeVar

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import AppSetting
from app.schemas.app_settings import DEFAULT_SETTINGS, SettingsModel

T = TypeVar("T", bound=SettingsModel)

SETTINGS_MODELS: dict[str, type[SettingsModel]] = {type(m).KEY: type(m) for m in DEFAULT_SETTINGS}


async def load_settings(session: AsyncSession, model: type[T]) -> T:
    row = await session.get(AppSetting, model.KEY)
    return model.model_validate(row.value) if row is not None else model()


async def load_all(session: AsyncSession) -> dict[str, SettingsModel]:
    return {key: await load_settings(session, model) for key, model in SETTINGS_MODELS.items()}


def settings_model(key: str) -> type[SettingsModel]:
    model = SETTINGS_MODELS.get(key)
    if model is None:
        raise NotFoundError(f"No settings named {key!r}")
    return model


async def save_settings(session: AsyncSession, value: SettingsModel) -> None:
    """Upsert one validated settings document. Commits."""
    data = value.model_dump(mode="json")
    await session.execute(
        insert(AppSetting)
        .values(key=value.KEY, value=data)
        .on_conflict_do_update(index_elements=[AppSetting.key], set_={"value": data})
    )
    await session.commit()
