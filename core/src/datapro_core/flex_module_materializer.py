"""Materialize a flex module's source text to disk where the Trino
container can read it.

Single stable path per catalog: ``<host_dir>/<catalog_name>.py``.
Writes are atomic (tempfile + ``os.replace``) so the Trino-side worker
never sees a partial file. The container-visible path is derived from
the same catalog name + ``flex_modules_container_dir``.

No content hashing, no versioning at the filesystem level — the
mtime-based reload on the Java connector side picks up changes
automatically (see ``FlexPythonWorker`` in the flex/connector project).
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from datapro_core.config import Config


# Catalog names are validated upstream (alphanumerics, underscores,
# hyphens — see ``CatalogCreateRequest`` in ``schemas.py``). Belt and
# braces: refuse anything that could turn into a directory escape.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def _ensure_safe(catalog_name: str) -> None:
    if not _SAFE_NAME.fullmatch(catalog_name):
        raise ValueError(
            f"unsafe catalog name for flex module path: {catalog_name!r}"
        )


def host_path_for(config: Config, catalog_name: str) -> Path:
    """Where Core writes the module file on its own filesystem."""
    _ensure_safe(catalog_name)
    return Path(config.flex_modules_host_dir) / f"{catalog_name}.py"


def container_path_for(config: Config, catalog_name: str) -> str:
    """Where the Trino container sees the same bytes. This is the value
    that ends up in the catalog's ``flex.module_path`` property."""
    _ensure_safe(catalog_name)
    return f"{config.flex_modules_container_dir.rstrip('/')}/{catalog_name}.py"


def write(config: Config, catalog_name: str, source_text: str) -> Path:
    """Atomically write ``source_text`` to the catalog's stable path.

    Returns the host path. Creates the parent directory if needed so
    the first-ever write doesn't error on a missing tree.
    """
    target = host_path_for(config, catalog_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    # tempfile in the same directory so os.replace is a same-fs rename
    # (atomic POSIX). Different-fs renames would copy and that's no
    # longer atomic.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{catalog_name}.", suffix=".py.tmp", dir=str(target.parent)
    )
    try:
        # mkstemp forces mode 0600. The flex worker runs INSIDE the Trino
        # container as uid 1000 (trino), while this host-owned file bind-mounts
        # as root — so at 0600 the worker gets EACCES importing the module. Make
        # it world-readable (it's non-secret Python source). On macOS this is a
        # harmless no-op (Docker Desktop squashes ownership); on Linux it's the
        # difference between the flex catalog working and not.
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(source_text)
        os.replace(tmp_name, target)
    except Exception:
        # Best-effort cleanup if we didn't make it to the replace step.
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return target


def delete(config: Config, catalog_name: str) -> None:
    """Remove the materialized file. No-op if it doesn't exist
    (catalog DROP after a failed write shouldn't error)."""
    target = host_path_for(config, catalog_name)
    try:
        target.unlink()
    except FileNotFoundError:
        pass
