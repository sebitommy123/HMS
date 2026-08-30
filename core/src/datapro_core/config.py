import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    database_url: str
    trino_host: str
    trino_port: int
    trino_user: str
    cors_origins: tuple[str, ...]
    # Flex module materialization paths. ``flex_modules_host_dir`` is where
    # Core writes <catalog>.py files on disk; ``flex_modules_container_dir``
    # is the path Trino sees the same files at (bind-mounted in
    # ``datapro/docker-compose.yml``). The two are equal when Core runs on
    # the host and Trino in the container — Core writes the host path,
    # Trino reads the container path; both point at the same bytes.
    flex_modules_host_dir: str
    flex_modules_container_dir: str

    @classmethod
    def from_env(cls) -> "Config":
        # CORS_ORIGINS is comma-separated. The default covers this checkout's
        # own UI dev server (UI_PORT comes from its stack slot — see
        # scripts/hms.py) plus vite's preview port. scripts/dev-up.sh sets
        # CORS_ORIGINS explicitly; in production, so should you.
        ui_port = os.environ.get("UI_PORT", "5003")
        default_origins = [
            f"http://{host}:{port}"
            for host in ("localhost", "127.0.0.1")
            for port in (ui_port, "4173")
        ]
        cors_raw = os.environ.get("CORS_ORIGINS", ",".join(default_origins))
        cors = tuple(o.strip() for o in cors_raw.split(",") if o.strip())
        # Default flex module host path: the bind mount in the dev
        # compose setup. Containerized deployments should override
        # FLEX_MODULES_HOST_DIR to wherever Core can write that the
        # Trino container reads.
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        default_host_dir = os.path.join(repo_root, "datapro", "flex-modules")
        # DATABASE_URL and TRINO_PORT are mandatory, and deliberately have no
        # fallback. Both are per-checkout now (every worktree runs its own
        # Postgres and Trino on its own ports — see scripts/hms.py), so any
        # baked-in default would be wrong for every checkout but one, and would
        # fail by quietly serving another worktree's data rather than by
        # erroring. scripts/dev-up.sh always sets them.
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL is required. Start via scripts/dev-up.sh, or set it "
                'explicitly — `eval "$(scripts/hms.py env)"` exports the right one '
                "for this checkout as CORE_DATABASE_URL."
            )
        trino_port = os.environ.get("TRINO_PORT")
        if not trino_port:
            raise RuntimeError(
                "TRINO_PORT is required — it differs per checkout. Start via "
                'scripts/dev-up.sh, or `eval "$(scripts/hms.py env)"`.'
            )

        return cls(
            database_url=database_url,
            trino_host=os.environ.get("TRINO_HOST", "localhost"),
            trino_port=int(trino_port),
            trino_user=os.environ.get("TRINO_USER", "datapro-core"),
            cors_origins=cors,
            flex_modules_host_dir=os.environ.get(
                "FLEX_MODULES_HOST_DIR", default_host_dir
            ),
            flex_modules_container_dir=os.environ.get(
                "FLEX_MODULES_CONTAINER_DIR", "/var/datapro-flex"
            ),
        )
