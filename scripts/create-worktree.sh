#!/usr/bin/env bash
# Create an isolated worktree with its own stack slot, branched off the latest
# origin/main and ready to dev-up.
#
# Thin shim onto the real implementation in scripts/hms.py, kept so the
# familiar entry point kept working. `scripts/hms.py --help` lists everything.
exec python3 "$(dirname "$0")/hms.py" worktree new "$@"
