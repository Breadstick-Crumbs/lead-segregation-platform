"""AI pipeline: pgvector embeddings on leads, duplicate link, round-robin assignment.

Revision ID: 4135f3127c4d
Revises: b7c1e2a9d4f0
Create Date: 2026-11-06 16:38:56.590060

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "4135f3127c4d"
down_revision: str | Sequence[str] | None = "b7c1e2a9d4f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("leads", sa.Column("embedding", Vector(1536), nullable=True))
    op.add_column("leads", sa.Column("possible_duplicate_of_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_leads_embedding_hnsw",
        "leads",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_foreign_key(
        op.f("fk_leads_possible_duplicate_of_id_leads"),
        "leads",
        "leads",
        ["possible_duplicate_of_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("users", sa.Column("last_assigned_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_assigned_at")
    op.drop_constraint(op.f("fk_leads_possible_duplicate_of_id_leads"), "leads", type_="foreignkey")
    op.drop_index("ix_leads_embedding_hnsw", table_name="leads")
    op.drop_column("leads", "possible_duplicate_of_id")
    op.drop_column("leads", "embedding")
    op.execute("DROP EXTENSION IF EXISTS vector")
