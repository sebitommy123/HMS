"""The action language — the *only* thing the AI can produce.

A chat's entire intent is an ordered ``list[Action]`` (a **changeset**). The
agent never mutates Core directly, not even in staging; it only appends/edits
actions. Staging is a pure function of ``(prod snapshot, changeset)``: to
materialize it we snapshot prod read-only and **replay** the actions in order.
Nothing the agent does to staging persists except the actions themselves.

Apply = replay the same actions against prod, transactionally. There is never a
diff between two object graphs — the changeset *is* the delta.

## References (the one genuinely new idea)

An action can operate on:
  * a **prod** entity that already exists  → ``ProdRef(kind, id)`` (a stable id), or
  * an entity **created by an earlier action** in this same changeset
    → ``ActionRef(handle)`` (a symbolic handle the create-action declared).

Every ``create_*`` action declares a ``handle`` (unique within the changeset).
Later actions reference it by handle. At replay the engine keeps a symbol table
``handle -> freshly-created concrete id`` and resolves refs as it goes. Because
handles are symbolic, replaying onto a *fresh* prod snapshot (after prod drifts,
or after the agent reorders actions) rebinds cleanly to whatever new ids get
minted — the changeset never hard-codes a staging id. This is exactly
Terraform's resource-reference graph.

Data sources are special: they are *discovered* from a catalog, not created by
an action. So a factory names its source structurally as
``DataSourceRef(catalog=<Ref>, schema, table)``; replay resolves it to a concrete
``data_source_id`` after the (possibly action-created) catalog has synced.

## Normalization

The vocabulary is deliberately small and each op is a whole-value replace, so
replay is deterministic and order-independent per target. Ergonomic agent tools
("add one column", "replace a substring in the flex module") do the
read-modify-write against the *materialized* stage and emit a single
whole-value action (``update_object_factory`` with the full ``column_spec``,
``set_flex_module`` with the full source). The log therefore never contains a
delta that depends on a prior stage's incidental state.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# References
# --------------------------------------------------------------------------- #

# The entity kinds a reference can point at. Data sources are absent on purpose:
# they are never a create target (they are discovered from catalogs).
RefKind = Literal["catalog", "object_type", "object_factory"]


class ProdRef(BaseModel):
    """Points at an entity that already exists in production. ``id`` is that
    entity's stable prod id (catalog *name* for catalogs, uuid for the rest).
    Touching a ProdRef is what puts an entity in the changeset's *touched set*
    for drift detection."""

    model_config = ConfigDict(extra="forbid")
    source: Literal["prod"] = "prod"
    kind: RefKind
    id: str = Field(min_length=1)


class ActionRef(BaseModel):
    """Points at an entity created by an earlier action in this changeset, by
    the ``handle`` that action declared. Never touches prod → never drifts."""

    model_config = ConfigDict(extra="forbid")
    source: Literal["action"] = "action"
    handle: str = Field(min_length=1)


Ref = Annotated[Union[ProdRef, ActionRef], Field(discriminator="source")]


class DataSourceRef(BaseModel):
    """A data source named structurally: the catalog it lives in (itself a Ref,
    so it can be an action-created catalog) plus the schema + table. Resolved to
    a concrete ``data_source_id`` at replay, after the catalog has synced."""

    model_config = ConfigDict(extra="forbid")
    catalog: Ref
    schema_name: str = Field(min_length=1)
    table: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Action base
# --------------------------------------------------------------------------- #


def _new_id() -> str:
    return str(uuid4())


class _ActionBase(BaseModel):
    """Common to every action. ``id`` is the stable identity of the action *in
    the list* — it survives reorder/edit and is what the UI and
    'delete action N' operations key on. It is NOT an entity id."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=_new_id)
    # A short human-readable description the agent writes when it creates/edits
    # the action, explaining what this action does and why. Shown to the user in
    # the UI when they expand the action. Not sent to Core — pure changeset
    # metadata for review.
    note: str = Field(default="", max_length=2000)


