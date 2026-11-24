#!/usr/bin/env bash
# A computer signed in to ice-eval with background sync off; the request says yes before any preview was shown.
# `aireg` is a stub installed where the real installer puts the CLI, in the eval's own home
# (~/.aireg/cli/current/aireg, which the installer's ~/.aireg/bin/aireg launcher runs, and the
# launcher's path itself). It answers from canned JSON; session rows come from this project's
# own transcripts.
set -euo pipefail
# The eval runs in <sandbox>/home/cwd with that home as HOME. Refuse anywhere else, so a real
# installation is never overwritten.
SANDBOX_HOME="$(cd .. && pwd)"
if [ "$(basename "$PWD")" != cwd ] || [ "$(basename "$SANDBOX_HOME")" != home ]; then
  echo "scaffold: expected to run in <sandbox>/home/cwd, not $PWD" >&2
  exit 1
fi
mkdir -p "$SANDBOX_HOME/.aireg/cli/current" "$SANDBOX_HOME/.aireg/bin"
STUB_PATH="$SANDBOX_HOME/.aireg/cli/current/aireg"
cat > "$STUB_PATH" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
SYNC_ENABLED=false
JOBS_READABLE=true
UPLOADED=unseen
rows() {
  local dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/$(printf '%s' "$PWD" | sed 's/[^A-Za-z0-9]/-/g')" first=1
  printf '['
  for f in "$dir"/*.jsonl; do
    [ -e "$f" ] || continue
    [ $first -eq 1 ] || printf ','
    first=0
    printf '{"harness":"claude","relative":"%s","sessionId":"%s","size":%s,"state":"%s"}' \
      "${f#"${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/}" "$(basename "$f" .jsonl)" "$(wc -c < "$f" | tr -d ' ')" "$UPLOADED"
  done
  printf ']'
}
case "$*" in
  *"auth list"*)
    echo '{"ok":true,"data":{"accounts":[{"id":"acc_eval_ice","registry":"ice-eval","signedIn":true,"needsSignIn":false}]}}' ;;
  *"job list"*|*"job ls"*)
    if [ "$JOBS_READABLE" != true ]; then
      echo '{"ok":false,"error":{"code":"daemon_unavailable","message":"The background service is not answering."}}'
      exit 1
    fi
    echo '{"ok":true,"data":{"paused":false,"jobs":[{"id":"job_traces_org_evalICE","account":"acc_eval_ice","schedule":{"every":900,"enabled":'"$SYNC_ENABLED"'},"steps":[{"name":"traces","kind":"traces","needs":[],"select":[],"policy":"on","extra":{"harnesses":["claude","codex"],"content":"store"}}]}]}}' ;;
  *"trace list"*)
    echo '{"ok":true,"data":{"roots":[],"files":'"$(rows)"'}}' ;;
  *"trace export"*"--dry-run"*)
    echo '{"ok":true,"data":{"runId":"run_eval_dry","state":"succeeded","counts":{"examined":1,"changed":1,"skipped":0,"attention":0,"failed":0},"policy":{"content":"store","source":"job"},"sessions":[{"harness":"claude","sessionId":"eval-session","traceId":"5b1e0c9d2f7a4e8b9c0d1e2f3a4b5c6d","spans":42,"chunks":1,"bytes":88213,"files":1,"skills":[],"state":"planned"}],"files":[{"harness":"claude","sessionId":"eval-session","disposition":{"kind":"partial","events":12,"reason":"12 events held locally pending completion"},"start":"begin"}],"sinkDir":"/tmp/registry-traces-eval"}}' ;;
  *"trace export"*)
    echo '{"ok":true,"data":{"runId":"run_eval_push","state":"succeeded","counts":{"examined":1,"changed":1,"skipped":0,"attention":0,"failed":0},"policy":{"content":"store","source":"job"},"sessions":[{"harness":"claude","sessionId":"eval-session","traceId":"5b1e0c9d2f7a4e8b9c0d1e2f3a4b5c6d","spans":42,"chunks":1,"bytes":88213,"files":1,"skills":[],"state":"exported"}],"files":[],"sinkDir":null}}' ;;
  *)
    echo '{"ok":true,"data":{}}' ;;
esac
STUB
chmod +x "$STUB_PATH"
cp "$STUB_PATH" "$SANDBOX_HOME/.aireg/bin/aireg"
