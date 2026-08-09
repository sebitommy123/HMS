"""Registry of known traits. Hardcoded — traits aren't user-defined.

Adding a new trait: write the implementation under ``traits/<name>.py``,
import it here, and add an instance to ``_TRAITS``. If the trait affects
query planning, also branch on it in the planner.
"""

from datapro_core.traits.base import Trait
from datapro_core.traits.identity import IdentityTrait
from datapro_core.traits.temporal import TemporalTrait


_TRAITS: dict[str, Trait] = {
    t.name: t
    for t in (
        IdentityTrait(),
        TemporalTrait(),
    )
}

TRAITS: dict[str, Trait] = dict(_TRAITS)


def get_trait(name: str) -> Trait | None:
    return _TRAITS.get(name)


def known_trait_names() -> list[str]:
    """Sorted list of trait names. Sorted output keeps API responses
    deterministic regardless of insertion order."""
    return sorted(_TRAITS.keys())
