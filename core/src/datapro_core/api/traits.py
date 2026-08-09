"""Trait discovery endpoint.

The set of traits is hardcoded in ``datapro_core.traits`` — the UI's
"add a trait to this object type" dropdown asks here for the list +
human descriptions rather than hardcoding them on the SPA side.
"""

from flask import Blueprint, jsonify

from datapro_core.traits import TRAITS

bp = Blueprint("traits", __name__)


@bp.get("/traits")
def list_traits():
    """All traits the running Core knows about. Sorted by name for
    stable wire output."""
    return jsonify(
        [
            {
                "name": t.name,
                "description": t.description,
                "required_config_keys": list(t.required_config_keys()),
            }
            for t in sorted(TRAITS.values(), key=lambda x: x.name)
        ]
    )
