"""add changeset + stage_tests to conversations

The chat's staging changeset (ordered Action list) and saved acceptance tests —
the only durable artifacts the agent authors. The staging env is derived from
the changeset by replay; nothing else about the stage persists.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "e7ceef6f9813"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("changeset", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "conversations",
        sa.Column("stage_tests", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("conversations", "stage_tests")
    op.drop_column("conversations", "changeset")
