#!/usr/bin/env bash
# Restart Core + AI after a code change. Containers are left alone.
#
# Thin shim onto the real implementation in scripts/hms.py, kept so the
# familiar entry point kept working. `scripts/hms.py --help` lists everything.
exec python3 "$(dirname "$0")/hms.py" restart "$@"
