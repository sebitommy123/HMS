# HMS / DataPro — working notes for Claude

## Conventions

### Always work in a worktree
**Never modify the main clone directly.** Before starting any work, create a
fresh git worktree with `scripts/create-worktree.sh` and do everything there. It
makes a UUID-named worktree under `.worktrees/` (gitignored), branches it as
`wt/<uuid>` off the current HEAD, and copies in the local-only files needed to
run the stack (e.g. `ai/key.sh`). Worktrees share Docker containers and ports
with the main clone, so run one stack at a time or override
`CORE_PORT`/`AI_PORT`/`UI_PORT`/`TRINO_PORT`.

### No backward compatibility
This project is in **active experimentation**. Do **not** add backward-compatibility
shims, deprecation paths, dual-support code, or migration fallbacks when changing an
API or contract. Change the thing cleanly, update every caller in the same pass, and
delete the old shape. There are no external consumers to protect yet — a clean break
is always preferred over a compatible one.
