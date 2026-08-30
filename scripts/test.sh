#!/usr/bin/env bash
# Run the test suite: core | ai | ui | perf | all (default all).
#
# Thin shim onto the real implementation in scripts/hms.py, kept so the
# familiar entry point kept working. `scripts/hms.py --help` lists everything.
exec python3 "$(dirname "$0")/hms.py" test "$@"
