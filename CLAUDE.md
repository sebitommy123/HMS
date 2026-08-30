# HMS / DataPro — working notes for Claude

## Conventions

### Always work in a worktree
**Never modify the main clone directly, and never modify a worktree you didn't
create** — several agents work in this repo at once, each in its own. Before
starting any work, create a fresh git worktree with `scripts/create-worktree.sh`
and do everything there. It makes a UUID-named worktree under `.worktrees/`
(gitignored), branches it as `wt/<uuid>` off the latest `origin/main` (refusing
to run if local main is stale), copies in the local-only files needed to run the
stack (e.g. `ai/key.sh`), and allocates it a stack slot.

Each checkout gets a **fully independent stack** — its own containers, volumes,
ports and databases, all derived from that slot — so `scripts/dev-up.sh` is safe
to run concurrently from any number of worktrees. `scripts/hms.py ls` shows
every stack on the machine. See `scripts/README.md` for the details.

When you're done with a worktree, `scripts/hms.py worktree rm <path>` tears down
its stack, frees the ports, and removes the worktree and branch in one step. If
anything ever gets left behind — a process that outlived its pid file, a stack
whose worktree was deleted, leaked testcontainers — `scripts/doctor.sh` reports
it and `scripts/clean.sh` removes it. Neither touches a stack whose checkout is
still alive.

To stop or destroy *everything* on the machine, other agents' worktrees
included, use `scripts/dev-down.sh --all` (reversible — keeps the databases) or
`scripts/stack-rm.sh --all` (not reversible). Both prompt first. Don't reach for
them casually: another session may be mid-test.

### No backward compatibility
This project is in **active experimentation**. Do **not** add backward-compatibility
shims, deprecation paths, dual-support code, or migration fallbacks when changing an
API or contract. Change the thing cleanly, update every caller in the same pass, and
delete the old shape. There are no external consumers to protect yet — a clean break
is always preferred over a compatible one.
