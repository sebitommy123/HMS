#!/usr/bin/env bash
# Remove everything scripts/doctor.sh reports. Only touches things nothing owns
# any more — running stacks with a live checkout are never disturbed.
#
# Thin shim onto the real implementation in scripts/hms.py, kept so the
# familiar entry point kept working. `scripts/hms.py --help` lists everything.
exec python3 "$(dirname "$0")/hms.py" clean "$@"
