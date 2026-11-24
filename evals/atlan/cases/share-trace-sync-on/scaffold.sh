#!/usr/bin/env bash
# A computer signed in to one organization, ice-eval, whose desktop traces job is on: this session is already uploaded.
# `aireg` is a stub installed where the real installer puts the CLI, in the eval's own home
# (~/.aireg/cli/current/aireg, which the installer's ~/.aireg/bin/aireg launcher runs, and the
# launcher's path itself). It answers from canned JSON. Like the real CLI, `trace export <session
# id>` finds the transcript itself: the stub writes a fixture transcript for that id inside the
# eval workspace (the sandbox does not let the agent read Claude Code's config folder), and
# `trace list` then reports that session.
set -euo pipefail
# The eval runs in <sandbox>/home/cwd with that home as HOME. Refuse anywhere else, so a real
# installation is never overwritten.
WORKSPACE="$(pwd -P)"
SANDBOX_HOME="$(cd .. && pwd -P)"
if [ "$(basename "$WORKSPACE")" != cwd ] || [ "$(basename "$SANDBOX_HOME")" != home ]; then
  echo "scaffold: expected to run in <sandbox>/home/cwd, not $WORKSPACE" >&2
  exit 1
fi

ROOT="$WORKSPACE/.eval-claude"
PROJECT="projects/$(printf '%s' "$WORKSPACE" | sed 's/[^A-Za-z0-9]/-/g')"
mkdir -p "$ROOT/$PROJECT"
# The transcript body: SESSION_ID is the id the agent passes, STARTED_AT the time of the call.
cat > "$ROOT/template.jsonl" <<'JSONL'
{"type":"user","sessionId":"SESSION_ID","timestamp":"STARTED_AT","message":{"role":"user","content":"/atlanai:share-trace"}}
{"type":"custom-title","sessionId":"SESSION_ID","customTitle":"Share this session's trace"}
{"type":"assistant","sessionId":"SESSION_ID","timestamp":"STARTED_AT","message":{"role":"assistant","content":[{"type":"text","text":"Checking whether background sync already uploads this session."}]}}
JSONL

mkdir -p "$SANDBOX_HOME/.aireg/cli/current" "$SANDBOX_HOME/.aireg/bin"
cat > "$SANDBOX_HOME/.aireg/eval.env" <<ENV
ROOT=$ROOT
PROJECT=$PROJECT
SYNC_ENABLED=true
JOBS_READABLE=true
UPLOADED=finalized
ENV
STUB_PATH="$SANDBOX_HOME/.aireg/cli/current/aireg"
cat > "$STUB_PATH" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
. "$HOME/.aireg/eval.env"
RECORD="$HOME/.aireg/eval-session"
SESSION="$(cat "$RECORD" 2>/dev/null || true)"
REL="$PROJECT/$SESSION.jsonl"
SIZE=0
if [ "${1:-}" = trace ] && [ "${2:-}" = export ]; then
  for arg in "${@:3}"; do
    case "$arg" in -*) ;; *) SELECTOR="$arg"; break ;; esac
  done
  case "${SELECTOR:-}" in
    "") echo '{"ok":false,"error":{"code":"usage","message":"stub: name one session"}}'; exit 2 ;;
    */*) SESSION="$(basename "$SELECTOR" .jsonl)" ;;
    *) SESSION="$SELECTOR" ;;
  esac
  REL="$PROJECT/$SESSION.jsonl"
  sed -e "s/SESSION_ID/$SESSION/g" -e "s/STARTED_AT/$(date -u +%Y-%m-%dT%H:%M:%S.000Z)/g" \
    "$ROOT/template.jsonl" > "$ROOT/$REL"
  printf '%s' "$SESSION" > "$RECORD"
fi
[ -f "$ROOT/$REL" ] && SIZE="$(wc -c < "$ROOT/$REL" | tr -d ' ')"
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
    if [ -z "$SESSION" ]; then
      echo '{"ok":true,"data":{"roots":[{"harness":"claude","root":"'"$ROOT"'"}],"files":[]}}'
    else
      echo '{"ok":true,"data":{"roots":[{"harness":"claude","root":"'"$ROOT"'"}],"files":[{"harness":"claude","relative":"'"$REL"'","sessionId":"'"$SESSION"'","size":'"$SIZE"',"state":"'"$UPLOADED"'"}]}}'
    fi ;;
  *"trace export"*"--dry-run"*)
    echo '{"ok":true,"data":{"runId":"run_eval_dry","state":"succeeded","counts":{"examined":1,"changed":1,"skipped":0,"attention":0,"failed":0},"policy":{"content":"store","source":"job"},"sessions":[{"harness":"claude","sessionId":"'"$SESSION"'","traceId":"5b1e0c9d2f7a4e8b9c0d1e2f3a4b5c6d","spans":42,"chunks":1,"bytes":'"$SIZE"',"files":1,"skills":[],"state":"planned"}],"files":[{"harness":"claude","path":"'"$ROOT/$REL"'","sessionId":"'"$SESSION"'","disposition":{"kind":"partial","events":2,"reason":"2 events held locally pending completion"},"start":"begin"}],"sinkDir":"/tmp/registry-traces-eval"}}' ;;
  *"trace export"*)
    echo '{"ok":true,"data":{"runId":"run_eval_push","state":"succeeded","counts":{"examined":1,"changed":1,"skipped":0,"attention":0,"failed":0},"policy":{"content":"store","source":"job"},"sessions":[{"harness":"claude","sessionId":"'"$SESSION"'","traceId":"5b1e0c9d2f7a4e8b9c0d1e2f3a4b5c6d","spans":42,"chunks":1,"bytes":'"$SIZE"',"files":1,"skills":[],"state":"exported"}],"files":[],"sinkDir":null}}' ;;
  *)
    echo '{"ok":true,"data":{}}' ;;
esac
STUB
chmod +x "$STUB_PATH"
cp "$STUB_PATH" "$SANDBOX_HOME/.aireg/bin/aireg"
