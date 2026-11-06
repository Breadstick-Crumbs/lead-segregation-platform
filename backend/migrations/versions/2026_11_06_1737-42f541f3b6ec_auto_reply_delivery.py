"""Auto-reply delivery tracking: send_started_at (at-most-once sends) and delivery_error.

Revision ID: 42f541f3b6ec
Revises: 4135f3127c4d
Create Date: 2026-11-06 17:37:41.590043

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "42f541f3b6ec"
down_revision: str | Sequence[str] | None = "4135f3127c4d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages", sa.Column("send_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("messages", sa.Column("delivery_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "delivery_error")
    op.drop_column("messages", "send_started_at")
