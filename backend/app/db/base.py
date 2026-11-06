"""Declarative base and column helpers shared by all ORM models."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, MetaData, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names keep Alembic autogenerate diffs stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """A VARCHAR + CHECK column for a StrEnum. Unlike native Postgres enums, adding a
    value later is a constraint change, not an `ALTER TYPE` that can't run in a transaction."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


def tz_datetime() -> DateTime:
    return DateTime(timezone=True)


# Procrastinate owns its tables (created by its own migration). Alembic autogenerate and the
# migration drift test must ignore them, or they'd propose dropping the job queue.
EXTERNAL_TABLE_PREFIX = "procrastinate_"


def include_in_migrations(name: str | None, type_: str, _parent_names: object) -> bool:
    return not (type_ == "table" and name is not None and name.startswith(EXTERNAL_TABLE_PREFIX))
