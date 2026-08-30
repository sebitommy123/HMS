#!/usr/bin/env python3
"""HMS dev tooling — one CLI for the whole local stack.

Every checkout of this repo (the main clone and each worktree) owns a *slot*:
a slug plus a base port. Everything that could collide between checkouts —
compose project names, container names, published ports, the Trino image tag,
databases, pid files — is derived from that slot, so any number of worktrees
can run complete stacks side by side without touching each other.

    scripts/hms.py up          bring this checkout's stack up
    scripts/hms.py ls          every stack on this machine
    scripts/hms.py --help      the rest

The scripts/*.sh entry points are one-line shims onto these subcommands.

Stdlib only, and 3.9-compatible on purpose: this has to run on a fresh clone
before any venv exists — it is the thing that *builds* the environment, so it
can't depend on one.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent
HMS_ROOT = SCRIPTS_DIR.parent

# Machine-wide slot registry. Lives outside the repo so allocating a slot can
# see which ports other checkouts have claimed even when they're stopped.
REGISTRY = Path(os.environ.get("HMS_REGISTRY", Path.home() / ".hms" / "stacks"))

# This checkout's slot, and where dev-up records pids and logs.
STACK_FILE = HMS_ROOT / ".hms-stack"
RUN_DIR = HMS_ROOT / ".dev-logs"

# The main clone takes 5000; worktrees get 5100, 5200, ... A slot uses
# base+1 .. base+6 (core, ai, ui, trino, core-pg, ai-pg).
MAIN_BASE = 5000
BASE_FIRST = 5100
BASE_STEP = 100
BASE_LAST = 6900
PORT_OFFSETS = {
    "CORE_PORT": 1,
    "AI_PORT": 2,
    "UI_PORT": 3,
    "TRINO_PORT": 4,
    "CORE_PG_PORT": 5,
    "AI_PG_PORT": 6,
}

FLEX_JAR = HMS_ROOT / "flex" / "connector" / "target" / "trino-flex-connector-0.1.0.jar"
MAVEN_IMAGE = "docker.io/library/maven:3.9-eclipse-temurin-25-alpine"

# Local-only (gitignored) files carried into each new worktree. Everything else
# — venvs, node_modules, the flex JAR — is rebuilt by `up`, and uv/pnpm share
# caches across checkouts so that stays quick.
WORKTREE_LOCAL_FILES = [
    "ai/key.sh",  # Anthropic API key (required for AI)
    "flex/connector/maven-settings.xml",  # internal Maven mirror, if configured
    "datapro/docker-compose.override.yml",  # host-specific compose tweaks
    ".claude/settings.local.json",  # local Claude Code settings
]

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
# All three go to stderr so a command's stdout stays machine-readable —
# `hms.py env` is meant to be eval'd, and slot allocation can log on first run.

_COLOR = sys.stderr.isatty() and os.environ.get("NO_COLOR") is None


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def log(msg: str) -> None:
    print(f"{_paint('1;34', '==>')} {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"{_paint('1;33', '!!')}  {msg}", file=sys.stderr)


class Die(Exception):
    """Fatal, expected error — reported without a traceback."""


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------


class Slot:
    def __init__(self, stack: str, port_base: int, path: Path):
        self.stack = stack
        self.port_base = int(port_base)
        self.path = Path(path)

    @property
    def registry_file(self) -> Path:
        return REGISTRY / f"{self.stack}.json"

    def register(self) -> None:
        REGISTRY.mkdir(parents=True, exist_ok=True)
        self.registry_file.write_text(
            json.dumps(
                {"stack": self.stack, "port_base": self.port_base, "path": str(self.path)},
                indent=2,
            )
            + "\n"
        )

    def port(self, name: str) -> int:
        return self.port_base + PORT_OFFSETS[name]

    def __repr__(self) -> str:
        return f"Slot({self.stack!r}, {self.port_base}, {str(self.path)!r})"


def _read_registry_entry(path: Path) -> Optional[Slot]:
    try:
        data = json.loads(path.read_text())
        return Slot(data["stack"], data["port_base"], data["path"])
    except (OSError, ValueError, KeyError):
        return None


def registry_slots(prune: bool = True) -> List[Slot]:
    """Every slot on this machine, ordered by port block.

    A slot whose checkout has been deleted is only forgotten once it owns
    nothing — no containers, no volumes, no image. Forgetting it any earlier
    would strand exactly what we most need to clean up: the registry entry is
    the only record of the slug, and the slug is how `rm` finds the resources.
    So a deleted checkout with resources left is kept and reported as an
    orphan, and only a genuinely empty one is pruned.

    If Docker can't be reached we can't tell the difference, so nothing is
    pruned — holding a port block is cheap, losing a stack is not.
    """
    if not REGISTRY.is_dir():
        return []
    env = docker_env()
    can_check = docker_available(env)
    slots = []
    for f in sorted(REGISTRY.glob("*.json")):
        slot = _read_registry_entry(f)
        if slot is None:
            continue
        if prune and not slot.path.is_dir() and can_check and not stack_resources(slot.stack, env):
            f.unlink(missing_ok=True)
            continue
        slots.append(slot)
    return sorted(slots, key=lambda s: s.port_base)


def discovered_slugs(env: Dict[str, str]) -> List[str]:
    """Stack slugs Docker knows about, from compose project labels.

    The registry is the plan; this is the reality. Anything here that isn't in
    the registry is a stack whose slot record was lost (a `rm -rf` of a
    worktree, a cleared ~/.hms) and which nothing would otherwise clean up.
    """
    slugs = set()
    for kind in ("container", "volume", "network"):
        cmd = {"container": ["ps", "-a"], "volume": ["volume", "ls"], "network": ["network", "ls"]}[kind]
        for line in docker_out([*cmd, "--format", "{{.Labels}}"], env):
            for part in line.split(","):
                if part.startswith("com.docker.compose.project="):
                    project = part.split("=", 1)[1]
                    match = re.fullmatch(r"hms-(.+)-(?:datapro|core|ai)", project)
                    if match:
                        slugs.add(match.group(1))
    return sorted(slugs)


def stack_resources(slug: str, env: Dict[str, str]) -> Dict[str, List[str]]:
    """Every Docker object belonging to a stack, keyed by kind. Empty dict when
    the stack owns nothing. Looked up by compose label rather than by name, so
    it works whether or not the checkout that created it still exists."""
    found: Dict[str, List[str]] = {}
    for project in (f"hms-{slug}-datapro", f"hms-{slug}-core", f"hms-{slug}-ai"):
        label = f"label=com.docker.compose.project={project}"
        for kind, cmd in (
            ("container", ["ps", "-aq"]),
            ("volume", ["volume", "ls", "-q"]),
            ("network", ["network", "ls", "-q"]),
        ):
            ids = docker_out([*cmd, "--filter", label], env)
            if ids:
                found.setdefault(kind, []).extend(ids)
    image = f"hms-datapro/trino-flex:{slug}"
    if docker_out(["image", "ls", "-q", image], env):
        found["image"] = [image]
    return found


def _port_free(port: int) -> bool:
    """True when nothing is listening on 127.0.0.1:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _base_free(base: int) -> bool:
    return all(_port_free(base + off) for off in PORT_OFFSETS.values())


def _derive_slug(taken: Dict[str, Path]) -> str:
    """`main` for the primary clone (where .git is a real directory),
    `wt<first 6 alnum of the directory name>` for a worktree. Gains a counter
    on the rare collision with an unrelated checkout."""
    if (HMS_ROOT / ".git").is_dir():
        base = "main"
    else:
        base = "wt" + re.sub(r"[^a-zA-Z0-9]", "", HMS_ROOT.name)[:6]
    slug, n = base, 1
    while slug in taken and taken[slug] != HMS_ROOT:
        n += 1
        slug = f"{base}{n}"
    return slug


