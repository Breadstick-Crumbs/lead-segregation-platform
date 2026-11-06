"""Index for listing a user's notifications newest first.

Revision ID: 284cf4237b3b
Revises: 42f541f3b6ec
Create Date: 2026-11-06 18:19:30.818091

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "284cf4237b3b"
down_revision: str | Sequence[str] | None = "42f541f3b6ec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_notifications_user_id_created_at",
        "notifications",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_user_id_created_at", table_name="notifications")
