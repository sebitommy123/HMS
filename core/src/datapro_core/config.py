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
        # CORS_ORIGINS is comma-separated. Defaults cover the common local-dev
        # ports vite picks: 5173 (dev) and 4173 (preview), plus 5174-5176/4174-4176
        # in case another vite project on the same machine already grabbed the
        # primary port. In production, set CORS_ORIGINS explicitly.
        default_origins = [
            f"http://{host}:{port}"
            for host in ("localhost", "127.0.0.1")
            for port in (5173, 5174, 5175, 5176, 4173, 4174, 4175, 4176)
        ]
        cors_raw = os.environ.get("CORS_ORIGINS", ",".join(default_origins))
        cors = tuple(o.strip() for o in cors_raw.split(",") if o.strip())
        # Default flex module host path: the bind mount in the dev
        # compose setup. Containerized deployments should override
        # FLEX_MODULES_HOST_DIR to wherever Core can write that the
        # Trino container reads.
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        default_host_dir = os.path.join(repo_root, "datapro", "flex-modules")
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql+psycopg://datapro:datapro@localhost:5433/datapro_core",
            ),
            trino_host=os.environ.get("TRINO_HOST", "localhost"),
            trino_port=int(os.environ.get("TRINO_PORT", "8080")),
            trino_user=os.environ.get("TRINO_USER", "datapro-core"),
            cors_origins=cors,
            flex_modules_host_dir=os.environ.get(
                "FLEX_MODULES_HOST_DIR", default_host_dir
            ),
            flex_modules_container_dir=os.environ.get(
                "FLEX_MODULES_CONTAINER_DIR", "/var/datapro-flex"
            ),
        )
