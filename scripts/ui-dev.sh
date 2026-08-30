#!/usr/bin/env bash
# Run the UI dev server in the foreground. Ctrl+C to stop.
#
# Thin shim onto the real implementation in scripts/hms.py, kept so the
# familiar entry point kept working. `scripts/hms.py --help` lists everything.
exec python3 "$(dirname "$0")/hms.py" ui "$@"