class _RegistryLock:
    """Serialize allocation so two worktrees bootstrapping at once can't claim
    the same block. A directory is the portable atomic-create primitive."""

    STALE_AFTER = 60  # seconds

    def __init__(self) -> None:
        self.path = REGISTRY / ".lock"

    def __enter__(self) -> "_RegistryLock":
        REGISTRY.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + 30
        while True:
            try:
                self.path.mkdir()
                return self
            except FileExistsError:
                # Break a lock left behind by a killed process.
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > self.STALE_AFTER:
                        self.path.rmdir()
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise Die(f"Timed out on the slot registry lock ({self.path}). Remove it if it's stale.")
                time.sleep(0.2)

    def __exit__(self, *exc: object) -> None:
        try:
            self.path.rmdir()
        except OSError:
            pass


def allocate_slot() -> Slot:
    with _RegistryLock():
        existing = registry_slots()
        taken_slugs = {s.stack: s.path for s in existing}
        claimed_bases = {s.port_base for s in existing if s.path != HMS_ROOT}

        slug = _derive_slug(taken_slugs)

        # The main clone prefers 5000 so its ports stay predictable. Everyone
        # else takes the first block no other checkout claims that is also
        # actually free on the host right now.
        base = None
        if slug == "main" and MAIN_BASE not in claimed_bases:
            base = MAIN_BASE
        else:
            for candidate in range(BASE_FIRST, BASE_LAST + 1, BASE_STEP):
                if candidate not in claimed_bases and _base_free(candidate):
                    base = candidate
                    break
        if base is None:
            raise Die(
                f"No free port block in {BASE_FIRST}-{BASE_LAST}. "
                "Free one with `scripts/hms.py rm <slug>` (`scripts/hms.py ls` lists them)."
            )

        slot = Slot(slug, base, HMS_ROOT)
        STACK_FILE.write_text(json.dumps({"stack": slug, "port_base": base}, indent=2) + "\n")
        slot.register()

    log(f"Allocated stack slot '{slug}' on port base {base}.")
    return slot


def current_slot() -> Slot:
    """This checkout's slot, allocating one on first use.

    HMS_STACK + HMS_PORT_BASE in the environment bypass allocation entirely,
    which is how `rm` acts on another checkout's slot.
    """
    env_stack, env_base = os.environ.get("HMS_STACK"), os.environ.get("HMS_PORT_BASE")
    if env_stack and env_base:
        return Slot(env_stack, int(env_base), HMS_ROOT)

    if STACK_FILE.is_file():
        try:
            data = json.loads(STACK_FILE.read_text())
            slot = Slot(data["stack"], data["port_base"], HMS_ROOT)
        except (ValueError, KeyError):
            raise Die(f"{STACK_FILE} is corrupt. Delete it and re-run to get a fresh slot.")
        # Re-register if the registry lost us (fresh machine, cleared ~/.hms).
        if not slot.registry_file.is_file():
            slot.register()
        return slot

    return allocate_slot()


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def _docker_host() -> Optional[str]:
    """The right socket for anything shelling out to docker (testcontainers
    included): colima's on macOS, rootless Docker's on Linux. Only adopted if
    the socket actually exists, so hosts running rootful Docker are untouched.
    """
    if os.environ.get("DOCKER_HOST"):
        return os.environ["DOCKER_HOST"]
    if sys.platform == "darwin":
        sock = Path.home() / ".colima" / "default" / "docker.sock"
    else:
        runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        sock = Path(runtime) / "docker.sock"
    return f"unix://{sock}" if sock.is_socket() else None


def _os_compose_overlay() -> Optional[str]:
    """The datapro stack needs per-OS host bind mounts for flex modules (the
    flex Python worker runs inside the Trino container and can only read files
    mounted in). The base compose file is host-agnostic; the mounts live in an
    OS-specific overlay layered on with -f."""
    name = {"darwin": "docker-compose.macos.yml", "linux": "docker-compose.linux.yml"}.get(sys.platform)
    if name and (HMS_ROOT / "datapro" / name).is_file():
        return name
    return None


