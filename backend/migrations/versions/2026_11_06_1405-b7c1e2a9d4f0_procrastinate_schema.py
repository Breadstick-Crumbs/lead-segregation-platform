"""procrastinate job queue schema (3.10.0)

The SQL is vendored from procrastinate 3.10.0 (migrations/sql/), not read from the installed
package, so this migration stays reproducible. When upgrading procrastinate, add a new
migration that applies its bundled migration files between the two versions.

Revision ID: b7c1e2a9d4f0
Revises: 3d64a0d82036
Create Date: 2026-11-06 14:05:00

"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "b7c1e2a9d4f0"
down_revision: str | Sequence[str] | None = "3d64a0d82036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_SQL = Path(__file__).parents[1] / "sql" / "procrastinate_3.10.0_schema.sql"


def upgrade() -> None:
    op.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    # Tables first (CASCADE takes their triggers and indexes), then functions and types.
    op.execute(
        r"""
        DO $$
        DECLARE obj record;
        BEGIN
          FOR obj IN SELECT tablename FROM pg_tables
                     WHERE schemaname = 'public' AND tablename LIKE 'procrastinate\_%' LOOP
            EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', obj.tablename);
          END LOOP;
          FOR obj IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
                     JOIN pg_namespace n ON n.oid = p.pronamespace
                     WHERE n.nspname = 'public' AND p.proname LIKE 'procrastinate\_%' LOOP
            EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', obj.sig);
          END LOOP;
          FOR obj IN SELECT t.typname FROM pg_type t
                     JOIN pg_namespace n ON n.oid = t.typnamespace
                     WHERE n.nspname = 'public' AND t.typname LIKE 'procrastinate\_%'
                       AND t.typtype IN ('e', 'c') LOOP
            EXECUTE format('DROP TYPE IF EXISTS %I CASCADE', obj.typname);
          END LOOP;
        END $$;
        """
    )
