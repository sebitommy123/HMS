import logging
import threading
import time

from flask import Flask, jsonify
from flask_cors import CORS

from datapro_core.api import catalogs as catalogs_api
from datapro_core.api import data_sources as data_sources_api
from datapro_core.api import flex_modules as flex_modules_api
from datapro_core.api import object_factories as object_factories_api
from datapro_core.api import object_types as object_types_api
from datapro_core.api import query as query_api
from datapro_core.api import raw_trino_query as raw_trino_query_api
from datapro_core.api import reconcile as reconcile_api
from datapro_core.api import traits as traits_api
from datapro_core.api import trino_state as trino_state_api
from datapro_core.config import Config
from datapro_core.db import make_engine, make_session_factory, ping as db_ping
from datapro_core.factory_validator import validate_all
from datapro_core.reconciler import reconcile
from datapro_core.trino_client import TrinoClient, TrinoError


log = logging.getLogger("datapro_core")


def create_app(
    config: Config | None = None,
    *,
    start_heartbeat: bool = False,
    run_startup_reconcile: bool = False,
) -> Flask:
    cfg = config or Config.from_env()
    app = Flask(__name__)
    app.config["DATAPRO"] = cfg

    # CORS — the SPA at the configured origin needs to call us in the browser.
    CORS(app, origins=list(cfg.cors_origins))

    engine = make_engine(cfg.database_url)
    app.extensions["db_engine"] = engine
    app.extensions["db_session"] = make_session_factory(engine)
    app.extensions["trino"] = TrinoClient(
        host=cfg.trino_host, port=cfg.trino_port, user=cfg.trino_user
    )

    @app.get("/health")
    def health():
        trino: TrinoClient = app.extensions["trino"]
        status = {
            "postgres": "reachable" if db_ping(engine) else "unreachable",
            "trino": "reachable" if trino.ping() else "unreachable",
        }
        overall_ok = all(v == "reachable" for v in status.values())
        return jsonify({"status": "ok" if overall_ok else "degraded", **status}), (
            200 if overall_ok else 503
        )

    app.register_blueprint(catalogs_api.bp)
    app.register_blueprint(object_types_api.bp)
    app.register_blueprint(data_sources_api.bp)
    app.register_blueprint(object_factories_api.bp)
    app.register_blueprint(reconcile_api.bp)
    app.register_blueprint(trino_state_api.bp)
    app.register_blueprint(raw_trino_query_api.bp)
    app.register_blueprint(query_api.bp)
    app.register_blueprint(traits_api.bp)
    app.register_blueprint(flex_modules_api.bp)

    if run_startup_reconcile:
        _safe_startup_reconcile(app)

    if start_heartbeat:
        _start_heartbeat(app, interval_seconds=60)

    return app


def _safe_startup_reconcile(app: Flask) -> None:
    session = app.extensions["db_session"]()
    trino: TrinoClient = app.extensions["trino"]
    try:
        with session:
            reconcile(session, trino)
        log.info("startup reconcile complete")
    except TrinoError as exc:
        log.warning("startup reconcile skipped: %s", exc)


def _start_heartbeat(app: Flask, interval_seconds: int) -> None:
    def loop():
        while True:
            time.sleep(interval_seconds)
            session = app.extensions["db_session"]()
            trino: TrinoClient = app.extensions["trino"]
            # Reconcile catalogs (Postgres → Trino convergence).
            try:
                with session:
                    reconcile(session, trino)
            except Exception as exc:
                log.warning("heartbeat reconcile failed: %s", exc)
            # Re-validate every object factory so the UI's status badges
            # reflect upstream changes (dropped tables, removed columns)
            # even when nobody's running queries.
            session = app.extensions["db_session"]()
            try:
                with session:
                    summary = validate_all(session=session, trino=trino)
                if summary["broken"]:
                    log.info(
                        "heartbeat factory validation: %d ok, %d broken",
                        summary["ok"],
                        summary["broken"],
                    )
            except Exception as exc:
                log.warning("heartbeat factory validation failed: %s", exc)

    t = threading.Thread(target=loop, daemon=True, name="reconcile-heartbeat")
    t.start()


app = create_app(run_startup_reconcile=True, start_heartbeat=True)