def _anthropic_key() -> str:
    """Pick up the key from the local-only ai/key.sh if the shell hasn't set
    it. Sourced with bash rather than parsed, since it's a shell file and may
    legitimately shell out (a password-manager read, say)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    key_sh = HMS_ROOT / "ai" / "key.sh"
    if not key_sh.is_file():
        return ""
    try:
        out = subprocess.run(
            ["bash", "-c", f'set -a; source {shlex.quote(str(key_sh))}; printf %s "${{ANTHROPIC_API_KEY:-}}"'],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def stack_env(slot: Slot) -> Dict[str, str]:
    """The full derived environment for a slot. Every value stays individually
    overridable from the real environment, for one-off situations."""

    def pick(name: str, value: object) -> str:
        return os.environ.get(name) or str(value)

    env: Dict[str, str] = {
        "HMS_ROOT": str(HMS_ROOT),
        "HMS_STACK": slot.stack,
        "HMS_PORT_BASE": str(slot.port_base),
    }
    for name in PORT_OFFSETS:
        env[name] = pick(name, slot.port(name))

    env["CORE_URL"] = pick("CORE_URL", f"http://127.0.0.1:{env['CORE_PORT']}")
    env["AI_URL"] = pick("AI_URL", f"http://127.0.0.1:{env['AI_PORT']}")

    # Per-slot image tag: two worktrees building different flex code must not
    # clobber each other's Trino image. core/tests/conftest.py reads this too.
    env["TRINO_IMAGE"] = pick("TRINO_IMAGE", f"hms-datapro/trino-flex:{slot.stack}")

    # Connection strings for the host-run Core and AI processes, passed
    # explicitly when launching them (and to alembic). The fallbacks baked into
    # the service configs would otherwise point every checkout at one database.
    env["CORE_DATABASE_URL"] = (
        f"postgresql+psycopg://datapro:datapro@127.0.0.1:{env['CORE_PG_PORT']}/datapro_core"
    )
    env["AI_DATABASE_URL"] = (
        f"postgresql+psycopg://datapro:datapro@127.0.0.1:{env['AI_PG_PORT']}/datapro_ai"
    )

    # Only this slot's UI dev server may call this slot's Core/AI.
    env["CORS_ORIGINS"] = pick(
        "CORS_ORIGINS", f"http://localhost:{env['UI_PORT']},http://127.0.0.1:{env['UI_PORT']}"
    )

    # Compose project names — one per compose file, all carrying the slug, so
    # containers, networks and named volumes are namespaced per checkout.
    env["DATAPRO_PROJECT"] = f"hms-{slot.stack}-datapro"
    env["CORE_PROJECT"] = f"hms-{slot.stack}-core"
    env["AI_PROJECT"] = f"hms-{slot.stack}-ai"

    docker_host = _docker_host()
    if docker_host:
        env["DOCKER_HOST"] = docker_host

    # Ryuk (testcontainers' cleanup sidecar) doesn't behave under colima, so we
    # disable it and sweep leaked containers ourselves — see `sweep`.
    env["TESTCONTAINERS_RYUK_DISABLED"] = pick("TESTCONTAINERS_RYUK_DISABLED", "true")

    key = _anthropic_key()
    if key:
        env["ANTHROPIC_API_KEY"] = key
    return env


def full_env(slot: Slot) -> Dict[str, str]:
    """The process environment plus the slot's, for handing to subprocesses."""
    env = dict(os.environ)
    env.update(stack_env(slot))
    # uv isn't always on PATH in non-login shells.
    local_bin = Path.home() / ".local" / "bin"
    if not shutil.which("uv") and (local_bin / "uv").exists():
        env["PATH"] = f"{local_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def run(cmd: Sequence[str], env: Dict[str, str], cwd: Optional[Path] = None, check: bool = True,
        capture: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(
        list(cmd), cwd=str(cwd or HMS_ROOT), env=env, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and result.returncode != 0:
        if capture and result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        raise Die(f"Command failed ({result.returncode}): {' '.join(shlex.quote(c) for c in cmd)}")
    return result


def docker_env() -> Dict[str, str]:
    """Enough environment to talk to Docker, without needing a slot. Used by
    slot allocation, which has to look at Docker before a slot exists."""
    env = dict(os.environ)
    host = _docker_host()
    if host:
        env["DOCKER_HOST"] = host
    return env


def docker_available(env: Dict[str, str]) -> bool:
    return run(["docker", "info"], env, check=False, capture=True).returncode == 0


def require_docker(env: Dict[str, str]) -> None:
    if not docker_available(env):
        raise Die(
            f"Docker not reachable at DOCKER_HOST={env.get('DOCKER_HOST', 'default')}. "
            "Start colima with: colima start"
        )


def docker_out(args: Sequence[str], env: Dict[str, str]) -> List[str]:
    """Run a read-only docker query, returning non-empty output lines."""
    result = run(["docker", *args], env, check=False, capture=True)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def compose(kind: str, args: Sequence[str], env: Dict[str, str], check: bool = True) -> None:
    """Run docker compose for one of this slot's three projects.

    Always project-scoped with -p, so a command in one checkout can never
    adopt, recreate or tear down another checkout's containers.
    """
    if kind == "datapro":
        files = ["-f", "docker-compose.yml"]
        overlay = _os_compose_overlay()
        if overlay:
            files += ["-f", overlay]
        else:
            warn(f"No host-mount overlay for {sys.platform} — flex modules can't read host files.")
        project = env["DATAPRO_PROJECT"]
    else:
        files = []
        project = env["CORE_PROJECT"] if kind == "core" else env["AI_PROJECT"]
    run(["docker", "compose", "-p", project, *files, *args], env, cwd=HMS_ROOT / kind, check=check)


def wait_for_http(url: str, timeout: int = 30) -> None:
    """Wait until url answers 2xx or 503. 503 is acceptable — it means the
    service is up but a dependency is down, which the status bar surfaces."""
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    last = "no response"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if 200 <= resp.status < 300:
                    return
                last = str(resp.status)
        except urllib.error.HTTPError as exc:
            if exc.code == 503:
                return
            last = str(exc.code)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = type(exc).__name__
        if time.time() > deadline:
            raise Die(f"Timed out waiting for {url} (last: {last})")
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Host-run services (Core, AI)
# ---------------------------------------------------------------------------
# Tracked by pid file rather than a `pgrep -f datapro_core.app` pattern: that
# pattern matches every checkout's process, so it would make the second
# worktree think Core was already up and let its `down` kill the first one's.

SERVICES = {"core": "datapro_core.app", "ai": "datapro_ai.app"}

# What a process must look like before we'll kill it during reconciliation,
# per port. The pid file is the primary record; this is the safety net for when
# it's gone (deleted .dev-logs, a kill -9'd `up`, a machine that slept through a
# crash). Nothing is killed unless its command line contains the marker, so an
# unrelated process that happens to hold the port is reported, never touched.
PORT_OWNERS = {
    "CORE_PORT": ("core", "datapro_core.app"),
    "AI_PORT": ("ai", "datapro_ai.app"),
    "UI_PORT": ("ui dev server", "vite"),
}


def _pidfile(name: str) -> Path:
    return RUN_DIR / f"{name}.pid"


def process_command(pid: int) -> str:
    result = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    return result.stdout.strip()


def _pgid(pid: int) -> Optional[int]:
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def listeners_on(port: int) -> List[int]:
    """Pids listening on a TCP port. Empty if lsof isn't available — the port
    probe still tells us *something* is there, we just can't name it."""
    result = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True
    )
    if result.returncode != 0:
        return []
    return [int(line) for line in result.stdout.split() if line.strip().isdigit()]


def untracked_processes(ports: Dict[str, int], root: Path = HMS_ROOT) -> List[Dict[str, object]]:
    """Processes squatting on a slot's ports that no pid file accounts for.

    These are the true leftovers: a Core, AI or vite that outlived the command
    which started it. Each entry says whether we recognise it well enough to
    kill it. `ports` maps PORT_OWNERS keys to port numbers, and `root` is the
    checkout whose pid files say what's already accounted for — both explicit
    so this works for any slot, not just the one we're standing in.
    """
    tracked = {pid for pid in (service_pid(n, root) for n in SERVICES) if pid}
    found = []
    for port_key, (label, marker) in PORT_OWNERS.items():
        port = ports[port_key]
        for pid in listeners_on(port):
            # The pid we record is `uv run`, which spawns flask as a child — so
            # the process actually holding the port is usually not the recorded
            # one. They share a process group (start_new_session), so compare
            # against that, or every healthy service looks untracked.
            if pid in tracked or _pgid(pid) in tracked:
                continue
            command = process_command(pid)
            found.append({
                "pid": pid,
                "port": port,
                "label": label,
                "command": command,
                "ours": marker in command,
            })
    return found


def kill_pid(pid: int) -> None:
    """Terminate a process and the group it leads, escalating if it lingers."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except OSError:
            try:
                os.kill(pid, sig)
            except OSError:
                return
        for _ in range(40):
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.05)


def service_pid(name: str, root: Path = HMS_ROOT) -> Optional[int]:
    """The live pid for a service in `root`, or None. Clears stale pid files.

    The recorded module name is checked against the running process, so a
    recycled pid can never be mistaken for ours (and killed).
    """
    f = root / ".dev-logs" / f"{name}.pid"
    try:
        record = json.loads(f.read_text())
        pid, module = int(record["pid"]), record["module"]
    except (OSError, ValueError, KeyError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        f.unlink(missing_ok=True)
        return None
    ps = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    if module not in ps.stdout:
        f.unlink(missing_ok=True)
        return None
    return pid


def start_service(name: str, port: int, env: Dict[str, str], extra: Dict[str, str]) -> None:
    module = SERVICES[name]
    existing = service_pid(name)
    if existing:
        warn(f"{name} is already running for stack '{env['HMS_STACK']}' (pid {existing}). Skipping launch.")
        return
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    log(f"Starting {name} on port {port}...")
    child_env = dict(env)
    child_env.update(extra)
    logfile = open(RUN_DIR / f"{name}.log", "ab")
    # start_new_session puts the child in its own process group, so stopping it
    # takes uv *and* the flask process it spawns down without ever signalling
    # this process — and it survives this command exiting.
    proc = subprocess.Popen(
        ["uv", "run", "flask", "--app", module, "run", "--host", "0.0.0.0", "--port", str(port)],
        cwd=str(HMS_ROOT / name),
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=logfile,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _pidfile(name).write_text(json.dumps({"pid": proc.pid, "module": module}) + "\n")


def stop_service(name: str, root: Path = HMS_ROOT) -> bool:
    pid = service_pid(name, root)
    if pid is None:
        return False
    log(f"Stopping {name} (pid {pid})...")
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    # Give it a moment to release its port, then insist.
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.05)
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
    _pidfile(name).unlink(missing_ok=True)
    return True


# ---------------------------------------------------------------------------
# Machine-wide operations
# ---------------------------------------------------------------------------
# Everything else in this file is scoped to one checkout on purpose. These are
# the deliberate exceptions — for when you want the machine quiet, whatever any
# other worktree is in the middle of. They always say what they're about to
# touch, and always ask first.


def port_map(env: Dict[str, str]) -> Dict[str, int]:
    """The PORT_OWNERS ports for the current environment, as ints."""
    return {key: int(env[key]) for key in PORT_OWNERS}


def slot_ports(slot: Slot) -> Dict[str, int]:
    """A slot's ports straight from its port base. Deliberately not read from
    the environment: for another checkout's slot, the registry is the truth."""
    return {key: slot.port_base + off for key, off in PORT_OFFSETS.items()}