class _CreateBase(_ActionBase):
    """A create-action additionally declares a ``handle`` — the symbolic name
    later actions use to reference the entity it produces. Unique per changeset."""

    handle: str = Field(min_length=1, max_length=128)


# --------------------------------------------------------------------------- #
# Catalog actions
# --------------------------------------------------------------------------- #


class CreateCatalog(_CreateBase):
    op: Literal["create_catalog"] = "create_catalog"
    name: str = Field(min_length=1, max_length=128)
    connector: str = Field(min_length=1, max_length=128)
    properties: dict[str, str] = Field(default_factory=dict)
    # Flex only: the module source. Mirrors CatalogCreateRequest.source.
    source: str | None = None


class UpdateCatalog(_ActionBase):
    op: Literal["update_catalog"] = "update_catalog"
    target: Ref
    connector: str | None = Field(default=None, min_length=1, max_length=128)
    # Whole-value replace, exactly like PATCH /catalogs.
    properties: dict[str, str] | None = None


class DeleteCatalog(_ActionBase):
    op: Literal["delete_catalog"] = "delete_catalog"
    target: Ref


class SetFlexModule(_ActionBase):
    """Whole-source replace for a flex catalog's module. Substring/line edits
    the agent makes normalize down to one of these."""

    op: Literal["set_flex_module"] = "set_flex_module"
    target: Ref
    source: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Object-type actions
# --------------------------------------------------------------------------- #


class CreateObjectType(_CreateBase):
    op: Literal["create_object_type"] = "create_object_type"
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=4096)


class UpdateObjectType(_ActionBase):
    op: Literal["update_object_type"] = "update_object_type"
    target: Ref
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4096)


class DeleteObjectType(_ActionBase):
    op: Literal["delete_object_type"] = "delete_object_type"
    target: Ref


class AddTrait(_ActionBase):
    """Attach a trait to an object type. Config-less by design — the identity/
    temporal *configuration* lives per-factory in ``trait_config`` (see
    factories). Idempotent, mirroring PUT .../traits/<name>."""

    op: Literal["add_trait"] = "add_trait"
    target: Ref  # -> object_type
    trait: str = Field(min_length=1)


class RemoveTrait(_ActionBase):
    op: Literal["remove_trait"] = "remove_trait"
    target: Ref  # -> object_type
    trait: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Object-factory actions
# --------------------------------------------------------------------------- #


class CreateObjectFactory(_CreateBase):
    op: Literal["create_object_factory"] = "create_object_factory"
    data_source: DataSourceRef
    object_type: Ref
    description: str = Field(default="", max_length=4096)
    use_all_columns: bool = True
    column_spec: list[str] = Field(default_factory=list, max_length=256)
    # {trait_name: {trait-specific keys}}. Whole-value replace on update.
    trait_config: dict = Field(default_factory=dict)


class UpdateObjectFactory(_ActionBase):
    op: Literal["update_object_factory"] = "update_object_factory"
    target: Ref
    description: str | None = Field(default=None, max_length=4096)
    use_all_columns: bool | None = None
    column_spec: list[str] | None = Field(default=None, max_length=256)
    trait_config: dict | None = None


class DeleteObjectFactory(_ActionBase):
    op: Literal["delete_object_factory"] = "delete_object_factory"
    target: Ref


# --------------------------------------------------------------------------- #
# The tagged union + the changeset
# --------------------------------------------------------------------------- #

Action = Annotated[
    Union[
        CreateCatalog,
        UpdateCatalog,
        DeleteCatalog,
        SetFlexModule,
        CreateObjectType,
        UpdateObjectType,
        DeleteObjectType,
        AddTrait,
        RemoveTrait,
        CreateObjectFactory,
        UpdateObjectFactory,
        DeleteObjectFactory,
    ],
    Field(discriminator="op"),
]


class Changeset(BaseModel):
    """The whole persisted intent of one chat: an ordered action list. This +
    the saved test suite are the *only* durable artifacts; staging is derived."""

    model_config = ConfigDict(extra="forbid")
    actions: list[Action] = Field(default_factory=list)
