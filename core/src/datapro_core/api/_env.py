"""Shared helper for reading the ``?env=<uuid>`` axis off a request.

Every mutation/read endpoint accepts an optional ``env`` query param. Absent →
production (``None``). Present → must name an OPEN environment. Endpoints call
``env_from_request(session)`` and thread the returned id into the ``staging.env``
overlay helpers.
"""

import uuid

from flask import request
from sqlalchemy.orm import Session

from datapro_core.staging.env import EnvError, require_open_env


class BadEnv(Exception):
    """Malformed or non-open env id — the endpoint turns this into a 400."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def env_from_request(session: Session) -> uuid.UUID | None:
    raw = request.args.get("env")
    if raw is None or raw == "":
        return None
    try:
        env_id = uuid.UUID(raw)
    except (ValueError, AttributeError):
        raise BadEnv(f"env is not a valid uuid: {raw!r}")
    try:
        return require_open_env(session, env_id)
    except EnvError as exc:
        raise BadEnv(str(exc))