def all_slots(env: Dict[str, str]) -> List[Slot]:
    """Every stack on this machine — registered slots plus any that only Docker
    still knows about. Nothing is pruned; this is for acting, not tidying."""
    slots = registry_slots(prune=False)
    known = {s.stack for s in slots}
    if docker_available(env):
        for slug in discovered_slugs(env):
            if slug not in known:
                slots.append(Slot(slug, 0, Path("(no slot record)")))
    return slots


def stop_everything(env: Dict[str, str], slots: Sequence[Slot], containers: bool) -> int:
    """Stop every slot's host processes, and optionally its containers. Returns
    how many things were stopped. Volumes and slot records are left alone."""
    stopped = 0
    for slot in slots:
        for name in ("ai", "core"):
            if slot.path.is_dir() and stop_service(name, slot.path):
                stopped += 1
        if slot.port_base:
            for proc in untracked_processes(slot_ports(slot), slot.path):
                if proc["ours"]:
                    log(f"[{slot.stack}] stopping untracked {proc['label']} "
                        f"on port {proc['port']} (pid {proc['pid']})...")
                    kill_pid(int(proc["pid"]))  # type: ignore[arg-type]
                    stopped += 1
                else:
                    warn(f"[{slot.stack}] port {proc['port']} held by pid {proc['pid']}, "
                         f"not ours — left alone: {proc['command']}")
        if containers:
            ids = stack_resources(slot.stack, env).get("container", [])
            running = [c for c in ids if docker_out(["ps", "-q", "--filter", f"id={c}"], env)]
            if running:
                log(f"[{slot.stack}] stopping {len(running)} container(s)...")
                run(["docker", "stop", *running], env, check=False, capture=True)
                stopped += len(running)
    return stopped


