#!/usr/bin/env bash
# Restart Core + AI (e.g. after a code change you want picked up by the
# already-running dev processes). Doesn't touch the Docker containers.

source "$(dirname "$0")/_lib.sh"

"$HMS_ROOT/scripts/dev-down.sh"
"$HMS_ROOT/scripts/dev-up.sh"
