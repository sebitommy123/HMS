#!/usr/bin/env bash
# Stop this checkout's Core + AI. Pass --containers to stop its containers too.
#
# Thin shim onto the real implementation in scripts/hms.py, kept so the
# familiar entry point kept working. `scripts/hms.py --help` lists everything.
exec python3 "$(dirname "$0")/hms.py" down "$@"