def describe_slots(slots: Sequence[Slot], env: Dict[str, str]) -> None:
    for slot in slots:
        here = " (this checkout)" if slot.path == HMS_ROOT else ""
        counts = stack_resources(slot.stack, env)
        summary = ", ".join(f"{len(v)} {k}s" for k, v in counts.items()) or "no containers"
        print(f"  {slot.stack:<12} {summary:<40} {slot.path}{here}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


# Held back from `env` output by default: this command's whole point is to be
# read, pasted and eval'd, and a key that lands in a terminal scrollback or a
# pasted snippet is a key you have to rotate. Subprocesses still get it — it's
# in full_env() — so only ad-hoc compose runs of the containerized `ai` service
# need --with-secrets.
SECRET_KEYS = {"ANTHROPIC_API_KEY"}


def cmd_env(args: argparse.Namespace) -> int:
    env = stack_env(current_slot())
    withheld = sorted(SECRET_KEYS & set(env)) if not args.with_secrets else []
    for key in withheld:
        del env[key]

    if args.json:
        print(json.dumps(env, indent=2))
    else:
        for key, value in env.items():
            print(f"export {key}={shlex.quote(value)}")
    if withheld:
        warn(f"Withheld from output: {', '.join(withheld)}. Pass --with-secrets if you need it.")
    return 0


def build_flex_jar(env: Dict[str, str]) -> None:
    """Build the flex Trino plugin JAR if missing or stale. Maven-in-Docker
    avoids host-level Java + Maven prereqs. Cheap when the .m2 cache is warm (a
    couple of seconds); the first ever build is slow (~30s)."""
    if FLEX_JAR.is_file():
        src = HMS_ROOT / "flex" / "connector" / "src"
        pom = HMS_ROOT / "flex" / "connector" / "pom.xml"
        jar_mtime = FLEX_JAR.stat().st_mtime
        newer = [p for p in list(src.rglob("*")) + [pom] if p.is_file() and p.stat().st_mtime > jar_mtime]
        if not newer:
            return

    log("Building flex connector JAR...")
    connector = HMS_ROOT / "flex" / "connector"
    # Mount a local maven-settings.xml if the user created one (internal mirror
    # config for networks where Maven Central is blocked). Absent → Central.
    settings = connector / "maven-settings.xml"
    mounts = ["-v", f"{connector}:/work", "-v", f"{connector / '.m2'}:/root/.m2"]
    if settings.is_file():
        mounts += ["-v", f"{settings}:/root/.m2/settings.xml:ro"]
    run(
        ["docker", "run", "--rm", *mounts, "-w", "/work", MAVEN_IMAGE,
         "mvn", "-B", "-DskipTests", "package"],
        env, cwd=connector, capture=True,
    )


def cmd_up(args: argparse.Namespace) -> int:
    """Start the full local dev stack: Trino, both Postgres DBs, Core, AI.

    Idempotent, and safe to run concurrently from any number of worktrees —
    each gets its own containers, volumes, ports and databases. Doesn't start
    the UI dev server; run `hms.py ui` in a separate terminal so its log isn't
    tangled with Core/AI output.

    Order matters: Core needs Trino + Postgres, AI needs Core + its own
    Postgres. Each step waits on /health before moving on.
    """
    slot = current_slot()
    env = full_env(slot)
    require_docker(env)

    log(
        f"Stack '{slot.stack}' — core:{env['CORE_PORT']} ai:{env['AI_PORT']} "
        f"ui:{env['UI_PORT']} trino:{env['TRINO_PORT']} "
        f"pg:{env['CORE_PG_PORT']}/{env['AI_PG_PORT']}"
    )

    build_flex_jar(env)

    log("Bringing up the Trino container...")
    # --build rebuilds the custom trino-flex image whenever the Dockerfile, the
    # Python runtime or the flex JAR changed. Docker's layer cache makes the
    # common case (no changes) near-instant.
    compose("datapro", ["up", "-d", "--build"], env)

    log("Waiting for Trino to be reachable...")
    wait_for_http(f"http://127.0.0.1:{env['TRINO_PORT']}/v1/info", 120)

    # Core and AI each own a `postgres` service in their own compose file (the
    # `core`/`ai` services there are the containerized deploy path — we run
    # those on the host instead, so only start `postgres`). --wait blocks until
    # the healthcheck passes so the alembic steps don't race a cold DB.
    log("Bringing up Core's Postgres...")
    compose("core", ["up", "-d", "--wait", "postgres"], env)

    log("Running Core Alembic migrations...")
    run(["uv", "run", "alembic", "upgrade", "head"],
        {**env, "DATABASE_URL": env["CORE_DATABASE_URL"]}, cwd=HMS_ROOT / "core")

    start_service("core", int(env["CORE_PORT"]), env, {
        "DATABASE_URL": env["CORE_DATABASE_URL"],
        "TRINO_HOST": "127.0.0.1",
        "TRINO_PORT": env["TRINO_PORT"],
        "CORS_ORIGINS": env["CORS_ORIGINS"],
    })

    log("Waiting for Core /health...")
    wait_for_http(f"{env['CORE_URL']}/health", 30)

    log("Bringing up AI's Postgres...")
    compose("ai", ["up", "-d", "--wait", "postgres"], env)

    log("Running AI Alembic migrations...")
    run(["uv", "run", "alembic", "upgrade", "head"],
        {**env, "DATABASE_URL": env["AI_DATABASE_URL"]}, cwd=HMS_ROOT / "ai")

    if not env.get("ANTHROPIC_API_KEY"):
        warn("ANTHROPIC_API_KEY not set — AI will return 503 from /messages endpoints.")
        warn("  Set it in your shell, or put it in ai/key.sh.")

    start_service("ai", int(env["AI_PORT"]), env, {
        "DATABASE_URL": env["AI_DATABASE_URL"],
        "CORE_URL": env["CORE_URL"],
        "CORS_ORIGINS": env["CORS_ORIGINS"],
        "ANTHROPIC_API_KEY": env.get("ANTHROPIC_API_KEY", ""),
    })

    log("Waiting for AI /health...")
    wait_for_http(f"{env['AI_URL']}/health", 30)

    log(f"Stack '{slot.stack}' is up:")
    print(f"  Core:  {env['CORE_URL']}/health", file=sys.stderr)
    print(f"  AI:    {env['AI_URL']}/health", file=sys.stderr)
    print(f"  Trino: http://127.0.0.1:{env['TRINO_PORT']}/v1/info", file=sys.stderr)
    print(f"  UI:    scripts/ui-dev.sh in another terminal (port {env['UI_PORT']})", file=sys.stderr)
    print(f"  Logs:  tail -f {RUN_DIR}/{{core,ai}}.log", file=sys.stderr)
    print("  Other stacks: scripts/hms.py ls", file=sys.stderr)
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    """Stop this checkout's Core + AI. Other checkouts are untouched.

    Containers are left running by default — they cost little idle and are slow
    to restart. --containers stops this stack's too; `rm` destroys them.
    """
    slot = current_slot()
    env = full_env(slot)

    if args.all:
        require_docker(env)
        slots = all_slots(env)
        if not slots:
            log("No stacks on this machine.")
            return 0
        log("Will stop every stack on this machine — including other worktrees':")
        describe_slots(slots, env)
        print("  (databases and slots are kept — `rm --all` destroys those)", file=sys.stderr)
        _confirm("Stop all of them?", args.yes)
        count = stop_everything(env, slots, containers=True)
        log(f"Stopped {count} thing(s) across {len(slots)} stack(s).")
        return 0

    stopped = [name for name in ("ai", "core") if stop_service(name)]

    # Anything still holding this slot's ports outlived its pid file. Clean it
    # up here rather than leaving the next `up` to fail on a bound port.
    for proc in untracked_processes(port_map(env)):
        if proc["ours"]:
            log(f"Stopping untracked {proc['label']} on port {proc['port']} (pid {proc['pid']})...")
            kill_pid(int(proc["pid"]))  # type: ignore[arg-type]
            stopped.append(str(proc["label"]))
        else:
            warn(f"Port {proc['port']} is held by pid {proc['pid']}, which isn't ours — "
                 f"left alone: {proc['command']}")

    if args.containers:
        require_docker(env)
        log(f"Stopping containers for stack '{slot.stack}'...")
        for kind in ("ai", "core", "datapro"):
            compose(kind, ["stop"], env, check=False)

    if not stopped:
        warn(f"Nothing was running for stack '{slot.stack}'.")
    elif not args.containers:
        log(f"Done. Containers for '{slot.stack}' left running — pass --containers to stop them too.")
    else:
        log(f"Done. Stack '{slot.stack}' is fully stopped (volumes kept).")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    cmd_down(argparse.Namespace(containers=False, all=False, yes=True))
    return cmd_up(args)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report every leftover on this machine, changing nothing.

    Covers the three things that outlive a checkout: processes on a slot's
    ports that no pid file accounts for, stacks whose worktree is gone, and
    leaked testcontainers. `hms.py clean` fixes what this finds.
    """
    slot = current_slot()
    env = full_env(slot)
    problems = 0   # things `clean` can fix
    foreign = 0    # things it deliberately won't touch

    log(f"Checking stack '{slot.stack}' ({HMS_ROOT})")
    for proc in untracked_processes(port_map(env)):
        if proc["ours"]:
            problems += 1
            state = "ours, safe to kill"
        else:
            foreign += 1
            state = "NOT ours — clean will leave it alone"
        print(f"  untracked {proc['label']} on port {proc['port']}: pid {proc['pid']} ({state})",
              file=sys.stderr)
        print(f"    {proc['command']}", file=sys.stderr)

    if not docker_available(env):
        warn("Docker unreachable — can't check for orphaned stacks or testcontainers.")
        return 1 if problems or foreign else 0

    known = {s.stack: s for s in registry_slots(prune=False)}
    for slug in sorted(set(known) | set(discovered_slugs(env))):
        entry = known.get(slug)
        alive = entry is not None and entry.path.is_dir()
        resources = stack_resources(slug, env)
        if alive or not resources:
            continue
        problems += 1
        why = "checkout deleted" if entry else "no slot record"
        counts = ", ".join(f"{len(v)} {k}{'s' if len(v) > 1 else ''}" for k, v in resources.items())
        print(f"  orphaned stack '{slug}' ({why}): {counts}", file=sys.stderr)

    leaked, _ = _leaked_testcontainers(env, args.older_than)
    if leaked:
        problems += 1
        print(f"  {len(leaked)} leaked testcontainer(s) older than {args.older_than}m", file=sys.stderr)

    if foreign:
        warn(f"{foreign} port(s) held by processes that aren't ours — deal with those by hand, "
             "or move this checkout to another slot with `hms.py rm` then `hms.py up`.")
    if problems:
        warn(f"{problems} thing(s) to clean up. Run: scripts/hms.py clean")
    if not problems and not foreign:
        log("Nothing left behind.")
    return 1 if (problems or foreign) else 0


def cmd_clean(args: argparse.Namespace) -> int:
    """Remove everything `doctor` finds: untracked processes on this slot's
    ports, orphaned stacks, and leaked testcontainers.

    Only touches things nothing owns any more — a running stack whose checkout
    still exists is never disturbed, including other worktrees'.
    """
    slot = current_slot()
    env = full_env(slot)

    procs = [p for p in untracked_processes(port_map(env)) if p["ours"]]
    for proc in untracked_processes(port_map(env)):
        if not proc["ours"]:
            warn(f"Port {proc['port']} is held by pid {proc['pid']}, which isn't ours — "
                 f"leaving it: {proc['command']}")

    require_docker(env)
    known = {s.stack: s for s in registry_slots(prune=False)}
    orphans = []
    for slug in sorted(set(known) | set(discovered_slugs(env))):
        entry = known.get(slug)
        if entry is not None and entry.path.is_dir():
            continue
        if stack_resources(slug, env):
            orphans.append(slug)
    leaked, _ = _leaked_testcontainers(env, args.older_than)

    if not procs and not orphans and not leaked:
        log("Nothing to clean.")
        return 0

    log("Will remove:")
    for proc in procs:
        print(f"  {proc['label']} on port {proc['port']} (pid {proc['pid']})", file=sys.stderr)
    for slug in orphans:
        counts = ", ".join(f"{len(v)} {k}s" for k, v in stack_resources(slug, env).items())
        print(f"  orphaned stack '{slug}' — {counts} (databases included)", file=sys.stderr)
    if leaked:
        print(f"  {len(leaked)} leaked testcontainer(s)", file=sys.stderr)

    if args.dry_run:
        log("Dry run — nothing removed.")
        return 0
    _confirm("Remove all of the above?", args.yes)

    for proc in procs:
        kill_pid(int(proc["pid"]))  # type: ignore[arg-type]
    for slug in orphans:
        destroy_stack(slug, env, assume_yes=True)
    if leaked:
        run(["docker", "rm", "-f", *leaked], env, check=False, capture=True)
    log("Done.")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    """Run the UI dev server in the foreground so its log is visible.

    Same-origin reverse proxy: the app talks to /api/core and /api/ai on its
    own origin and Vite proxies those to this slot's Core/AI, so the whole
    stack is reachable from one URL with no CORS config. UI_HOST=0.0.0.0 makes
    it LAN-reachable.
    """
    slot = current_slot()
    env = full_env(slot)
    ui = HMS_ROOT / "ui"
    if not (ui / "node_modules").is_dir():
        log("node_modules missing — running pnpm install...")
        run(["pnpm", "install"], env, cwd=ui)

    env.update({"VITE_CORE_URL": "/api/core", "VITE_AI_URL": "/api/ai"})
    log(f"Starting UI dev server on port {env['UI_PORT']} "
        f"(proxying /api/core→{env['CORE_URL']}, /api/ai→{env['AI_URL']})...")
    # exec so Ctrl+C reaches vite directly.
    os.chdir(ui)
    os.execvpe("pnpm", ["pnpm", "exec", "vite", "--port", env["UI_PORT"],
                        "--host", os.environ.get("UI_HOST", "127.0.0.1")], env)


def cmd_migrate(args: argparse.Namespace) -> int:
    """Apply pending Alembic migrations to this slot's Core Postgres."""
    env = full_env(current_slot())
    require_docker(env)
    log("Applying Core migrations...")
    run(["uv", "run", "alembic", "upgrade", "head"],
        {**env, "DATABASE_URL": env["CORE_DATABASE_URL"]}, cwd=HMS_ROOT / "core")
    return 0


def cmd_migrate_new(args: argparse.Namespace) -> int:
    """Autogenerate a migration by diffing the models against this slot's DB.
    Edit the result before committing — autogen is a starting point."""
    env = full_env(current_slot())
    require_docker(env)
    log(f"Autogenerating migration: {args.message}")
    run(["uv", "run", "alembic", "revision", "--autogenerate", "-m", args.message],
        {**env, "DATABASE_URL": env["CORE_DATABASE_URL"]}, cwd=HMS_ROOT / "core")
    log("Generated. Review the new file under core/alembic/versions/ before committing.")
    return 0


def _parse_docker_time(value: str) -> Optional[datetime]:
    """Docker emits RFC3339 with nanoseconds and a Z suffix, which
    datetime.fromisoformat can't take before 3.11."""
    match = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?", value)
    if not match:
        return None
    stamp, frac, tz = match.groups()
    text = stamp + ("." + frac[:6] if frac else "")
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=timezone.utc) if tz in (None, "Z") else parsed.astimezone(timezone.utc)


