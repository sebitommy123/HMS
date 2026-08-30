# scripts/

All of it lives in **`scripts/hms.py`** — one stdlib-only Python CLI, no venv
and no dependencies, because it's the thing that *builds* the environment. The
`*.sh` files are one-line shims onto its subcommands, kept so the familiar
entry points still work. `scripts/hms.py --help` lists everything.

## Stack slots

Every checkout of this repo — the main clone and each worktree — owns a **slot**:
a slug plus a base port, allocated on first use and recorded in `.hms-stack`
(gitignored) plus a machine-wide registry at `~/.hms/stacks/`.

Everything that could collide between checkouts is derived from the slot:

| Derived from the slot | Example (`main`) | Example (a worktree) |
|---|---|---|
| Core / AI / UI / Trino ports | 5001 / 5002 / 5003 / 5004 | 5101 / 5102 / 5103 / 5104 |
| Core / AI Postgres ports | 5005 / 5006 | 5105 / 5106 |
| Compose projects | `hms-main-{datapro,core,ai}` | `hms-wt2572fb-{…}` |
| Containers | `hms-main-trino` | `hms-wt2572fb-trino` |
| Named volumes (the databases) | `hms-main-core_postgres_data` | `hms-wt2572fb-core_postgres_data` |
| Trino image tag | `hms-datapro/trino-flex:main` | `hms-datapro/trino-flex:wt2572fb` |

So `dev-up.sh` in any number of worktrees is safe to run concurrently — each
gets its own containers, volumes, ports and databases, and nothing one checkout
does can reach another. The cost is real, though: a running stack means its own
Trino (~1–2 GB). Keep an eye on it with `hms.py ls`, and free slots you're done
with using `hms.py rm`.

The compose files read `HMS_STACK`, `TRINO_PORT` and friends from the
environment with **no defaults**, so a bare `docker compose` in a worktree fails
loudly instead of quietly operating on another checkout's containers. For
ad-hoc commands, load the slot first:

```bash
eval "$(scripts/hms.py env)"      # then docker compose / psql / curl as usual
```

## First-time setup

```bash
# Start colima if you haven't (Docker Desktop also works — DOCKER_HOST is
# auto-detected either way).
colima start

# Backends (Core + AI) use uv-managed venvs; UI uses pnpm. dev-up.sh runs
# these for you, but you can prime them:
(cd core && uv sync)
(cd ai && uv sync)
(cd ui && pnpm install)
```

## Day-to-day

```bash
scripts/dev-up.sh         # Trino + Postgres + Core + AI, for this checkout. Idempotent.
scripts/ui-dev.sh         # UI dev server (foreground, Ctrl+C to stop).
scripts/dev-down.sh       # Stop this checkout's Core + AI. Containers stay up.
scripts/dev-down.sh --containers   # ...and stop its containers too.
scripts/dev-restart.sh    # dev-down + dev-up. Use after code changes.
scripts/migrate.sh        # Apply Alembic migrations to this checkout's Core DB.
scripts/migrate-new.sh "your message"   # Autogenerate a migration.
scripts/test.sh           # Everything (core + ai + ui).
scripts/test.sh core      # Just one slice. Same with `ai`, `ui`, `perf`.
scripts/test.sh core tests/integration/test_object_factories.py  # Pass-through args.
```

## Working across checkouts

```bash
scripts/create-worktree.sh          # new worktree off origin/main + its own slot
scripts/hms.py ls                   # every stack on this machine, ports and state
scripts/hms.py rm [slug]            # destroy a stack (containers + volumes), free the slot
scripts/hms.py worktree rm <path>   # ...and remove the worktree and its branch
scripts/doctor.sh                   # report anything left behind. Changes nothing.
scripts/clean.sh                    # remove what doctor found (--dry-run to preview)
scripts/clean-testcontainers.sh     # just the leaked test containers (see below)
```

### Stopping everything at once

Every command above is scoped to the checkout you're standing in. These two are
the deliberate exceptions — they reach across every worktree on the machine:

