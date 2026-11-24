#!/usr/bin/env bash
# A computer without Atlan's CLI: nothing syncs from it, so there is nothing to exclude.
# No `aireg` is installed in the eval's home.
set -euo pipefail
# The eval runs in <sandbox>/home/cwd with that home as HOME. Refuse anywhere else, so a real
# installation is never overwritten.
WORKSPACE="$(pwd -P)"
SANDBOX_HOME="$(cd .. && pwd -P)"
if [ "$(basename "$WORKSPACE")" != cwd ] || [ "$(basename "$SANDBOX_HOME")" != home ]; then
  echo "scaffold: expected to run in <sandbox>/home/cwd, not $WORKSPACE" >&2
  exit 1
fi
rm -rf "$SANDBOX_HOME/.aireg"
