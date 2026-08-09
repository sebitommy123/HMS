"""Canonical-JSON query → validated Query.

Q1 surface is intentionally tiny: `from` (required), `limit` (1-100,
default 25), `timeout_seconds` (1-30, default 10). Unknown fields are
rejected so we catch typos / unsupported features early instead of
silently ignoring them.
"""

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from datapro_core.query.models import Query


DEFAULT_LIMIT = 25
MAX_LIMIT = 100
DEFAULT_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 30


class _QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # `from` is a Python reserved word, so the Pydantic field is named
    # from_type with an alias so the wire-format key stays `from`.
    from_type: str = Field(min_length=1, max_length=128, alias="from")
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT_SECONDS, ge=1, le=MAX_TIMEOUT_SECONDS
    )


class ParseError(Exception):
    """Raised by parse_query when the input doesn't conform. The endpoint
    catches this and renders a 400 with the structured details."""

    def __init__(self, error: str, details: object):
        self.error = error
        self.details = details
        super().__init__(error)


def parse_query(raw: object) -> Query:
    """Validate a raw JSON-like dict into a Query. Raises ParseError on
    malformed input (with structured details for the endpoint)."""
    if not isinstance(raw, dict):
        raise ParseError("invalid_request", "Query body must be a JSON object.")

    try:
        parsed = _QueryRequest.model_validate(raw)
    except ValidationError as exc:
        raise ParseError(
            "invalid_request",
            [
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in exc.errors()
            ],
        ) from exc

    return Query(
        from_type=parsed.from_type,
        limit=parsed.limit,
        timeout_seconds=parsed.timeout_seconds,
    )
