"""Identity trait.

Declares "every instance of this object type has a stable identifier".
Per factory, asks for one column on the data source's table to play
that role. The query planner uses this column to JOIN branches across
factories (FULL OUTER JOIN on the identity column) instead of UNIONing
rows that don't know they're the same object.
"""


class IdentityTrait:
    name = "identity"
    description = (
        "Object instances have a stable identifier. Each factory picks "
        "one column to serve as the identity; queries OUTER JOIN factories "
        "on that column instead of UNIONing rows."
    )

    def required_config_keys(self) -> list[str]:
        return ["column"]

    def validate_factory_config(
        self,
        config: dict,
        available_columns: list[tuple[str, str]],
    ) -> str | None:
        if not isinstance(config, dict):
            return "identity config must be an object with a 'column' key"
        column = config.get("column")
        if not isinstance(column, str) or not column.strip():
            return "identity config must include a non-empty string 'column'"
        valid_names = {name for (name, _ty) in available_columns}
        if column not in valid_names:
            return (
                f"identity column {column!r} is not a column of the "
                f"data source's table"
            )
        return None
