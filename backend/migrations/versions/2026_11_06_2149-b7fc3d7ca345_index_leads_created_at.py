"""Index leads by created_at, for reporting periods.

Revision ID: b7fc3d7ca345
Revises: 284cf4237b3b
Create Date: 2026-11-06 21:49:08.432553

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7fc3d7ca345"
down_revision: str | Sequence[str] | None = "284cf4237b3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_leads_created_at", "leads", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_leads_created_at", table_name="leads")
