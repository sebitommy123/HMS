#!/usr/bin/env bash
# Report anything this machine has left behind: untracked processes on a stack's
# ports, stacks whose worktree is gone, leaked test containers. Changes nothing.
#
# Thin shim onto the real implementation in scripts/hms.py, kept so the
# familiar entry point kept working. `scripts/hms.py --help` lists everything.
exec python3 "$(dirname "$0")/hms.py" doctor "$@"
