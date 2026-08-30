"""The flex module materializer must write WORLD-READABLE files.

The flex worker runs inside the Trino container as uid 1000 (trino); the
host-owned materialized .py bind-mounts as root. mkstemp forces mode 0600, so
without an explicit chmod the worker gets EACCES importing the module — a
Linux-only failure (Docker Desktop squashes ownership, so macOS misses it).
Pure unit test: no Trino needed."""

import os
import stat
import tempfile
from types import SimpleNamespace

from datapro_core import flex_module_materializer as materializer


def _cfg(host_dir: str):
    return SimpleNamespace(
        flex_modules_host_dir=host_dir,
        flex_modules_container_dir="/var/datapro-flex",
    )


def test_materialized_module_is_world_readable():
    path = materializer.write(_cfg(tempfile.mkdtemp()), "demo", "x = 1\n")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o644, f"expected 0o644 so the in-container worker can read it, got {oct(mode)}"


def test_overwrite_keeps_world_readable():
    cfg = _cfg(tempfile.mkdtemp())
    materializer.write(cfg, "demo", "x = 1\n")
    path = materializer.write(cfg, "demo", "x = 2\n")  # atomic replace
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644
    assert path.read_text() == "x = 2\n"