def sweep_testcontainers(env: Dict[str, str], older_than: int, everything: bool) -> None:
    """Remove leaked testcontainers.

    Ryuk (testcontainers' auto-cleanup sidecar) doesn't behave under colima so
    we disable it, which means runs that crash or time out leak containers, and
    the stale port mappings confuse the next testcontainers session.

    Only containers older than `older_than` minutes are swept, so a test run in
    progress in another worktree survives. `hms-*` containers are dev stacks and
    are never touched.
    """
    doomed, skipped = _leaked_testcontainers(env, older_than, everything)
    if skipped:
        log(f"Leaving {skipped} testcontainer(s) younger than {older_than}m alone "
            "(another worktree may be mid-run).")
    if doomed:
        log(f"Removing {len(doomed)} leaked testcontainer(s)...")
        run(["docker", "rm", "-f", *doomed], env, check=False, capture=True)


def _leaked_testcontainers(
    env: Dict[str, str], older_than: int, everything: bool = False
) -> "tuple":
    """(ids considered leaked, count skipped for being too young). `hms-*` are
    dev stacks and are never included."""
    ids = docker_out(["ps", "-aq", "--filter", "label=org.testcontainers=true"], env)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than)
    doomed, skipped = [], 0
    for cid in ids:
        info = docker_out(["inspect", "--format", "{{.Created}}\t{{.Name}}", cid], env)
        if not info:
            continue
        created_raw, _, name = info[0].partition("\t")
        if name.lstrip("/").startswith("hms-"):
            continue
        created = _parse_docker_time(created_raw)
        if not everything and created is not None and created > cutoff:
            skipped += 1
            continue
        doomed.append(cid)
    return doomed, skipped


def cmd_sweep(args: argparse.Namespace) -> int:
    env = full_env(current_slot())
    require_docker(env)
    sweep_testcontainers(env, args.older_than, args.all)
    log("Done.")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    """Run the test suite. Scope: core | ai | ui | perf | all (default all).
    `perf` is the query performance suite and is excluded from `all`."""
    slot = current_slot()
    env = full_env(slot)
    require_docker(env)
    sweep_testcontainers(env, args.older_than, everything=False)

    extra = list(args.pytest_args)

    def core() -> None:
        log("Running Core tests...")
        run(["uv", "run", "pytest", *extra], {**env, "DATABASE_URL": env["CORE_DATABASE_URL"]},
            cwd=HMS_ROOT / "core")

    def ai() -> None:
        log("Running AI tests...")
        # AI tool tests need a live Core; integration tests also need a key.
        try:
            wait_for_http(f"{env['CORE_URL']}/health", 1)
        except Die:
            warn(f"Core isn't running at {env['CORE_URL']} — AI tool tests will skip. "
                 "Start it with scripts/dev-up.sh.")
        run(["uv", "run", "pytest", *extra], {**env, "DATABASE_URL": env["AI_DATABASE_URL"]},
            cwd=HMS_ROOT / "ai")

    def ui() -> None:
        log("Running UI tests...")
        run(["pnpm", "exec", "tsc", "--noEmit"], env, cwd=HMS_ROOT / "ui")
        run(["pnpm", "exec", "vitest", "run", *extra], env, cwd=HMS_ROOT / "ui")

    def perf() -> None:
        log("Running Core query-perf suite (tracked baselines, non-blocking)...")
        # Seeds a large Postgres and measures the semantic query path. `-s` so
        # the baseline report prints. Excluded from `all` — run it explicitly.
        run(["uv", "run", "pytest", "-m", "perf", "-s", *extra],
            {**env, "DATABASE_URL": env["CORE_DATABASE_URL"]}, cwd=HMS_ROOT / "core")

    scopes = {"core": [core], "ai": [ai], "ui": [ui], "perf": [perf], "all": [core, ai, ui]}
    if args.scope not in scopes:
        raise Die(f"Unknown scope: {args.scope}. Use one of: {', '.join(scopes)}.")
    for step in scopes[args.scope]:
        step()
    return 0


