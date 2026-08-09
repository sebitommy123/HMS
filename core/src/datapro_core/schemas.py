from pydantic import BaseModel, ConfigDict, Field, field_validator


class CatalogCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    connector: str = Field(min_length=1, max_length=128)
    properties: dict[str, str] = Field(default_factory=dict)
    # Flex-only: the Python module source. When ``connector == "flex"``
    # and ``source`` is provided, Core materializes it to the shared
    # volume + auto-populates the ``flex.module_path`` property.
    # Ignored for other connectors. Mutually-exclusive with a manually
    # supplied ``flex.module_path`` in ``properties``.
    source: str | None = Field(default=None)

    @field_validator("name", "connector")
    @classmethod
    def lowercase_identifier(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                "must contain only alphanumerics, underscores, and hyphens"
            )
        return v


class FlexModuleUpdateRequest(BaseModel):
    """PUT /flex-modules/{catalog_name}. Replaces the entire source.
    Empty text is rejected — that would be a useless module."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)


class FlexModulePreviewRequest(BaseModel):
    """POST /flex-modules/preview. Runs the supplied source in a
    transient catalog so the operator can see what schema + rows it
    would produce before committing."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    # Per-table sample row cap. Per-table because preview enumerates
    # every declared table and samples the first split of each.
    sample_limit: int = Field(default=10, ge=1, le=500)


class FlexModuleReplaceRequest(BaseModel):
    """POST /flex-modules/{catalog_name}/replace. Substring replacement
    intended for the AI's editing surface — ``old_text`` must appear
    exactly once in the source, else the request is rejected so the AI
    knows to disambiguate. Avoids whole-file rewrites for trivial edits."""

    model_config = ConfigDict(extra="forbid")

    old_text: str = Field(min_length=1)
    new_text: str = Field(default="")


class FlexModuleReplaceLinesRequest(BaseModel):
    """POST /flex-modules/{catalog_name}/replace-lines. Replace an
    inclusive 1-based line range with ``new_text``. Newline at the end
    of ``new_text`` is optional; the endpoint normalizes."""

    model_config = ConfigDict(extra="forbid")

    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    new_text: str = Field(default="")


class ObjectTypeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=4096)

    @field_validator("name")
    @classmethod
    def lowercase_identifier(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                "must contain only alphanumerics, underscores, and hyphens"
            )
        return v


class ObjectTypeUpdateRequest(BaseModel):
    """PATCH /object-types/{id}. Either field optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4096)

    @field_validator("name")
    @classmethod
    def lowercase_identifier(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                "must contain only alphanumerics, underscores, and hyphens"
            )
        return v


class ObjectFactoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_source_id: str = Field(min_length=1)
    object_type_id: str = Field(min_length=1)
    description: str = Field(default="", max_length=4096)
    use_all_columns: bool = Field(default=True)
    column_spec: list[str] = Field(
        default_factory=list, max_length=256, description="Each entry max 1024 chars."
    )
    # Per-trait configuration. Shape ``{trait_name: {trait-specific keys}}``;
    # each trait validates its own slot. Required when the object type has
    # traits enabled — the factory_validator will mark the factory broken
    # otherwise (which is fine for staged setup).
    trait_config: dict = Field(default_factory=dict)


class ObjectFactoryUpdateRequest(BaseModel):
    """PATCH /object-factories/{id}. Each field optional; provide whichever
    you want to change. ``trait_config``, if provided, FULLY REPLACES the
    factory's trait_config dict — to change one trait's config keep the
    others by reading first."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=4096)
    use_all_columns: bool | None = Field(default=None)
    column_spec: list[str] | None = Field(default=None, max_length=256)
    trait_config: dict | None = Field(default=None)


class CatalogUpdateRequest(BaseModel):
    """PATCH /catalogs/{name}. Both fields optional; only what's provided
    changes. ``properties``, if provided, fully REPLACES the catalog's
    properties dict — to add or remove a single key, GET first, mutate the
    object, then PATCH."""

    model_config = ConfigDict(extra="forbid")

    connector: str | None = Field(default=None, min_length=1, max_length=128)
    properties: dict[str, str] | None = None

    @field_validator("connector")
    @classmethod
    def lowercase_identifier(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                "must contain only alphanumerics, underscores, and hyphens"
            )
        return v
