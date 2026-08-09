"""object_factories column_spec to JSON list

Revision ID: 16b8b2cf731f
Revises: f10483ccd160
Create Date: 2026-06-19 21:00:31.250257

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "16b8b2cf731f"
down_revision: Union[str, None] = "f10483ccd160"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres can't convert TEXT → JSON without a USING expression. Existing
    # rows we wrote as empty strings become empty arrays; any non-empty
    # legacy text becomes a one-element array so no data is lost.
    op.execute(
        """
        ALTER TABLE object_factories
            ALTER COLUMN column_spec
            DROP DEFAULT
        """
    )
    op.execute(
        """
        ALTER TABLE object_factories
            ALTER COLUMN column_spec
            TYPE json
            USING (
                CASE
                    WHEN column_spec IS NULL OR column_spec = '' THEN '[]'::json
                    ELSE json_build_array(column_spec)
                END
            )
        """
    )
    op.execute(
        """
        ALTER TABLE object_factories
            ALTER COLUMN column_spec
            SET DEFAULT '[]'::json
        """
    )


def downgrade() -> None:
    # Reverse: JSON → TEXT, joining list entries with ", " so the textual
    # form is at least human-readable.
    op.execute(
        """
        ALTER TABLE object_factories
            ALTER COLUMN column_spec
            DROP DEFAULT
        """
    )
    op.execute(
        """
        ALTER TABLE object_factories
            ALTER COLUMN column_spec
            TYPE text
            USING (
                CASE
                    WHEN json_typeof(column_spec) = 'array' THEN (
                        SELECT COALESCE(string_agg(value::text, ', '), '')
                        FROM json_array_elements_text(column_spec)
                    )
                    ELSE ''
                END
            )
        """
    )
    op.execute(
        """
        ALTER TABLE object_factories
            ALTER COLUMN column_spec
            SET DEFAULT ''
        """
    )
