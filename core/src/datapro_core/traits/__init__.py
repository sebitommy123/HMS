"""Object-type traits.

A trait declares a capability an object type can have, and the
configuration each factory producing that type must supply. Trait
*behaviour* during query planning (e.g. Identity rewriting UNION into
FULL OUTER JOIN) lives in the planner itself — the planner is
trait-aware. The Trait class here is intentionally just about
validation + config shape.

To add a new trait:
  1. Implement a Trait subclass under ``traits/<name>.py``.
  2. Register it in ``registry.py``.
  3. If it changes SQL, branch on it explicitly in the planner.
"""

from datapro_core.traits.base import Trait
from datapro_core.traits.registry import (
    TRAITS,
    get_trait,
    known_trait_names,
)

__all__ = ["Trait", "TRAITS", "get_trait", "known_trait_names"]
