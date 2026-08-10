"""Trino client. Reads catalog state, applies CREATE/DROP CATALOG DDL, runs
operator-issued passthrough queries with wall-clock and row-count limits.

No mocking, ever. This module talks to a live Trino. Errors from Trino are surfaced
as TrinoError so callers can record the real Trino message.
"""

import math
import time
from dataclasses import dataclass

import requests
import trino


SYSTEM_CATALOGS = {"system"}


class TrinoError(Exception):
    """Raised when Trino rejects a query or is unreachable."""


class QueryTimeoutError(TrinoError):
    """Raised when a passthrough query exceeds its wall-clock budget."""


def _is_trino_time_limit(exc: Exception) -> bool:
    """True if a Trino error is a server-side execution-time-limit kill
    (the `query_max_execution_time` budget), so we can surface it as a timeout
    rather than a generic query error."""
    name = getattr(exc, "error_name", "") or ""
    return name == "EXCEEDED_TIME_LIMIT" or "exceeded maximum execution time" in str(exc).lower()


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool
    elapsed_seconds: float
    query_id: str | None


@dataclass(frozen=True)
class TrinoCatalogSnapshot:
    """One row from Trino's `system.metadata.catalogs`. Properties are NOT exposed
    by Trino's introspection — we only get name + connector name."""

    name: str
    connector: str


