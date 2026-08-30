"""drop catalogs.version and flex_modules.version

The version counters were never load-bearing: catalogs.version was never
incremented (always 1), and flex_modules.version was bumped on every edit but
never read/enforced (no optimistic-concurrency check, no revision history since
the single row's source_text is overwritten in place). Removed as dead weight.

Revision ID: a1b2c3d4e5f6
Revises: 0d8003eda947
Create Date: 2026-08-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '0d8003eda947'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('catalogs', 'version')
    op.drop_column('flex_modules', 'version')


def downgrade() -> None:
    op.add_column(
        'flex_modules',
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    )
    op.add_column(
        'catalogs',
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    )
