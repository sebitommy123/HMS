#!/usr/bin/env bash
# Create an isolated git worktree (named by a random UUID) off the current HEAD,
# and copy in the local-only files needed to run the stack — so you can work in
# throwaway worktrees and never touch the main clone.
#
# What gets copied: only the gitignored files that CAN'T be regenerated — the
# Anthropic key (ai/key.sh) and any local config overrides. Everything else
# (Python venvs, node_modules, the flex JAR) is rebuilt by the normal scripts;
# uv and pnpm use shared caches, so a fresh worktree bootstraps quickly.
#
# Usage:
#   scripts/create-worktree.sh                 # worktree under .worktrees/<uuid>
#   WORKTREE_BASE=/some/dir scripts/create-worktree.sh
#
# The worktree gets its own branch, wt/<uuid>, based on the current HEAD.

source "$(dirname "$0")/_lib.sh"

# Where worktrees live. Default: .worktrees/ inside the repo, which is gitignored
# so the nested checkouts never show up as untracked files. Override with
# WORKTREE_BASE.
WORKTREE_BASE="${WORKTREE_BASE:-$HMS_ROOT/.worktrees}"

# Local-only (gitignored) files to carry into each worktree, if they exist here.
# Add to this list as new local config/secrets appear.
LOCAL_FILES=(
  "ai/key.sh"                             # Anthropic API key (required for AI)
  "flex/connector/maven-settings.xml"     # internal Maven mirror, if configured
  "datapro/docker-compose.override.yml"   # host-specific compose tweaks
  ".claude/settings.local.json"           # local Claude Code settings
)

# A random UUID for the branch + directory (uuidgen on mac/linux; fallbacks).
uuid="$(uuidgen 2>/dev/null | tr '[:upper:]' '[:lower:]')"
if [[ -z "$uuid" ]]; then
  uuid="$(cat /proc/sys/kernel/random/uuid 2>/dev/null \
        || python3 -c 'import uuid; print(uuid.uuid4())')"
fi
branch="wt/${uuid}"
dest="${WORKTREE_BASE}/${uuid}"

log "Creating worktree"
printf '  branch: %s\n' "$branch"
printf '  path:   %s\n' "$dest"

mkdir -p "$WORKTREE_BASE"
git -C "$HMS_ROOT" worktree add -b "$branch" "$dest" HEAD

# Carry over the local-only files that exist in this clone.
copied=0
for rel in "${LOCAL_FILES[@]}"; do
  src="$HMS_ROOT/$rel"
  if [[ -e "$src" ]]; then
    mkdir -p "$(dirname "$dest/$rel")"
    cp -R "$src" "$dest/$rel"
    printf '  copied   %s\n' "$rel"
    copied=$((copied + 1))
  else
    printf '  skipped  %s (not present)\n' "$rel"
  fi
done
log "Copied ${copied} local file(s)."

log "Worktree ready. Next steps:"
printf '  cd %q\n' "$dest"
printf '  ./scripts/dev-up.sh        # builds flex JAR + brings up the stack (installs deps)\n'
printf '  ./scripts/ui-dev.sh        # in a separate terminal\n'
echo
warn "Worktrees share Docker containers + ports with the main clone. Run one"
warn "stack at a time, or set CORE_PORT / AI_PORT / UI_PORT / TRINO_PORT to"
warn "distinct values before dev-up so they can run side by side."
echo
printf 'When done: git worktree remove %q && git branch -D %s\n' "$dest" "$branch"
