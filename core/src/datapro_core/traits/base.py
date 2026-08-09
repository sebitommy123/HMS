"""Trait base class.

A Trait owns one piece of object-type capability and its per-factory
configuration rules. SQL behaviour is NOT here — the query planner
branches on which traits a type has and applies the appropriate
strategy. Keeping SQL out of the Trait keeps complex cross-trait
interactions (e.g. Identity + Temporal + future Versioned) in one
place that can reason about all of them together, instead of trying
to compose them through tiny per-trait hooks.
"""

from __future__ import annotations

from typing import Protocol


class Trait(Protocol):
    """A trait an object type can have. Implementations are stateless
    singletons registered in ``traits/registry.py``."""

    name: str
    """The wire-format identifier, snake_case (e.g. ``identity``)."""

    description: str
    """Short human-readable summary, shown in the UI's add-trait dropdown."""

    def required_config_keys(self) -> list[str]:
        """Keys the factory's ``trait_config[trait_name]`` dict must
        contain. Used by validators / UI to know what fields to show
        before the trait-specific validate runs."""
        ...

    def validate_factory_config(
        self,
        config: dict,
        available_columns: list[tuple[str, str]],
    ) -> str | None:
        """Validate one factory's config for this trait. Returns ``None``
        if valid, else a precise human-readable error explaining what's
        wrong (which gets piped into the factory's ``last_error``).

        ``available_columns`` is the live ``(name, type)`` list for the
        factory's data source, as returned by the factory_validator's
        Trino introspection. The trait can reject configs that reference
        non-existent columns or columns of the wrong type.
        """
        ...
