#!/usr/bin/env bash
# A computer signed in to one organization, ice-eval, whose desktop traces job is off: nothing from it uploads unless pushed.
# `aireg` is a stub on PATH (through the project's settings) that answers from canned JSON and
# logs every call to .aireg-calls.log. Session rows come from this project's own transcripts.
set -euo pipefail
mkdir -p bin .claude
cat > bin/aireg <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "${AIREG_STUB_LOG:-/dev/null}"
SYNC_ENABLED=false
JOBS_READABLE=true
UPLOADED=unseen
rows() {
  local dir="$HOME/.claude/projects/$(printf '%s' "$PWD" | sed 's/[^A-Za-z0-9]/-/g')" first=1
  printf '['
  for f in "$dir"/*.jsonl; do
    [ -e "$f" ] || continue
    [ $first -eq 1 ] || printf ','
    first=0
    printf '{"harness":"claude","relative":"%s","sessionId":"%s","size":%s,"state":"%s"}' \
      "${f#"$HOME"/.claude/}" "$(basename "$f" .jsonl)" "$(wc -c < "$f" | tr -d ' ')" "$UPLOADED"
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
chmod +x bin/aireg
cat > .claude/settings.json <<JSON
{ "env": { "PATH": "$PWD/bin:$PATH", "AIREG_STUB_LOG": "$PWD/.aireg-calls.log" } }
JSON
