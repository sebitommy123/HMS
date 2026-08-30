#!/usr/bin/env bash
# List every HMS stack on this machine with its ports and state.
#
# Thin shim onto the real implementation in scripts/hms.py, kept so the
# familiar entry point kept working. `scripts/hms.py --help` lists everything.
exec python3 "$(dirname "$0")/hms.py" ls "$@"