def cmd_ls(args: argparse.Namespace) -> int:
    """Every stack slot on this machine, with ports and running state."""
    slots = registry_slots()
    if not slots:
        warn("No stacks registered. Run scripts/dev-up.sh in a checkout to create one.")
        return 0

    if args.ports:
        for slot in slots:
            print(f"{slot.stack:<12} {slot.port_base + 1}-{slot.port_base + 6}")
        return 0

    # Docker may not be running; the table still works, we just can't say "up".
    env = full_env(current_slot())
    running = set()
    docker_up = docker_available(env)
    if docker_up:
        running = set(docker_out(["ps", "--format", "{{.Names}}"], env))

    # Stacks Docker knows about that the registry doesn't — their slot record
    # was lost, so nothing but this would ever surface them.
    if docker_up:
        known = {s.stack for s in slots}
        for slug in discovered_slugs(env):
            if slug not in known:
                slots.append(Slot(slug, 0, Path("(no slot record — `hms.py clean` removes it)")))

    here = HMS_ROOT
    print(f"{'':1}{'STACK':<12}{'STATE':<7}{'CORE':<6}{'AI':<6}{'UI':<6}{'TRINO':<7}{'BRANCH':<10}PATH")
    for slot in slots:
        if not slot.path.is_dir():
            # Checkout gone but resources remain: this is what `clean` targets.
            state = "orphan"
        elif f"hms-{slot.stack}-trino" in running:
            state = "up"
        elif service_pid("core", slot.path) or service_pid("ai", slot.path):
            state = "part"
        else:
            state = "down"

        if state == "orphan":
            print(f" {slot.stack:<12}{state:<7}{'—':<6}{'—':<6}{'—':<6}{'—':<7}{'—':<10}{slot.path}")
            continue

        branch = "?"
        rev = subprocess.run(["git", "-C", str(slot.path), "rev-parse", "--abbrev-ref", "HEAD"],
                             capture_output=True, text=True)
        if rev.returncode == 0:
            branch = rev.stdout.strip()
            # Worktree branches are wt/<uuid>; the uuid is already in PATH.
            if branch.startswith("wt/"):
                branch = "wt/…"

        b = slot.port_base
        print(f"{'*' if slot.path == here else ' '}{slot.stack:<12}{state:<7}"
              f"{b + 1:<6}{b + 2:<6}{b + 3:<6}{b + 4:<7}{branch:<10}{slot.path}")

    sys.stdout.flush()  # keep the note below the table when stderr is a tty
    print("\n* = this checkout. Postgres is on base+5 (core) and base+6 (ai).", file=sys.stderr)
    if any(not s.path.is_dir() for s in slots):
        warn("Stacks marked 'orphan' have no checkout left. `hms.py clean` removes them.")
    return 0


def _confirm(prompt: str, assume_yes: bool) -> None:
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise Die("Not a terminal — pass --yes to run this non-interactively.")
    if input(f"{prompt} [y/N] ").strip().lower() not in ("y", "yes"):
        raise Die("Aborted.")


def destroy_stack(slug: str, env: Dict[str, str], assume_yes: bool) -> None:
    """Remove a stack's containers, volumes, network and image, and release the
    slot. Works by compose label rather than `docker compose down`, so it still
    works when the checkout that created the stack is already gone."""
    entry = REGISTRY / f"{slug}.json"
    known = _read_registry_entry(entry)
    resources = stack_resources(slug, env)

    log(f"Stack '{slug}'{f' ({known.path})' if known else ''} — this will remove:")
    for kind, ids in resources.items():
        print(f"  {len(ids)} {kind}{'s' if len(ids) > 1 else ''}", file=sys.stderr)

    if not resources:
        log("Nothing left to remove; releasing the slot.")
    else:
        _confirm(f"Remove stack '{slug}' including its databases?", assume_yes)

    # Containers first, then volumes, then networks — a network can't go while
    # something is still attached to it.
    if resources.get("container"):
        run(["docker", "rm", "-f", *resources["container"]], env, check=False, capture=True)
    if resources.get("volume"):
        run(["docker", "volume", "rm", *resources["volume"]], env, check=False, capture=True)
    if resources.get("network"):
        run(["docker", "network", "rm", *resources["network"]], env, check=False, capture=True)
    if resources.get("image"):
        run(["docker", "image", "rm", *resources["image"]], env, check=False, capture=True)

    # The slot record goes last: if anything above failed, the next `ls` still
    # reports the stack as an orphan rather than losing track of it.
    leftover = stack_resources(slug, env)
    if leftover:
        warn(f"Stack '{slug}' still has {sum(len(v) for v in leftover.values())} object(s); "
             "keeping its slot record so `hms.py clean` can retry.")
    else:
        entry.unlink(missing_ok=True)


def cmd_rm(args: argparse.Namespace) -> int:
    """Destroy a stack — containers, volumes (databases included), network and
    image — and release its slot. The checkout itself is left alone.

    --all does this for every stack on the machine, other worktrees included.
    """
    slot = current_slot()
    env = full_env(slot)
    require_docker(env)

    if args.all:
        if args.slug:
            raise Die("Pass either a slug or --all, not both.")
        slots = all_slots(env)
        if not slots:
            log("No stacks on this machine.")
            return 0
        log("Will DESTROY every stack on this machine — including other worktrees':")
        describe_slots(slots, env)
        print("  All databases go with them. Checkouts and branches are left alone.",
              file=sys.stderr)
        _confirm("Destroy all of them?", args.yes)
        # Stop first so nothing is mid-write, then destroy.
        stop_everything(env, slots, containers=False)
        for target in slots:
            destroy_stack(target.stack, env, assume_yes=True)
        # Only our own .hms-stack is ours to delete — other checkouts keep
        # theirs and will re-register the same slot (and so the same ports) on
        # their next `up`, which is what you want: stable ports, empty stacks.
        STACK_FILE.unlink(missing_ok=True)
        log(f"Destroyed {len(slots)} stack(s). Each checkout rebuilds its own on the next `up`.")
        return 0

    slug = args.slug or slot.stack

    if slug == slot.stack:
        for name in ("ai", "core"):
            stop_service(name)

    destroy_stack(slug, env, args.yes)

    if slug == slot.stack:
        STACK_FILE.unlink(missing_ok=True)
        log(f"Removed stack '{slug}'. This checkout gets a fresh slot on the next `up`.")
    else:
        log(f"Removed stack '{slug}'.")
    return 0


def main_clone_root() -> Path:
    """The primary clone's root, even when called from inside a worktree.

    New worktrees always go under the main clone's .worktrees/. Nesting one
    worktree inside another is legal in git but leaves confusing paths, and a
    `worktree rm` of the outer one would strand the inner.
    """
    result = subprocess.run(
        ["git", "-C", str(HMS_ROOT), "rev-parse", "--git-common-dir"], capture_output=True, text=True
    )
    if result.returncode != 0:
        return HMS_ROOT
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = (HMS_ROOT / common).resolve()
    return common.parent


def require_main_current(root: Path) -> None:
    """Refuse to branch from a stale main.

    A worktree based on a stale commit silently misses work and can't
    fast-forward back onto origin/main. Fetch, then require local main to be
    exactly origin/main before doing anything.
    """
    git_env = dict(os.environ)
    log("Checking main is up to date with origin…")
    run(["git", "-C", str(root), "fetch", "--quiet", "origin", "main"], git_env,
        check=False, capture=True)

    def rev(ref: str) -> str:
        result = run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", ref],
                     git_env, check=False, capture=True)
        return result.stdout.strip()

    local, origin = rev("main"), rev("origin/main")
    if not local or not origin:
        raise Die("Couldn't resolve main / origin/main — is this the HMS repo with an 'origin' remote?")
    if local != origin:
        raise Die(
            f"Local main ({local[:7]}) is not at origin/main ({origin[:7]}).\n"
            "    A worktree would start from a stale base. Sync main first, e.g.:\n"
            f'      git -C "{root}" switch main && git pull --ff-only'
        )


