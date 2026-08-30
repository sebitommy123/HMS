#!/usr/bin/env bash
# Destroy this checkout stack (containers, volumes, image) and release its slot.
#
# Thin shim onto the real implementation in scripts/hms.py, kept so the
# familiar entry point kept working. `scripts/hms.py --help` lists everything.
exec python3 "$(dirname "$0")/hms.py" rm "$@"
