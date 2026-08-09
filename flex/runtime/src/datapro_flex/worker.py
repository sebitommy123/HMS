"""Arrow Flight worker that hosts a single flex module.

The Java connector spawns one of these processes per catalog:

    python -m datapro_flex.worker --module-path /path/to/module.py --port 0

We bind a Flight server on the chosen port (0 → OS picks a free one),
print exactly one stdout line ``{"flight_port": NNNNN}`` so the parent
can connect, then serve requests until shutdown.

Three RPCs are exposed:

  * Action ``get_tables`` (body: empty)
      → JSON body: ``[{"schema": "...", "name": "...", "columns": [...]}, ...]``

  * Action ``get_splits`` (body: JSON ``{"table": {"schema","name"}, ...}``)
      → JSON body: always ``[{}]``. Flex runs a single split; the module
        has no say in splitting, so this is synthesized here and the
        module is never consulted. The Java side still calls it so Trino
        gets its one split to schedule.

  * DoGet ``ticket`` JSON ``{"table": {"schema","name"}, "split": {...}, "columns": [...]}``
      → stream of Arrow record batches. The module's ``read_table``
        yields every declared column; the worker projects each batch
        down to ``columns`` before it crosses the wire. The ``split``
        payload is the synthetic ``{}`` and is ignored.

Logging goes to stderr — stdout is reserved for the port handshake +
graceful "shutdown" acknowledgement (single ``{"shutdown":"ok"}`` line).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import signal
import sys
import threading
import types
from typing import Iterable

import pyarrow as pa
import pyarrow.flight as flight

from datapro_flex.arrow_schema import arrow_schema_for_table
from datapro_flex.contract import TableDescriptor


log = logging.getLogger("datapro_flex.worker")


# ---- module loading --------------------------------------------------


def _load_module(module_path: str) -> types.ModuleType:
    """Import the user's flex module by file path. We don't rely on
    sys.path so the module can live anywhere — Core materializes
    modules under a versioned path per catalog (Phase B)."""
    spec = importlib.util.spec_from_file_location("flex_user_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import flex module at {module_path!r}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for attr in ("get_tables", "read_table"):
        if not callable(getattr(mod, attr, None)):
            raise RuntimeError(
                f"flex module {module_path!r} is missing required callable: {attr}"
            )
    return mod


# ---- the Flight server -----------------------------------------------


class _FlexServer(flight.FlightServerBase):
    def __init__(self, location: str, module: types.ModuleType, module_path: str):
        super().__init__(location=location)
        self._module = module
        self._module_path = module_path
        self._tables_cache: list[TableDescriptor] | None = None
        self._tables_by_qname: dict[tuple[str, str], TableDescriptor] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    # -- tables --

    def _tables(self) -> list[TableDescriptor]:
        with self._lock:
            if self._tables_cache is None:
                raw = list(self._module.get_tables())
                normalized: list[TableDescriptor] = []
                for t in raw:
                    schema = str(t.get("schema") or "default")
                    name = str(t["name"])
                    cols = [
                        {"name": str(c["name"]), "type": str(c["type"])}
                        for c in t["columns"]
                    ]
                    desc: TableDescriptor = {"schema": schema, "name": name, "columns": cols}  # type: ignore[typeddict-item]
                    normalized.append(desc)
                self._tables_cache = normalized
                self._tables_by_qname = {(t["schema"], t["name"]): t for t in normalized}
            return self._tables_cache

    def _lookup_table(self, schema: str, name: str) -> TableDescriptor:
        self._tables()  # populate cache
        try:
            return self._tables_by_qname[(schema, name)]
        except KeyError:
            raise flight.FlightServerError(
                f"unknown flex table: {schema!r}.{name!r}"
            ) from None

    # -- actions: get_tables / get_splits / shutdown --

    def do_action(self, context, action):
        op = action.type
        body = bytes(action.body.to_pybytes()) if action.body is not None else b""
        if op == "get_tables":
            payload = json.dumps(self._tables()).encode("utf-8")
            return iter([payload])
        if op == "get_splits":
            # Flex always runs a single split — the module has no say in
            # splitting. We synthesize one empty split here; the module is
            # never consulted. Any constraints Java sends are ignored
            # (Trino re-applies predicates post-scan regardless).
            req = json.loads(body or b"{}")
            schema = str(req["table"]["schema"])
            name = str(req["table"]["name"])
            # Sanity: confirm the table exists before Trino schedules a scan.
            self._lookup_table(schema, name)
            payload = json.dumps([{}]).encode("utf-8")
            return iter([payload])
        if op == "reload":
            # Re-import the user's module from disk. New top-level code
            # runs; old function references already captured by in-flight
            # RPCs continue with the old definitions until they return.
            # We deliberately don't use importlib.reload — that requires
            # the module to live under a known sys.modules name; just
            # re-running spec_from_file_location is simpler and rebinds
            # cleanly.
            with self._lock:
                self._module = _load_module(self._module_path)
                self._tables_cache = None
                self._tables_by_qname = {}
            return iter([b'{"reload":"ok"}'])
        if op == "shutdown":
            # Tell the parent we're going away, then trigger the
            # graceful-exit path on the main thread.
            self._stop_event.set()
            return iter([b'{"shutdown":"ok"}'])
        raise flight.FlightServerError(f"unknown action: {op!r}")

    def list_actions(self, context):
        return [
            ("get_tables", "Return the module's declared tables"),
            ("get_splits", "Return the single synthetic split flex always uses"),
            ("reload", "Re-import the user module from disk + clear caches"),
            ("shutdown", "Stop the worker after the in-flight RPCs drain"),
        ]

    # -- DoGet: streamed Arrow batches for one split --

    def do_get(self, context, ticket):
        req = json.loads(bytes(ticket.ticket))
        schema_name = str(req["table"]["schema"])
        table_name = str(req["table"]["name"])
        # ``split`` is the synthetic {} and is ignored — flex has no splits.
        columns: list[str] = list(req["columns"])

        table = self._lookup_table(schema_name, table_name)
        arrow_schema = _projected_schema(table, columns)

        # The module yields every declared column; we project down to the
        # columns Trino asked for at the egress so the module never has to
        # think about projection.
        batches = self._module.read_table(table_name)
        projected = _project_batches(batches, columns, table_name)
        validated = _validate_batches(projected, arrow_schema, table_name)
        return flight.GeneratorStream(arrow_schema, validated)

    # -- termination plumbing --

    def await_stop(self) -> None:
        """Block until either a SIGTERM/SIGINT arrives or a client
        sends the ``shutdown`` action, then unblock the main thread
        so it can call ``server.shutdown()`` cleanly."""
        self._stop_event.wait()


def _projected_schema(table: TableDescriptor, columns: list[str]) -> pa.Schema:
    if not columns:
        return pa.schema([])
    full = arrow_schema_for_table(table)
    indices = [full.get_field_index(c) for c in columns]
    missing = [c for c, i in zip(columns, indices) if i < 0]
    if missing:
        raise flight.FlightServerError(
            f"query requested unknown columns for table "
            f"{table['schema']!r}.{table['name']!r}: {missing!r}"
        )
    return pa.schema([full.field(i) for i in indices])


def _project_batches(
    batches: Iterable[pa.RecordBatch],
    columns: list[str],
    table_name: str,
) -> Iterable[pa.RecordBatch]:
    """Drop every column the query didn't ask for. ``read_table`` always
    produces the full declared schema; Trino only wants ``columns`` (which
    may be empty for COUNT(*), yielding zero-column batches that still
    carry the right row count). Selecting by name from the full batch
    also reorders to match the requested projection order."""
    for batch in batches:
        try:
            yield batch.select(columns)
        except KeyError as exc:
            raise flight.FlightServerError(
                f"read_table for {table_name!r} produced a batch missing a "
                f"declared column (query needs {columns!r}, batch has "
                f"{batch.schema.names!r}): {exc}"
            ) from None


def _validate_batches(
    batches: Iterable[pa.RecordBatch],
    expected_schema: pa.Schema,
    table_name: str,
) -> Iterable[pa.RecordBatch]:
    """Cheap shape check on every batch the user yields. We don't deep-
    compare types per column on every batch (Arrow can cast cheaply when
    the planner asks for it); we do check field names + arity so a
    misshapen batch fails loudly at the boundary rather than mid-Trino."""
    expected_names = list(expected_schema.names)
    for batch in batches:
        if list(batch.schema.names) != expected_names:
            raise flight.FlightServerError(
                f"read_table produced batch with wrong column shape for "
                f"table {table_name!r}: expected {expected_names!r}, "
                f"got {batch.schema.names!r}"
            )
        yield batch


# ---- entry point -----------------------------------------------------


def _emit_handshake(port: int) -> None:
    """Single-line stdout handshake the parent Java process parses. Print
    + flush + close stdout so subsequent prints (or someone's misbehaving
    library) can't interleave."""
    sys.stdout.write(json.dumps({"flight_port": port}) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="datapro_flex.worker")
    parser.add_argument("--module-path", required=True, help="absolute path to the flex module .py")
    parser.add_argument("--port", type=int, default=0, help="Flight bind port (0 = OS picks)")
    parser.add_argument("--host", default="127.0.0.1", help="Flight bind host")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    module = _load_module(args.module_path)

    location = f"grpc://{args.host}:{args.port}"
    server = _FlexServer(location, module, args.module_path)
    # Bind so .port becomes real (when port=0); the actual serve loop
    # runs in a daemon thread so we can also listen for SIGTERM.
    actual_port = server.port
    _emit_handshake(actual_port)
    log.info("flex worker listening on grpc://%s:%d for module %s",
             args.host, actual_port, args.module_path)

    def _on_signal(signum, _frame):
        log.info("received signal %d, shutting down", signum)
        server._stop_event.set()  # type: ignore[attr-defined]

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    serve_thread = threading.Thread(target=server.serve, name="flex-flight-serve", daemon=True)
    serve_thread.start()

    server.await_stop()
    log.info("draining and shutting down flex worker")
    server.shutdown()
    # Block until the gRPC stack has truly stopped — otherwise the
    # process can linger holding the port, and our parent's "did the
    # subprocess exit?" check times out.
    server.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
