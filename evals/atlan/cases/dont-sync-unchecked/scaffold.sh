#!/usr/bin/env bash
# A computer whose only organization, ice-eval, is signed out: the session is excluded but nothing could be checked.
# `aireg` is a stub installed where the real installer puts the CLI, in the eval's own home
# (~/.aireg/cli/current/aireg, which the installer's ~/.aireg/bin/aireg launcher runs, and the
# launcher's path itself). It answers `trace exclude` and `trace excluded` from canned text.
set -euo pipefail
# The eval runs in <sandbox>/home/cwd with that home as HOME. Refuse anywhere else, so a real
# installation is never overwritten.
WORKSPACE="$(pwd -P)"
SANDBOX_HOME="$(cd .. && pwd -P)"
if [ "$(basename "$WORKSPACE")" != cwd ] || [ "$(basename "$SANDBOX_HOME")" != home ]; then
  echo "scaffold: expected to run in <sandbox>/home/cwd, not $WORKSPACE" >&2
  exit 1
fi
mkdir -p "$SANDBOX_HOME/.aireg/cli/current" "$SANDBOX_HOME/.aireg/bin"
printf 'MODE=%s\n' "unchecked" > "$SANDBOX_HOME/.aireg/eval.env"
STUB_PATH="$SANDBOX_HOME/.aireg/cli/current/aireg"
cat > "$STUB_PATH" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
. "$HOME/.aireg/eval.env"
if [ "${1:-}" = trace ] && [ "${2:-}" = exclude ]; then
  SESSION="${3:-}"
  if [ "$MODE" = too-old ]; then
    echo 'error: Unknown trace action "exclude". Use export or list.' >&2
    echo 'code: usage' >&2
    exit 2
  fi
  [ -n "$SESSION" ] || { echo 'error: Name a session: aireg trace exclude <session>.' >&2; exit 2; }
  printf '%s\n' "$SESSION" > "$HOME/.aireg/eval-excluded"
  echo "Session $SESSION"
  echo "This session won't sync to any organization from this computer."
  if [ "$MODE" = deleted ]; then
    echo "Already uploaded to ice-eval: content deleted; the session's record (timing, counts, costs) remains."
    exit 0
  fi
  echo "Couldn't check ice-eval (You are signed out of ice-eval). If this session was uploaded there, its content was not deleted; run this command again to retry."
  exit 1
fi
if [ "${1:-}" = trace ] && [ "${2:-}" = excluded ]; then
  cat "$HOME/.aireg/eval-excluded" 2>/dev/null || echo "No sessions are excluded on this computer."
  exit 0
fi
echo "stub: not part of this eval: $*" >&2
exit 1
STUB
chmod +x "$STUB_PATH"
cp "$STUB_PATH" "$SANDBOX_HOME/.aireg/bin/aireg"
