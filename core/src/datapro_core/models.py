import uuid
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from datapro_core.db import Base


class CatalogStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    BROKEN = "broken"
    # Registered in Trino (CREATE CATALOG succeeded), but the backing store
    # is unreachable/erroring — introspecting its tables fails. The catalog
    # stays registered (reconciler won't drop it) and flips back to ENABLED
    # once the store recovers. Distinct from BROKEN, which means the catalog
    # never got registered because CREATE CATALOG itself failed.
    DOWN = "down"


class DataSourceStatus(StrEnum):
    """Lifecycle of a data source relative to the live Trino catalog.
    Data sources are sync-owned (the reconciler discovers them), never
    created by hand.

    ``active`` = the table currently exists in Trino. ``deleted`` = the
    table disappeared from Trino but object factories still reference it,
    so we keep the row for the operator to resolve rather than cascade-
    deleting their factories. An unreferenced table that disappears is
    hard-deleted instead of being marked ``deleted``."""

    ACTIVE = "active"
    DELETED = "deleted"


class ObjectFactoryStatus(StrEnum):
    """Sync state between a factory's recorded config and the live Trino
    schema it depends on. ``ok`` = the factory's catalog is reachable,
    its table exists, and its column_spec entries are all real columns.
    ``broken`` = validation last failed for some reason captured in
    ``last_error``; the planner excludes broken factories from queries."""

    OK = "ok"
    BROKEN = "broken"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Catalog(Base):
    __tablename__ = "catalogs"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    connector: Mapped[str] = mapped_column(String, nullable=False)
    properties: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False, default=CatalogStatus.ENABLED)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "connector": self.connector,
            "properties": self.properties,
            "status": self.status,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ObjectTypeTrait(Base):
    """An (object_type, trait_name) attachment. Many-to-many between
    object types and the hardcoded trait registry. The trait_name is
    just a string — the actual behaviour for each trait lives in
    ``datapro_core.traits``. Unknown trait names get rejected at the
    API layer before reaching the DB.
    """

    __tablename__ = "object_type_traits"
    __table_args__ = (
        UniqueConstraint(
            "object_type_id", "trait_name", name="uq_object_type_traits_pair"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    object_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("object_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trait_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ObjectType(Base):
    """A kind of thing DataPro can talk about — e.g. 'Company', 'Filing'.

    First-class entity with a stable UUID. The display ``name`` is unique but
    mutable; references should use the UUID so renames don't break links.
    Fields and traits will hang off this row in later slices; for now it's
    just name + description.
    """

    __tablename__ = "object_types"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    trait_rows = relationship(
        "ObjectTypeTrait",
        cascade="all, delete-orphan",
        lazy="joined",
    )

    @property
    def trait_names(self) -> list[str]:
        # Sorted for stable wire output.
        return sorted({t.trait_name for t in self.trait_rows})

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "traits": self.trait_names,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class FlexModule(Base):
    """User-authored Python source for one flex-backed catalog.

    Exactly one row per flex catalog (unique on ``catalog_name``).
    Stored as plain text; the materializer writes it to the shared
    volume Trino reads from.
    """

    __tablename__ = "flex_modules"
    __table_args__ = (
        UniqueConstraint("catalog_name", name="uq_flex_modules_catalog"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    catalog_name: Mapped[str] = mapped_column(
        String,
        ForeignKey("catalogs.name", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_text: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "catalog_name": self.catalog_name,
            "source_text": self.source_text,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DataSource(Base):
    """A specific (schema, table) within a catalog. Catalogs own many data
    sources; object factories point at one. Decoupling factories from catalogs
    through this layer means multiple factories can sit on the same physical
    table, and the table address has a stable home for description /
    (eventually) sample / declared columns / freshness."""

    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint(
            "catalog_name",
            "schema_name",
            "table_name",
            name="uq_data_sources_path",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    catalog_name: Mapped[str] = mapped_column(
        String,
        ForeignKey("catalogs.name", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    schema_name: Mapped[str] = mapped_column(String, nullable=False)
    table_name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default=DataSourceStatus.ACTIVE,
        server_default=DataSourceStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "catalog_name": self.catalog_name,
            "schema_name": self.schema_name,
            "table_name": self.table_name,
            # Convenience: the fully-qualified Trino path. UI panels and the
            # agent both want this often; cheaper to compute here than at
            # every callsite.
            "path": f"{self.catalog_name}.{self.schema_name}.{self.table_name}",
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ObjectFactory(Base):
    """A (data_source, object_type) pair that says "this data source produces
    objects of this type." Owned by both parents — if either is deleted, the
    factory goes away too. ``description`` is editable; column-selection mode
    + column list are editable; richer configuration arrives once traits land."""

    __tablename__ = "object_factories"
    __table_args__ = (
        UniqueConstraint(
            "data_source_id",
            "object_type_id",
            name="uq_object_factories_source_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    object_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("object_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    # Column-selection mode. ``use_all_columns=True`` (default) means the
    # factory inherits every column from the source table verbatim. When
    # False, ``column_spec`` is the explicit SELECT list. Freeform because
    # catalogs don't expose a fixed schema (Trino can return different
    # columns per query).
    use_all_columns: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # List of freeform column expressions — each entry is what you'd put in
    # a SELECT list (just a column name, an alias, or any expression).
    # Stored as JSON so the order and individual entries are preserved.
    column_spec: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    # Per-trait configuration for traits the parent object_type has
    # enabled. Shape: ``{trait_name: {trait-specific keys}}`` —
    # validated by each trait's ``validate_factory_config`` in
    # ``datapro_core.traits``. Stored as JSON so each trait can define
    # its own shape without a model change.
    trait_config: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    # Sync state vs. the live Trino schema. Maintained by
    # ``factory_validator.validate_object_factory`` — called at query
    # planning time and from the periodic heartbeat.
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default=ObjectFactoryStatus.OK,
        server_default=ObjectFactoryStatus.OK,
    )
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    object_type = relationship("ObjectType", lazy="joined")
    data_source = relationship("DataSource", lazy="joined")

    def to_dict(self) -> dict:
        # Denormalize key parent fields so UI tables don't need a second
        # round-trip per row.
        ds = self.data_source
        return {
            "id": str(self.id),
            "data_source_id": str(self.data_source_id),
            "catalog_name": ds.catalog_name if ds else None,
            "schema_name": ds.schema_name if ds else None,
            "table_name": ds.table_name if ds else None,
            "data_source_path": (
                f"{ds.catalog_name}.{ds.schema_name}.{ds.table_name}" if ds else None
            ),
            "object_type_id": str(self.object_type_id),
            "object_type_name": self.object_type.name if self.object_type else None,
            # Denormalized so UI can render trait-config widgets / badges
            # without a second round-trip to /object-types/{id}.
            "object_type_traits": (
                self.object_type.trait_names if self.object_type else []
            ),
            "description": self.description,
            "use_all_columns": self.use_all_columns,
            "column_spec": self.column_spec,
            "trait_config": self.trait_config or {},
            "status": self.status,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