def cmd_worktree_new(args: argparse.Namespace) -> int:
    """Create an isolated worktree off the latest origin/main, carry the
    local-only files into it, and allocate its stack slot so it's ready to `up`."""
    root = main_clone_root()
    base_dir = Path(os.environ.get("WORKTREE_BASE", root / ".worktrees"))
    name = args.name or str(uuid.uuid4())
    branch = f"wt/{name}"
    dest = base_dir / name

    require_main_current(root)

    log("Creating worktree")
    print(f"  branch: {branch}", file=sys.stderr)
    print(f"  path:   {dest}", file=sys.stderr)
    base_dir.mkdir(parents=True, exist_ok=True)
    # Base off main (verified current above) rather than HEAD, so worktrees are
    # always cut from the latest pushed state regardless of where this is run.
    run(["git", "-C", str(root), "worktree", "add", "-b", branch, str(dest), "main"],
        dict(os.environ))

    copied = 0
    for rel in WORKTREE_LOCAL_FILES:
        # Prefer this checkout's copy, falling back to the main clone's — a
        # worktree may not have been given every local file.
        src = HMS_ROOT / rel
        if not src.exists():
            src = root / rel
        if src.exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)
            print(f"  copied   {rel}", file=sys.stderr)
            copied += 1
        else:
            print(f"  skipped  {rel} (not present)", file=sys.stderr)
    log(f"Copied {copied} local file(s).")

    # Allocate the new checkout's slot now, so its ports are visible here and
    # claimed before anyone else's allocation scan runs.
    result = subprocess.run([sys.executable, str(dest / "scripts" / "hms.py"), "env", "--json"],
                            capture_output=True, text=True)
    if result.returncode == 0:
        new_env = json.loads(result.stdout)
        log(f"Stack slot '{new_env['HMS_STACK']}' — core:{new_env['CORE_PORT']} "
            f"ai:{new_env['AI_PORT']} ui:{new_env['UI_PORT']} trino:{new_env['TRINO_PORT']} "
            f"pg:{new_env['CORE_PG_PORT']}/{new_env['AI_PG_PORT']}")
    else:
        # Expected when the checked-out commit predates this script; the slot
        # gets allocated on first use instead. Any other cause is worth seeing.
        warn("Could not pre-allocate a stack slot; it will be allocated on first use.")
        if result.stderr.strip():
            warn(f"  {result.stderr.strip().splitlines()[-1]}")

    log("Worktree ready:")
    print(f"  cd {shlex.quote(str(dest))}", file=sys.stderr)
    print("  ./scripts/dev-up.sh        # builds the flex JAR + brings up this worktree's stack", file=sys.stderr)
    print("  ./scripts/ui-dev.sh        # in a separate terminal", file=sys.stderr)
    print(f"\nWhen done: scripts/hms.py worktree rm {shlex.quote(str(dest))}", file=sys.stderr)
    return 0


def cmd_worktree_rm(args: argparse.Namespace) -> int:
    """Destroy a worktree's stack, then remove the worktree and its branch.
    The counterpart to `worktree new` — completes the lifecycle."""
    root = main_clone_root()
    target = Path(args.path).resolve() if args.path else HMS_ROOT
    if target == root:
        raise Die("Refusing to remove the main clone. Pass the path of a worktree.")

    slot_file = target / ".hms-stack"
    slug = None
    if slot_file.is_file():
        try:
            slug = json.loads(slot_file.read_text())["stack"]
        except (ValueError, KeyError):
            pass

    env = full_env(current_slot())
    if slug:
        require_docker(env)
    else:
        warn(f"No stack slot recorded in {target} — only the worktree will go.")
    _confirm(
        f"Remove the worktree at {target}"
        + (f" and destroy stack '{slug}' including its databases?" if slug else "?"),
        args.yes,
    )

    # Read the branch before the worktree goes away.
    rev = subprocess.run(["git", "-C", str(target), "rev-parse", "--abbrev-ref", "HEAD"],
                         capture_output=True, text=True)
    branch = rev.stdout.strip() if rev.returncode == 0 else ""

    # Remove the worktree *first*. git refuses when there are uncommitted or
    # untracked files, and that refusal has to leave the stack and the slot
    # intact — otherwise a rejected removal strands a worktree whose databases
    # are already gone and whose ports have been handed to someone else.
    log(f"Removing worktree {target}...")
    force = ["--force"] if args.force else []
    result = run(["git", "-C", str(root), "worktree", "remove", *force, str(target)],
                 dict(os.environ), check=False, capture=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr, end="")
        raise Die("Worktree not removed, so its stack and slot were left alone. "
                  "Commit or discard the changes, or pass --force to discard them.")

    if slug:
        destroy_stack(slug, env, assume_yes=True)

    if branch.startswith("wt/"):
        result = run(["git", "-C", str(root), "branch", "-D", branch], dict(os.environ),
                     check=False, capture=True)
        if result.returncode != 0:
            warn(f"Left branch {branch} in place: {result.stderr.strip()}")
    log("Done.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hms.py",
        description=(__doc__ or "").split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=(func.__doc__ or help_text))
        p.set_defaults(func=func)
        return p

    p = add("env", cmd_env, "print this checkout's stack env (eval-able)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of shell assignments")
    p.add_argument("--with-secrets", action="store_true",
                   help="include ANTHROPIC_API_KEY (withheld by default)")

    add("up", cmd_up, "bring this checkout's stack up")

    p = add("down", cmd_down, "stop this checkout's Core + AI")
    p.add_argument("--containers", action="store_true", help="also stop this stack's containers")
    p.add_argument("--all", action="store_true",
                   help="stop EVERY stack on this machine, other worktrees included "
                        "(keeps databases and slots)")
    p.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")

    add("restart", cmd_restart, "down then up")
    add("ui", cmd_ui, "run the UI dev server in the foreground")
    add("migrate", cmd_migrate, "apply Alembic migrations to this stack's Core DB")

    p = add("migrate-new", cmd_migrate_new, "autogenerate a new Alembic migration")
    p.add_argument("message", help="what the migration does")

    p = add("test", cmd_test, "run tests: core | ai | ui | perf | all")
    p.add_argument("scope", nargs="?", default="all")
    p.add_argument("pytest_args", nargs="*", help="passed through to pytest/vitest")
    p.add_argument("--older-than", type=int, default=15, metavar="MIN",
                   help="only sweep testcontainers older than this (default: 15)")

    p = add("ls", cmd_ls, "list every stack on this machine")
    p.add_argument("--ports", action="store_true", help="just the port blocks")

    p = add("rm", cmd_rm, "destroy a stack and release its slot")
    p.add_argument("slug", nargs="?", help="stack to remove (default: this checkout's)")
    p.add_argument("--all", action="store_true",
                   help="destroy EVERY stack on this machine, other worktrees included")
    p.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")

    p = add("doctor", cmd_doctor, "report anything left behind (changes nothing)")
    p.add_argument("--older-than", type=int, default=15, metavar="MIN",
                   help="testcontainer age threshold in minutes (default: 15)")

    p = add("clean", cmd_clean, "remove everything `doctor` finds")
    p.add_argument("--older-than", type=int, default=15, metavar="MIN",
                   help="testcontainer age threshold in minutes (default: 15)")
    p.add_argument("--dry-run", action="store_true", help="list what would go, remove nothing")
    p.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")

    p = add("sweep", cmd_sweep, "remove leaked testcontainers")
    p.add_argument("--older-than", type=int, default=15, metavar="MIN",
                   help="age threshold in minutes (default: 15)")
    p.add_argument("--all", action="store_true",
                   help="ignore the age threshold (may disrupt a concurrent test run)")

    wt = sub.add_parser("worktree", help="create or remove isolated worktrees")
    wt_sub = wt.add_subparsers(dest="worktree_command", required=True)

    p = wt_sub.add_parser("new", description=cmd_worktree_new.__doc__, help="create a worktree + slot")
    p.add_argument("name", nargs="?", help="worktree name (default: a random uuid)")
    p.set_defaults(func=cmd_worktree_new)

    p = wt_sub.add_parser("rm", description=cmd_worktree_rm.__doc__, help="remove a worktree + its stack")
    p.add_argument("path", nargs="?", help="worktree to remove (default: this one)")
    p.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    p.add_argument("--force", action="store_true", help="remove even with uncommitted changes")
    p.set_defaults(func=cmd_worktree_rm)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Die as exc:
        print(f"{_paint('1;31', 'xx')}  {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
