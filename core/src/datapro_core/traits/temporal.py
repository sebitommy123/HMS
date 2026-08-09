"""Temporal trait.

Declares "every instance of this object type has a primary timestamp".
Per factory, asks for one column to play that role. No SQL impact in
Q1 — the trait exists so that when WHERE clauses arrive, time-range
filters know which column to push down to each factory.

The current Q1 validator only checks that the column exists; future
work will check that the column is a temporal type (timestamp /
bigint epoch / date).
"""


class TemporalTrait:
    name = "temporal"
    description = (
        "Object instances are timestamped. Each factory picks one column "
        "as its timestamp. Doesn't affect SQL yet; enables time-range "
        "WHERE clauses once those land."
    )

    def required_config_keys(self) -> list[str]:
        return ["column"]

    def validate_factory_config(
        self,
        config: dict,
        available_columns: list[tuple[str, str]],
    ) -> str | None:
        if not isinstance(config, dict):
            return "temporal config must be an object with a 'column' key"
        column = config.get("column")
        if not isinstance(column, str) or not column.strip():
            return "temporal config must include a non-empty string 'column'"
        valid_names = {name for (name, _ty) in available_columns}
        if column not in valid_names:
            return (
                f"temporal column {column!r} is not a column of the "
                f"data source's table"
            )
        return None
