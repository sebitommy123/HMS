"""add env axis: environments table + env_id overlay columns

Adds the staging-environment overlay:
  * a new ``environments`` table (a row exists only for a live/retired staging
    overlay; production rows carry ``env_id = NULL``),
  * ``env_id`` / ``deleted`` (and ``logical_name`` on catalogs, ``base_id`` on
    types+factories) on the mutable tables,
  * env-scoped unique indexes with ``NULLS NOT DISTINCT`` so prod rows
    (env_id NULL) still collide, replacing the old single-column/constraint
    uniqueness.

Catalogs keep their physical name as the PK; ``logical_name`` is the user-facing
name (== name for prod). Data sources + flex modules inherit their env through
the physical catalog name, so they get no env_id.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- environments table ---------------------------------------------
    op.create_table(
        "environments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- catalogs -------------------------------------------------------
    op.add_column("catalogs", sa.Column("logical_name", sa.String(), nullable=True))
    op.execute("UPDATE catalogs SET logical_name = name WHERE logical_name IS NULL")
    op.alter_column("catalogs", "logical_name", nullable=False)
    op.add_column(
        "catalogs",
        sa.Column(
            "env_id",
            sa.UUID(),
            sa.ForeignKey("environments.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "catalogs",
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_catalogs_env_id", "catalogs", ["env_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_catalogs_env_logical ON catalogs "
        "(env_id, logical_name) NULLS NOT DISTINCT"
    )

    # --- object_types ---------------------------------------------------
    op.add_column(
        "object_types",
        sa.Column(
            "env_id",
            sa.UUID(),
            sa.ForeignKey("environments.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column("object_types", sa.Column("base_id", sa.UUID(), nullable=True))
    op.add_column(
        "object_types",
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.drop_constraint("object_types_name_key", "object_types", type_="unique")
    op.create_index("ix_object_types_env_id", "object_types", ["env_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_object_types_env_name ON object_types "
        "(env_id, name) NULLS NOT DISTINCT"
    )

    # --- object_type_traits ---------------------------------------------
    op.add_column(
        "object_type_traits",
        sa.Column(
            "env_id",
            sa.UUID(),
            sa.ForeignKey("environments.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.drop_constraint(
        "uq_object_type_traits_pair", "object_type_traits", type_="unique"
    )
    op.create_index(
        "ix_object_type_traits_env_id", "object_type_traits", ["env_id"]
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_object_type_traits_pair ON object_type_traits "
        "(env_id, object_type_id, trait_name) NULLS NOT DISTINCT"
    )

    # --- object_factories -----------------------------------------------
    op.add_column(
        "object_factories",
        sa.Column(
            "env_id",
            sa.UUID(),
            sa.ForeignKey("environments.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column("object_factories", sa.Column("base_id", sa.UUID(), nullable=True))
    op.add_column(
        "object_factories",
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.drop_constraint(
        "uq_object_factories_source_type", "object_factories", type_="unique"
    )
    op.create_index("ix_object_factories_env_id", "object_factories", ["env_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_object_factories_source_type ON object_factories "
        "(env_id, data_source_id, object_type_id) NULLS NOT DISTINCT"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_object_factories_source_type")
    op.drop_index("ix_object_factories_env_id", table_name="object_factories")
    op.create_unique_constraint(
        "uq_object_factories_source_type",
        "object_factories",
        ["data_source_id", "object_type_id"],
    )
    op.drop_column("object_factories", "deleted")
    op.drop_column("object_factories", "base_id")
    op.drop_column("object_factories", "env_id")

    op.execute("DROP INDEX IF EXISTS uq_object_type_traits_pair")
    op.drop_index("ix_object_type_traits_env_id", table_name="object_type_traits")
    op.create_unique_constraint(
        "uq_object_type_traits_pair",
        "object_type_traits",
        ["object_type_id", "trait_name"],
    )
    op.drop_column("object_type_traits", "env_id")

    op.execute("DROP INDEX IF EXISTS uq_object_types_env_name")
    op.drop_index("ix_object_types_env_id", table_name="object_types")
    op.create_unique_constraint("object_types_name_key", "object_types", ["name"])
    op.drop_column("object_types", "deleted")
    op.drop_column("object_types", "base_id")
    op.drop_column("object_types", "env_id")

    op.execute("DROP INDEX IF EXISTS uq_catalogs_env_logical")
    op.drop_index("ix_catalogs_env_id", table_name="catalogs")
    op.drop_column("catalogs", "deleted")
    op.drop_column("catalogs", "env_id")
    op.drop_column("catalogs", "logical_name")

    op.drop_table("environments")
