"""Idempotent seed: default settings, the admin account and demo sales reps.

    uv run python -m app.scripts.seed

Needs SEED_ADMIN_PASSWORD; demo reps are created only when SEED_DEMO_PASSWORD is set.
Existing users and settings are never overwritten.
"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.runtime import use_selector_event_loop_on_windows
from app.core.security import hash_password
from app.db.session import SessionFactory, engine
from app.models import AppSetting, User
from app.models.enums import Role
from app.repositories.users import UserRepository
from app.schemas.app_settings import DEFAULT_SETTINGS

logger = get_logger(__name__)

DEMO_REPS = (
    ("layla.hassan@tawasol.test", "Layla Hassan"),
    ("omar.khalid@tawasol.test", "Omar Khalid"),
    ("priya.nair@tawasol.test", "Priya Nair"),
)


async def seed_settings(session: AsyncSession) -> None:
    for model in DEFAULT_SETTINGS:
        if await session.get(AppSetting, model.KEY) is None:
            session.add(AppSetting(key=model.KEY, value=model.model_dump(mode="json")))
            logger.info("seeded_setting", key=model.KEY)


async def ensure_user(
    session: AsyncSession, *, email: str, full_name: str, role: Role, password: str
) -> None:
    users = UserRepository(session)
    if await users.get_by_email(email) is not None:
        return
    users.add(
        User(
            email=email.lower(),
            full_name=full_name,
            role=role,
            password_hash=hash_password(password),
        )
    )
    logger.info("seeded_user", email=email, role=role)


async def seed(settings: Settings) -> None:
    if settings.seed_admin_password is None:
        raise SystemExit("SEED_ADMIN_PASSWORD is required")
    async with SessionFactory() as session:
        await seed_settings(session)
        await ensure_user(
            session,
            email=settings.seed_admin_email,
            full_name="Platform Admin",
            role=Role.ADMIN,
            password=settings.seed_admin_password.get_secret_value(),
        )
        if settings.seed_demo_password is not None:
            for email, name in DEMO_REPS:
                await ensure_user(
                    session,
                    email=email,
                    full_name=name,
                    role=Role.SALES_REP,
                    password=settings.seed_demo_password.get_secret_value(),
                )
        await session.commit()
    await engine.dispose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.is_production)
    use_selector_event_loop_on_windows()
    asyncio.run(seed(settings))


if __name__ == "__main__":
    main()