@dataclass(frozen=True)
class TrinoClient:
    host: str
    port: int
    user: str
    request_timeout: float = 10.0

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _connection(
        self, session_properties: dict[str, str] | None = None
    ) -> trino.dbapi.Connection:
        return trino.dbapi.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            catalog="system",
            schema="runtime",
            request_timeout=self.request_timeout,
            session_properties=session_properties or {},
        )

    def ping(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/v1/info", timeout=2)
            return r.ok
        except requests.RequestException:
            return False

    def list_catalogs(self) -> list[TrinoCatalogSnapshot]:
        """Read all non-system catalogs from Trino's metadata."""
        sql = "SELECT catalog_name, connector_name FROM system.metadata.catalogs"
        try:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute(sql)
                rows = cur.fetchall()
        except Exception as exc:
            raise TrinoError(f"failed to list catalogs: {exc}") from exc
        return [
            TrinoCatalogSnapshot(name=r[0], connector=r[1])
            for r in rows
            if r[0] not in SYSTEM_CATALOGS
        ]

    def create_catalog(self, name: str, connector: str, properties: dict[str, str]) -> None:
        """Issue CREATE CATALOG via Trino. Properties are escaped string-by-string."""
        with_clause = ""
        if properties:
            props = ", ".join(
                f"{_quote_ident(k)} = {_quote_str(v)}" for k, v in properties.items()
            )
            with_clause = f" WITH ({props})"
        # Trino requires the connector name to be an unquoted identifier (the plugin
        # name lookup is by exact unquoted match). The catalog name CAN be quoted to
        # support hyphens and special chars in the name.
        if not connector.replace("_", "").isalnum():
            raise TrinoError(
                f"connector name must be unquoted-identifier-safe; got {connector!r}"
            )
        sql = f"CREATE CATALOG {_quote_ident(name)} USING {connector}{with_clause}"
        try:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute(sql)
                cur.fetchall()
        except Exception as exc:
            raise TrinoError(f"CREATE CATALOG {name} failed: {exc}") from exc

    def drop_catalog(self, name: str) -> None:
        sql = f"DROP CATALOG {_quote_ident(name)}"
        try:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute(sql)
                cur.fetchall()
        except Exception as exc:
            raise TrinoError(f"DROP CATALOG {name} failed: {exc}") from exc

    def show_columns(
        self, catalog: str, schema: str, table: str
    ) -> list[tuple[str, str]]:
        """Run ``SHOW COLUMNS FROM <catalog>.<schema>.<table>`` and return
        ``(name, type)`` per column. Used by the data-source columns
        endpoint so the UI / agent can introspect a registered table
        without crafting SQL themselves.

        Trino returns rows of (Column, Type, Extra, Comment). We keep the
        first two."""
        path = (
            f"{_quote_ident(catalog)}."
            f"{_quote_ident(schema)}."
            f"{_quote_ident(table)}"
        )
        sql = f"SHOW COLUMNS FROM {path}"
        try:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute(sql)
                rows = cur.fetchall()
        except Exception as exc:
            raise TrinoError(f"SHOW COLUMNS failed: {exc}") from exc
        return [(str(r[0]), str(r[1])) for r in rows]

    def list_tables(self, catalog: str) -> list[tuple[str, str]]:
        """Enumerate every ``(schema, table)`` in a catalog by querying its
        ``information_schema.tables``. Used by the reconciler to sync a
        catalog's data sources against what Trino actually exposes.

        Excludes the ``information_schema`` schema itself (metadata, not
        user data). Raises ``TrinoError`` if the catalog can't be
        introspected — the reconciler treats that as the catalog being
        registered-but-DOWN rather than as "the catalog has no tables".
        """
        sql = (
            "SELECT table_schema, table_name "
            f"FROM {_quote_ident(catalog)}.information_schema.tables "
            "WHERE table_schema <> 'information_schema'"
        )
        try:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute(sql)
                rows = cur.fetchall()
        except Exception as exc:
            raise TrinoError(f"listing tables for catalog {catalog} failed: {exc}") from exc
        return [(str(r[0]), str(r[1])) for r in rows]

    def describe_query(self, sql: str) -> list[tuple[str, str]]:
        """Return ``(column_name, trino_type_string)`` for every column the
        statement WOULD produce, without scanning data. Used by the query
        planner so the UNION-builder can NULL-fill missing columns with a
        compatible cast.

        Trino's ``DESCRIBE`` only handles tables (the SQL-standard
        ``DESCRIBE (<query>)`` isn't supported), so we use the portable
        ``SELECT * FROM (<query>) LIMIT 0`` trick — Trino plans and
        returns column metadata without fetching rows.
        """
        wrapped = f"SELECT * FROM ({sql}) AS _describe_target LIMIT 0"
        try:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute(wrapped)
                description = getattr(cur, "description", None) or []
                # Drain any (empty) result so the cursor cleans up.
                cur.fetchall()
        except Exception as exc:
            raise TrinoError(f"DESCRIBE-via-LIMIT-0 failed: {exc}") from exc
        # PEP-249: each description entry is (name, type_code, ...). For
        # Trino's DB-API the type_code is a string like 'bigint', 'varchar',
        # 'varchar(50)', 'timestamp(3) with time zone' — usable verbatim
        # in a CAST(... AS <type>) expression.
        return [(c[0], _stringify_type(c[1])) for c in description]

    def execute_query(
        self,
        sql: str,
        *,
        timeout_seconds: float,
        max_rows: int,
        batch_size: int = 1000,
    ) -> QueryResult:
        """Run an arbitrary SQL statement against Trino, stop fetching when either
        the wall-clock budget or the row cap is exceeded. Bounds are enforced
        defensively even if the caller passes nonsense (1..600s, 1..1_000_000 rows).
        """
        timeout_seconds = max(1.0, min(timeout_seconds, 600.0))
        max_rows = max(1, min(int(max_rows), 1_000_000))

        # Enforce the budget SERVER-SIDE too. The fetch-loop wall-clock below
        # only fires between row batches, so a query that stalls in Trino
        # planning/queuing before the first batch would otherwise be bounded
        # only by the per-HTTP-request timeout, not the total budget. Trino's
        # query_max_execution_time kills the query itself when exceeded.
        session_properties = {"query_max_execution_time": f"{math.ceil(timeout_seconds)}s"}

        start = time.monotonic()
        query_id: str | None = None
        try:
            with self._connection(session_properties) as conn:
                cur = conn.cursor()
                cur.execute(sql)
                query_id = getattr(cur, "query_id", None)
                description = getattr(cur, "description", None)
                columns = [c[0] for c in description] if description else []

                rows: list[list] = []
                truncated = False
                while True:
                    elapsed = time.monotonic() - start
                    if elapsed >= timeout_seconds:
                        _try_cancel(cur)
                        raise QueryTimeoutError(
                            f"query exceeded {timeout_seconds:.1f}s wall-clock budget"
                        )
                    remaining = max_rows - len(rows)
                    if remaining <= 0:
                        truncated = True
                        _try_cancel(cur)
                        break
                    batch = cur.fetchmany(min(batch_size, remaining + 1))
                    if not batch:
                        break
                    for r in batch:
                        if len(rows) >= max_rows:
                            truncated = True
                            break
                        rows.append(list(r))
                    if truncated:
                        _try_cancel(cur)
                        break
        except QueryTimeoutError:
            raise
        except Exception as exc:
            elapsed = time.monotonic() - start
            # A server-side execution-time-limit kill is a timeout by any other
            # name — surface it as one regardless of our local clock (it may fire
            # before the fetch-loop wall-clock does). Likewise if we blew past the
            # budget locally, the underlying error (dropped HTTP connection,
            # Trino-side cancel, etc.) is functionally a timeout. Surface both as
            # QueryTimeoutError so the API returns 504 instead of a misleading 400.
            if _is_trino_time_limit(exc) or elapsed >= timeout_seconds:
                raise QueryTimeoutError(
                    f"query exceeded {timeout_seconds:.1f}s budget"
                ) from exc
            raise TrinoError(str(exc)) from exc

        elapsed = time.monotonic() - start
        return QueryResult(
            columns=columns,
            rows=rows,
            truncated=truncated,
            elapsed_seconds=elapsed,
            query_id=query_id,
        )


def _try_cancel(cur: object) -> None:
    cancel = getattr(cur, "cancel", None)
    if callable(cancel):
        try:
            cancel()
        except Exception:
            pass


def _quote_ident(s: str) -> str:
    """Trino identifier — double-quote, escape internal double-quotes."""
    return '"' + s.replace('"', '""') + '"'


def _quote_str(s: str) -> str:
    """Trino string literal — single-quote, escape internal single-quotes."""
    return "'" + s.replace("'", "''") + "'"


def _stringify_type(type_code: object) -> str:
    """Best-effort conversion of a PEP-249 type_code to a string we can
    pass back to Trino in a CAST. Trino's Python DB-API typically gives
    strings directly (e.g. ``'bigint'``, ``'varchar(50)'``), but defend
    against alternative shapes (objects with ``__name__``, tuples, etc.)
    by falling back to repr-without-quotes."""
    if isinstance(type_code, str):
        return type_code
    name = getattr(type_code, "__name__", None)
    if isinstance(name, str):
        return name
    return str(type_code)
