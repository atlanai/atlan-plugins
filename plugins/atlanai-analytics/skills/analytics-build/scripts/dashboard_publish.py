"""Resumable host-mediated metric publication; this module makes no network calls.

The host captures live schema and a complete scoped inventory, reviews the generated
plan, executes one MCP call, then acknowledges it. An uncertain write is never replayed.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

from trident.core import Service, clean, digest, require
import context_contracts


class Publisher:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.store = Service(self.root)
        self.path = self.root / "dashboard-publication.json"

    def read(self, path):
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 4_000_000,
                "A required publication file is unavailable.")
        return clean(json.loads(path.read_text()))

    def save(self, state):
        self.store.write(self.path, state)

    def hold_identity(self):
        evidence = context_contracts.check_publication_holds(self.root)
        return hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False).encode()).hexdigest() if evidence is not None else None

    def plan(self, snapshot):
        if self.path.exists():
            old = self.read(self.path)
            require(not old.get("inflight"), "Resolve the outstanding call before preparing another plan.")
        workspace = snapshot.get("workspace_id")
        require(isinstance(workspace, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workspace),
                "Choose one workspace.")
        require(snapshot.get("complete") is True and isinstance(snapshot.get("metric_schema"), dict)
                and bool(snapshot["metric_schema"]), "Read the live schema and complete workspace inventory first.")
        existing = snapshot.get("artifacts")
        require(isinstance(existing, list), "Supply the scoped artifact inventory.")
        require(all(a.get("kind") == "metric" and a.get("workspace_id") == workspace for a in existing),
                "Inventory must contain only metrics from the selected workspace.")
        source_ids = [a["source_id"] for a in existing if a.get("source_id")]
        require(len(source_ids) == len(set(source_ids)), "Resolve duplicate source identities before publishing.")
        self.store.write(self.root / "existing.json", existing)
        context_contracts.cmd_check(SimpleNamespace(dir=str(self.root)))
        checked = self.read(self.root / "plan.json")
        bodies = {b["source_id"]: b for b in self.read(self.root / "metrics.json")}
        live = {a["id"]: a for a in existing}
        actions = []
        for item in checked["metrics"]:
            if not item["ok"]:
                continue
            body = copy.deepcopy(bodies[item["source_id"]])
            action = item["action"]
            artifact_id = item.get("match_id")
            if action == "revise":
                head = live[artifact_id]
                require(bool(head.get("etag")), "Read the matched head and its ETag before revising.")
                body["name"] = head["name"]
                body["display_name"] = head.get("display_name") or body["display_name"]
                body["definition"]["version"] = max(body["definition"]["version"], head["definition"]["version"] + 1)
                arguments = {"artifact_id": artifact_id, "if_match": head["etag"],
                             "metadata": {"definition": body["definition"], "auto_release": False,
                                          "source_version": body.get("source_version"),
                                          "source_metadata": head.get("source_metadata") or {}}}
                arguments["idempotency_key"] = "dashboard-" + digest(arguments)
                tool = "registry__append_artifact_version"
            elif action == "create":
                arguments = {"kind": "metric", "metadata": {**body, "workspace_id": workspace, "auto_release": False}}
                tool = "registry__create_artifact"
            else:
                body["definition"] = live[artifact_id]["definition"]
                arguments, tool = {"artifact_id": artifact_id}, "registry__get_artifact"
            actions.append({"action": action, "source_id": body["source_id"], "body": body,
                            "artifact_id": artifact_id, "call": {"name": tool, "arguments": arguments},
                            "phase": "head" if action == "reuse" else "write"})
        require(bool(actions), "No admitted metrics remain; inspect plan.json refusals.")
        document = {"workspace_id": workspace, "schema_hash": digest(snapshot["metric_schema"]),
                    "source_hash": digest(self.read(self.root / "metrics.json")), "actions": actions,
                    "publication_holds_hash": self.hold_identity(),
                    "refused": checked["refused"], "warnings": checked["warnings"]}
        plan_id = digest(document)
        state = {"plan_id": plan_id, "reviewed": document, "cursor": 0, "inflight": None, "published": []}
        self.save(state)
        return {"status": "review", "plan_id": plan_id, "workspace_id": workspace,
                "counts": checked["counts"], "refused": checked["refused"], "warnings": checked["warnings"],
                "actions": [{"action": a["action"], "name": a["body"]["name"], "call": a["call"]} for a in actions],
                "release": "draft; published answers require a separately verified release"}

    def next(self, approved):
        state = self.read(self.path)
        require(self.hold_identity() == state["reviewed"].get("publication_holds_hash"),
                "Publication holds changed; export and review a new plan.")
        require(approved == state["plan_id"], "Approval must name the exact reviewed plan.")
        require(not state.get("refused"), "The last call was refused; reconcile and review a new plan.")
        require(digest(self.read(self.root / "metrics.json")) == state["reviewed"]["source_hash"],
                "Context changed; reconcile and review a new plan.")
        if state["inflight"]:
            return {"status": "BLOCKED", "reason": "The previous call has no acknowledged result.",
                    "operation_id": state["inflight"]["operation_id"],
                    "fix": "Read the artifact by source identity and recover the exact write; never replay an uncertain create."}
        actions = state["reviewed"]["actions"]
        if state["cursor"] == len(actions):
            return {"status": "complete", "published": state["published"],
                    "released": sum(bool(p.get("release_id")) for p in state["published"]),
                    "drafts": sum(not p.get("release_id") for p in state["published"])}
        action = actions[state["cursor"]]
        if action["phase"] == "write":
            call = action["call"]
        else:
            call = {"name": "registry__get_artifact", "arguments": {"artifact_id": action["artifact_id"]}}
            if action["phase"] == "release":
                call["arguments"]["channel"] = "latest"
        operation_id = digest({"plan": state["plan_id"], "index": state["cursor"], "phase": action["phase"], "call": call})
        state["inflight"] = {"operation_id": operation_id, "phase": action["phase"], "call": call}
        self.save(state)
        return {"status": "call", "operation_id": operation_id, **call}

    @staticmethod
    def matches(artifact, action, workspace):
        return (artifact.get("kind") == "metric" and artifact.get("workspace_id") == workspace
                and artifact.get("source_id") == action["source_id"]
                and artifact.get("name") == action["body"]["name"]
                and artifact.get("definition") == action["body"]["definition"])

    def ack(self, receipt, recover=False):
        state = self.read(self.path)
        pending = state.get("inflight")
        require(pending and receipt.get("operation_id") == pending["operation_id"], "Acknowledge the exact outstanding call.")
        action = state["reviewed"]["actions"][state["cursor"]]
        phase = pending["phase"]
        if receipt.get("rejected") is True:
            require(receipt.get("http_status") in (400, 401, 403, 404, 409, 412, 413, 422),
                    "An uncertain transport or server failure cannot establish a refused write.")
            state["refused"] = {"operation_id": pending["operation_id"], "http_status": receipt["http_status"]}
            state["inflight"] = None
            self.save(state)
            return {"status": "BLOCKED", "reason": "Registry refused the call.",
                    "fix": "Resolve the refusal, reread live inventory and review a new plan."}
        artifact = receipt.get("artifact") or {}
        workspace = state["reviewed"]["workspace_id"]
        if phase == "release" and receipt.get("release_unavailable") is True:
            # Only a successful empty latest lookup qualifies. Auth/network errors stay pending.
            require(receipt.get("read_succeeded") is True, "A failed release read is not an unpublished artifact.")
            release_id = None
        else:
            require(receipt.get("read_succeeded") is True and isinstance(artifact.get("id"), str)
                    and bool(artifact["id"]), "Supply the successful tool result.")
            if action.get("artifact_id"):
                require(artifact["id"] == action["artifact_id"], "Receipt names another artifact.")
            if phase == "head" or recover:
                require(self.matches(artifact, action, workspace), "Readback differs from the reviewed definition or workspace.")
            if phase == "write":
                action["artifact_id"] = artifact["id"]
                action["phase"] = "head"
            elif phase == "head":
                action["phase"] = "release"
            release_id = (receipt.get("release_id") or artifact.get("release_id")) if phase == "release" else None
            if phase == "release":
                require(not receipt.get('release_id') or not artifact.get('release_id')
                        or receipt['release_id'] == artifact['release_id'], 'Release selectors disagree; reread the current release.')
                require(isinstance(release_id, str) and bool(release_id), "Record the release selector returned by the latest read.")
                require(artifact.get("kind") == "metric" and artifact.get("workspace_id") == workspace
                        and artifact.get("source_id") == action["source_id"], "Release belongs to another metric or workspace.")
                if not self.matches(artifact, action, workspace):
                    # A previous release remains live while the reviewed head is still a draft.
                    release_id = None
        if phase == "release":
            state["published"].append({"source_id": action["source_id"], "id": action["artifact_id"],
                                       "definition_hash": digest(action["body"]["definition"]), "release_id": release_id})
            state["cursor"] += 1
        state["inflight"] = None
        self.save(state)
        self.store.write(self.root / "published.json", state["published"])
        return {"status": "ok", "completed": state["cursor"], "total": len(state["reviewed"]["actions"])}