```bash
scripts/dev-down.sh --all    # stop every stack: processes + containers.
                             # Databases and slots survive; `up` brings it back.
scripts/stack-rm.sh --all    # destroy every stack: containers, volumes, images.
                             # Databases go with it. Checkouts and branches don't.
```

Both list exactly what they're about to touch and ask before doing it (`--yes`
to skip, which is what you want in a script). They're blunt on purpose: if
another worktree is mid-test, `--all` will stop it. Reach for `hms.py rm <slug>`
when you only want one gone.

`hms.py ls` marks the checkout you're in with `*`:

```
 STACK       STATE  CORE  AI    UI    TRINO  BRANCH    PATH
*wt2572fb    up     5101  5102  5103  5104   wt/…      …/.worktrees/2572fb5e-…
 main        down   5001  5002  5003  5004   main      …/HMS
```

`worktree rm` removes the worktree first and only tears the stack down once git
has agreed — so a refusal over uncommitted changes leaves everything intact.
Pass `--force` to discard those changes.

## Nothing gets left behind

Three things can outlive the command that made them, and `scripts/doctor.sh`
finds all three (`scripts/clean.sh` removes them):

**Processes that outlived their pid file.** Core and AI are tracked by pid file,
not by a `pgrep` pattern that would match every checkout. If that file is lost —
a deleted `.dev-logs/`, a `kill -9`'d `up`, a crash — `down` falls back to asking
who is listening on this slot's ports, and stops it if its command line is
actually ours. A process that *isn't* ours is reported and never touched, so an
unrelated service that happens to hold the port is safe.

**Stacks whose worktree is gone.** A slot record is only forgotten once its stack
owns nothing; a deleted checkout with containers left is kept and shown as
`orphan` in `ls`. Even if the slot record itself is lost, stacks are rediscovered
from Docker's own compose labels — so `rm -rf`'ing a worktree still leaves
something that can find and remove its containers, volumes and image.

**Leaked testcontainers.** Ryuk is off under colima, so crashed test runs leak.
Swept by age, so concurrent runs in other worktrees survive.

`clean` only ever touches things nothing owns any more. A running stack whose
checkout still exists — including another worktree's — is never disturbed.

## Env vars worth knowing

| Var | Default | What it does |
|-----|---------|--------------|
| `ANTHROPIC_API_KEY` | from `ai/key.sh` | Required for live AI tests + chat. AI returns 503 from its messages endpoints without it. Withheld from `hms.py env` output unless you pass `--with-secrets`. |
| `HMS_STACK`, `HMS_PORT_BASE` | from `.hms-stack` | Override to act on another checkout's slot. |
| `CORE_PORT`, `AI_PORT`, `UI_PORT`, `TRINO_PORT`, `CORE_PG_PORT`, `AI_PG_PORT` | slot-derived | Individually overridable for one-offs. |
| `WORKTREE_BASE` | `<main clone>/.worktrees` | Where new worktrees are created. |
| `HMS_REGISTRY` | `~/.hms/stacks` | Where slots are registered. |
| `DOCKER_HOST` | auto | colima's socket on macOS, rootless Docker's on Linux, if present. |
| `TESTCONTAINERS_RYUK_DISABLED` | `true` | Ryuk doesn't play well with colima; keep it off. |

## Known gotchas

**"Connection refused" against a port nothing's listening on, in tests.**
Ryuk is disabled (required under colima), so test runs that crash or time out
leak their containers, and stale port mappings confuse the next testcontainers
session. Fix: `scripts/clean-testcontainers.sh`. It only sweeps containers older
than 15 minutes, so a test run in another worktree survives — pass `--all` to
ignore that, and `--older-than N` to change it. If sweeping doesn't help,
`colima restart`.

**`docker compose` says a variable is unset.** You're running it outside the
scripts. `eval "$(scripts/hms.py env)"` first. This is deliberate — see above.

**Core or AI won't start: `DATABASE_URL is required`.** Same cause. These have
no fallback because a wrong-but-plausible default would mean silently reading
another worktree's database.

**Alembic autogen produced an empty migration.** Make sure the new model is
actually imported somewhere reachable from `datapro_core.db.Base` — autogen
only sees tables registered against `Base.metadata`.
