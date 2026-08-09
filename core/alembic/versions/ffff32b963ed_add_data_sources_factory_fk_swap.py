"""add data_sources; factory FK swap

Revision ID: ffff32b963ed
Revises: 16b8b2cf731f
Create Date: 2026-06-19 21:56:41.633161

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ffff32b963ed"
down_revision: Union[str, None] = "16b8b2cf731f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create the new data_sources table.
    op.create_table(
        "data_sources",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("catalog_name", sa.String(), nullable=False),
        sa.Column("schema_name", sa.String(), nullable=False),
        sa.Column("table_name", sa.String(), nullable=False),
        sa.Column(
            "description", sa.String(), nullable=False, server_default=""
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_name"], ["catalogs.name"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "catalog_name",
            "schema_name",
            "table_name",
            name="uq_data_sources_path",
        ),
    )
    op.create_index(
        op.f("ix_data_sources_catalog_name"),
        "data_sources",
        ["catalog_name"],
        unique=False,
    )

    # 2. Wipe existing object_factories. Existing rows have no schema/table
    # info (the column never existed); they're unusable under the new model.
    # Per the design call, the user re-creates them via the UI after the
    # data_source layer lands.
    op.execute("DELETE FROM object_factories")

    # 3. Swap the FK on object_factories from catalog_name to data_source_id.
    op.drop_constraint(
        op.f("object_factories_catalog_name_fkey"),
        "object_factories",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("uq_object_factories_catalog_type"),
        "object_factories",
        type_="unique",
    )
    op.drop_index(
        op.f("ix_object_factories_catalog_name"), table_name="object_factories"
    )
    op.drop_column("object_factories", "catalog_name")

    op.add_column(
        "object_factories",
        sa.Column("data_source_id", sa.UUID(), nullable=False),
    )
    op.create_index(
        op.f("ix_object_factories_data_source_id"),
        "object_factories",
        ["data_source_id"],
        unique=False,
    )
    op.create_foreign_key(
        "object_factories_data_source_id_fkey",
        "object_factories",
        "data_sources",
        ["data_source_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_object_factories_source_type",
        "object_factories",
        ["data_source_id", "object_type_id"],
    )


def downgrade() -> None:
    # Symmetric reverse. Downgrade ALSO wipes object_factories because the
    # forward path dropped catalog_name; we can't recover it.
    op.drop_constraint(
        "uq_object_factories_source_type", "object_factories", type_="unique"
    )
    op.drop_constraint(
        "object_factories_data_source_id_fkey",
        "object_factories",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_object_factories_data_source_id"), table_name="object_factories"
    )
    op.drop_column("object_factories", "data_source_id")

    op.execute("DELETE FROM object_factories")
    op.add_column(
        "object_factories",
        sa.Column("catalog_name", sa.VARCHAR(), nullable=False),
    )
    op.create_foreign_key(
        op.f("object_factories_catalog_name_fkey"),
        "object_factories",
        "catalogs",
        ["catalog_name"],
        ["name"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        op.f("uq_object_factories_catalog_type"),
        "object_factories",
        ["catalog_name", "object_type_id"],
    )
    op.create_index(
        op.f("ix_object_factories_catalog_name"),
        "object_factories",
        ["catalog_name"],
        unique=False,
    )

    op.drop_index(op.f("ix_data_sources_catalog_name"), table_name="data_sources")
    op.drop_table("data_sources")
