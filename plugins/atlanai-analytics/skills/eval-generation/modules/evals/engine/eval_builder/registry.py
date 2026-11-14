"""Push and pull eval datasets against the Atlan agent registry.

Auth rides on the `atlanai` CLI rather than a token this package manages. The
CLI is already authenticated wherever the workflow runs — Registry triggers the
skill into a harness on a machine where `atlanai auth login` has happened — so
minting a second credential path would add something the doctor has to check
and the user has to rotate, for no capability gain.

Field names come from the gateway's own OpenAPI (`/eval/v1/openapi.json`), not
the prose docs. They disagree, and the docs lose: the body takes `expected` and
`extra`, not `expected_output` and `metadata`.

Only approved records are pushed. An eval set nobody read is generated content
wearing ground truth's clothes, and pushing it to a shared registry launders
that provenance away.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field

from eval_builder.sensitive import scan_record
from eval_builder.spec import EvalRecord, EvalSpec, Provenance, known_fields

# The gateway caps a bulk record upload at 100 items.
BULK_LIMIT = 100


class RegistryError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------

def api(method: str, path: str, body: dict | None = None, timeout: int = 60) -> dict:
    """One authenticated gateway call via the CLI.

    The body goes in over stdin (`--input -`). There is no `--body` flag; the
    alternatives are `-f key=value` pairs, which cannot express the nested
    objects a record needs.
    """
    cmd = ["atlanai", "api", method.upper(), path]
    stdin = None
    if body is not None:
        cmd += ["--input", "-"]
        stdin = json.dumps(body)
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          input=stdin, timeout=timeout)
    out = (proc.stdout or "").strip()

    if proc.returncode != 0:
        detail = (proc.stderr or out or "").strip()
        raise RegistryError(f"{method} {path} failed: {detail[:400]}")
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise RegistryError(f"{method} {path} returned non-JSON: {out[:200]}") from e


def list_workspaces() -> list[dict]:
    """Every workspace the caller can see, as {id, display_name}."""
    proc = subprocess.run(
        ["atlanai", "workspace", "list", "--json", "id,display_name"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RegistryError(f"could not list workspaces: {(proc.stderr or '')[:200]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RegistryError(f"workspace list returned non-JSON: {proc.stdout[:200]}") from e


def resolve_workspace(name_or_id: str) -> str:
    """Turn a workspace display name into its handle.

    Exact match first: several workspaces on a real account share a prefix
    ("Sales", "Sales Sandbox"), and silently taking the first
    substring hit files the eval set in the wrong place.
    """
    if name_or_id.startswith("workspace_"):
        return name_or_id

    spaces = list_workspaces()
    exact = [w for w in spaces if (w.get("display_name") or "") == name_or_id]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(exact) > 1:
        raise RegistryError(
            f"{len(exact)} workspaces named {name_or_id!r}; pass the workspace_ handle"
        )

    near = [w for w in spaces if name_or_id.lower() in (w.get("display_name") or "").lower()]
    if len(near) == 1:
        return near[0]["id"]
    if len(near) > 1:
        names = ", ".join(sorted(w.get("display_name") or "?" for w in near)[:6])
        raise RegistryError(f"{name_or_id!r} matches several workspaces: {names}")
    raise RegistryError(f"no workspace matching {name_or_id!r}")


def workspace_detail(workspace_id: str) -> dict:
    """Everything init needs to decide whether binding here is safe."""
    return api("GET", f"/registry/v1/workspaces/{workspace_id}")


def agent_exists(name: str, workspace_id: str) -> tuple[bool, str]:
    """Is there an agent by this name in THIS workspace?

    Two distinct failures, and they need different messages. An agent that does
    not exist anywhere is a typo. An agent that exists in a different workspace
    is a real object that still cannot be linked: the gateway refuses a
    relationship whose two ends do not share a workspace, and that refusal
    arrives at push time, after all the building work is done.

    Returns (found_here, detail).
    """
    proc = subprocess.run(
        ["atlanai", "agent", "list", "--json", "name,workspace_id", "-L", "500"],
        capture_output=True, text=True, timeout=45,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RegistryError(f"could not list agents: {(proc.stderr or '')[:160]}")
    try:
        agents = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RegistryError("agent list returned non-JSON") from e
    here = [a for a in agents
            if a.get("name") == name and a.get("workspace_id") == workspace_id]
    if here:
        return True, here[0].get("id", "")

    elsewhere = [a for a in agents if a.get("name") == name]
    if elsewhere:
        return False, (f"exists, but in {elsewhere[0].get('workspace_id', '?')}. "
                       "A relationship's two ends must share a workspace, so the "
                       "set could be published but never linked")
    return False, "no agent by that name anywhere you can see"


@dataclass
class AccessProbe:
    """What this identity may do in a workspace, as the gateway reports it."""

    can_create: bool
    can_delete: bool
    detail: str

    def render(self) -> str:
        if not self.can_create:
            return f"cannot create content here — {self.detail}"
        if not self.can_delete:
            return ("can create but NOT delete — anything published here may be "
                    f"permanent for you. {self.detail}")
        return "create and delete both reported by the gateway"


def probe_write_access(workspace_id: str) -> AccessProbe:
    """Ask the gateway what this identity may do here. Read-only.

    An earlier version created a throwaway dataset and archived it, on the
    reasoning that only doing a thing establishes you can do it. That was
    unnecessary: `?include=permissions` returns the caller's capability matrix,
    and it reports exactly the right that a 403 later enforced —
    `can_delete: false` on the workspace whose skill archive was refused.

    The lesson was in how the API was searched, not in whether it existed:
    `include=capabilities` returned nothing, and absence of a guess was taken as
    absence of the feature.

    Advisory, by the gateway's own description: a UI gates controls on it and it
    never authorizes a request. That is the right strength for this — it decides
    whether to warn, not whether to permit.
    """
    try:
        view = api("GET", f"/registry/v1/workspaces/{workspace_id}?include=permissions")
    except RegistryError as e:
        return AccessProbe(True, True, f"permissions unreadable ({str(e)[:90]})")

    perms = view.get("permissions")
    if not isinstance(perms, dict):
        # Unknown is not "denied": refusing on a field the gateway declined to
        # return would block work for no established reason.
        return AccessProbe(True, True, "workspace reported no permission matrix")

    return AccessProbe(
        can_create=bool(perms.get("can_create_content", True)),
        can_delete=bool(perms.get("can_delete", False)),
        detail="" if perms.get("can_delete") else
               "the gateway reports can_delete: false for this workspace",
    )


def active_workspace() -> str:
    """The workspace new artifacts are created in.

    `workspace_id` is required on every create and the gateway never infers it,
    so getting it wrong files an eval set where nobody looks. There is no
    machine-readable accessor: `atlanai context show` takes no flags and prints
    nothing when no context is set, so a caller with no context must name the
    workspace rather than have one guessed for them.
    """
    proc = subprocess.run(["atlanai", "context", "show"],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode == 0 and proc.stdout.strip():
        m = re.search(r"\bworkspace_[a-z0-9]+\b", proc.stdout)
        if m:
            return m.group(0)
    raise RegistryError(
        "no active workspace. Either run `atlanai context use <workspace>`, or "
        "pass workspace= explicitly — a dataset filed under the wrong workspace "
        "is invisible to the people meant to review it."
    )


# --------------------------------------------------------------------------
# Push
# --------------------------------------------------------------------------

@dataclass
class PushResult:
    dataset_id: str
    created: int = 0
    skipped_existing: int = 0
    skipped_unapproved: int = 0
    rejected: list[tuple[int, str]] = field(default_factory=list)
    blocked_sensitive: list[tuple[str, str]] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"  dataset            {self.dataset_id}",
            f"  records created    {self.created}",
            f"  already present    {self.skipped_existing}",
            f"  not approved       {self.skipped_unapproved}",
        ]
        if self.blocked_sensitive:
            lines.append(f"  BLOCKED, customer data  {len(self.blocked_sensitive)}")
            for rid, why in self.blocked_sensitive[:6]:
                lines.append(f"    {rid}: {why[:110]}")
        if self.rejected:
            lines.append(f"  REJECTED           {len(self.rejected)}")
            for idx, err in self.rejected[:5]:
                lines.append(f"    [{idx}] {err[:120]}")
        return "\n".join(lines)


def find_dataset(name: str, workspace_id: str | None = None) -> dict | None:
    """An existing live dataset with this handle, if any.

    Scoped to a workspace when one is given, because the gateway makes `name`
    unique per (workspace, kind), not globally. A caller can see datasets across
    several workspaces, so matching on name alone will happily return a dataset
    belonging to a different workspace — and then records get pushed into
    someone else's set under a name that looked free.
    """
    offset, limit = 0, 100
    while True:
        page = api("GET", f"/eval/v1/datasets?limit={limit}&offset={offset}")
        items = page.get("items", [])
        for item in items:
            if item.get("name") != name:
                continue
            if item.get("artifact_status") != "active":
                continue
            if workspace_id and item.get("workspace_id") != workspace_id:
                continue
            return item
        total = (page.get("page") or {}).get("total")
        offset += limit
        if not items or (total is not None and offset >= total):
            return None


def ensure_dataset(spec: EvalSpec, workspace_id: str, input_key: str = "question") -> str:
    """Return the id of the dataset for this spec, creating it if needed."""
    existing = find_dataset(spec.name, workspace_id)
    if existing:
        return existing["id"]

    body = {
        "name": spec.name,
        "display_name": spec.name.replace("-", " "),
        "description": spec.source_description or None,
        "workspace_id": workspace_id,
        "input_key": input_key,
        "extra": {"built_by": "atlan-eval-agents"},
    }
    created = api("POST", "/eval/v1/datasets", body)
    ds_id = created.get("id")
    if not ds_id:
        raise RegistryError(f"dataset create returned no id: {str(created)[:200]}")
    return ds_id


def existing_hashes(dataset_id: str) -> set[str]:
    """content_hash of every live record already in the dataset.

    Pushing is re-run as a build is refined, so without this the same question
    accumulates a new row per run and every experiment over the dataset silently
    changes what it sampled.
    """
    hashes: set[str] = set()
    offset, limit = 0, 100
    while True:
        page = api("GET", f"/eval/v1/datasets/{dataset_id}/records?limit={limit}&offset={offset}")
        items = page.get("items", [])
        for item in items:
            if item.get("artifact_status") not in (None, "active"):
                continue
            h = (item.get("extra") or {}).get("content_hash")
            if h:
                hashes.add(h)
        total = (page.get("page") or {}).get("total")
        offset += limit
        if not items or (total is not None and offset >= total):
            break
    return hashes


def push(spec: EvalSpec, workspace: str | None = None,
         input_key: str = "question", require_approved: bool = True,
         allow_sensitive: bool = False) -> PushResult:
    """Create the dataset if needed and upload records that are not already there.

    `allow_sensitive` exists so a deliberate, reviewed exception is possible, but
    it defaults off: the failure mode being guarded is someone publishing a
    customer name without noticing, and a silent default would not guard it.
    """
    workspace_id = resolve_workspace(workspace) if workspace else active_workspace()
    dataset_id = ensure_dataset(spec, workspace_id, input_key)
    result = PushResult(dataset_id=dataset_id)

    if require_approved:
        approved = [r for r in spec.records if r.review_status == "approved"]
        result.skipped_unapproved = len(spec.records) - len(approved)
    else:
        approved = list(spec.records)

    # A record that names a customer must not be published, whatever its review
    # status says. Approval is a judgement about eval quality; this is a
    # different question, and the person approving may not have looked.
    publishable = []
    for r in approved:
        findings = scan_record(r)
        if findings and not allow_sensitive:
            result.blocked_sensitive.append(
                (r.id, "; ".join(f.render() for f in findings[:3]))
            )
            continue
        publishable.append(r)

    seen = existing_hashes(dataset_id)
    fresh = []
    for r in publishable:
        if r.content_hash() in seen:
            result.skipped_existing += 1
            continue
        fresh.append(r)

    for batch_start in range(0, len(fresh), BULK_LIMIT):
        batch = fresh[batch_start:batch_start + BULK_LIMIT]
        payload = {"items": [
            _with_workspace(r.to_registry_record(input_key, spec.name), workspace_id)
            for r in batch
        ]}
        resp = api("POST", f"/eval/v1/datasets/{dataset_id}/records/bulk", payload)
        # The response is index-aligned with what was submitted, and a partial
        # failure is normal — one bad row must not be reported as a whole
        # successful push.
        for slot in resp.get("items", []):
            idx = slot.get("index", -1)
            code = slot.get("status_code")
            if code == 201:
                result.created += 1
            else:
                problem = slot.get("error") or {}
                detail = problem.get("detail") or problem.get("title") or f"status {code}"
                result.rejected.append((batch_start + idx, str(detail)))

    return result


def _with_workspace(record: dict, workspace_id: str) -> dict:
    """workspace_id is required on the envelope of every artifact create."""
    return {**record, "workspace_id": workspace_id}


# --------------------------------------------------------------------------
# Pull
# --------------------------------------------------------------------------

def link_dataset_to_agent(dataset_id: str, agent_name: str,
                          workspace_id: str) -> tuple[bool, str]:
    """Record in the registry that this dataset evaluates that agent.

    Without it the binding lives only in a local run.json, and the runner — which
    expects to fetch evals and agent together — has nothing to follow. Both ends
    must sit in the same workspace; the gateway enforces that, and a set bound to
    an agent elsewhere simply cannot be linked.
    """
    agents = _agents_in(workspace_id)
    match = next((a for a in agents if a.get("name") == agent_name), None)
    if match is None:
        return False, (f"no agent named {agent_name!r} in this workspace — "
                       "a relationship's two ends must share one")
    try:
        api("POST", f"/registry/v1/artifacts/dataset/{dataset_id}/relationships",
            {"rel_type": "evaluates", "to_id": match["id"]})
    except RegistryError as e:
        return False, str(e)[:160]
    return True, match["id"]


def _agents_in(workspace_id: str) -> list[dict]:
    proc = subprocess.run(
        ["atlanai", "agent", "list", "--json", "id,name,workspace_id", "-L", "500"],
        capture_output=True, text=True, timeout=45,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        return [a for a in json.loads(proc.stdout)
                if a.get("workspace_id") == workspace_id]
    except json.JSONDecodeError:
        return []


def pull(dataset_name_or_id: str) -> EvalSpec:
    """Read a dataset back out of the registry as a local spec."""
    if dataset_name_or_id.startswith("dataset_"):
        dataset = api("GET", f"/eval/v1/datasets/{dataset_name_or_id}")
    else:
        dataset = find_dataset(dataset_name_or_id)
        if not dataset:
            raise RegistryError(f"no live dataset named {dataset_name_or_id!r}")

    dataset_id = dataset["id"]
    input_key = dataset.get("input_key") or "question"
    spec = EvalSpec(name=dataset.get("name") or dataset_id,
                    source_description=dataset.get("description") or "")

    offset, limit = 0, 100
    while True:
        page = api("GET", f"/eval/v1/datasets/{dataset_id}/records?limit={limit}&offset={offset}")
        items = page.get("items", [])
        for item in items:
            if item.get("artifact_status") not in (None, "active"):
                continue
            spec.records.append(_from_registry_record(item, input_key))
        total = (page.get("page") or {}).get("total")
        offset += limit
        if not items or (total is not None and offset >= total):
            break
    return spec


def _from_registry_record(item: dict, input_key: str) -> EvalRecord:
    extra = item.get("extra") or {}
    expected = item.get("expected") or {}
    prov = extra.get("provenance")
    return EvalRecord(
        # A tile record's source_ref is the tile, which its filter variants
        # share; the record's own id rides in its provenance.
        id=(prov.get("query_id") if isinstance(prov, dict) else None)
        or item.get("source_ref") or item.get("id"),
        question=(item.get("input") or {}).get(input_key, ""),
        golden_sql=expected.get("golden_sql", ""),
        dialect=expected.get("dialect") or "snowflake",
        tables=extra.get("tables") or [],
        as_of=expected.get("as_of"),
        modes=item.get("categories") or [],
        question_spec=extra.get("question_spec", ""),
        decisions=extra.get("decisions") or [],
        provenance=Provenance(**known_fields(Provenance, prov)) if isinstance(prov, dict) else None,
        review_status=item.get("label") or "pending",
        element=extra.get("element"),
    )


# ---------------------------------------------------------------------------
# Scorers and experiments
#
# A dataset with no scorer and no run is a list of questions in a database. The
# eval extension carries all three, and the skill was pushing only the first —
# so nothing in the registry said how a set is judged, and nothing recorded that
# the suite had ever passed.
#
# The division: a `code` scorer is cataloged here and executed by whoever runs
# it. The registry stores the definition and the scores; it does not run the
# grader. So `spec` must say precisely where the implementation lives, or the
# artifact is a claim nobody can reproduce.
# ---------------------------------------------------------------------------

def register_scorer(name: str, *, display_name: str, description: str,
                    scorer_kind: str, scope: str, outputs: dict,
                    spec: dict, workspace_id: str) -> tuple[str, bool]:
    """Catalog one scorer. Returns (id, created).

    Idempotent by name within the workspace: republishing the same definition
    must not fork it into a second artifact that later disagrees with the first.
    """
    existing = api("GET", f"/eval/v1/scorers?limit=100&workspace_id={workspace_id}")
    for item in existing.get("items", []):
        if item.get("name") == name and item.get("workspace_id") == workspace_id:
            return item["id"], False

    created = api("POST", "/eval/v1/scorers", {
        "name": name,
        "display_name": display_name,
        "description": description[:900],
        "scorer_kind": scorer_kind,
        "scope": scope,
        "outputs": outputs,
        "spec": spec,
        "workspace_id": workspace_id,
    })
    return created["id"], True


def start_experiment(name: str, *, display_name: str, dataset_id: str | None,
                     config: dict, workspace_id: str,
                     description: str = "") -> tuple[str, str]:
    """Open a run. Returns (id, warning). Status starts `running`.

    The dataset binding is attempted and then given up on, rather than being
    required. The gateway has been seen to refuse every `dataset_id` on create
    with `invalid dataset_record request: unknown sort field 'id'` — a
    server-side regression, reproducible against datasets that already have
    experiments bound from before it appeared. Failing the whole run over it
    would throw away the executions that had already happened; each result
    still carries its own `dataset_record_id`, so the join survives.
    """
    def create(extra: dict) -> str:
        try:
            return api("POST", "/eval/v1/experiments", body | extra)["id"]
        except RegistryError as e:
            if "artifacts_name_idx" not in str(e):
                raise
            # An interrupted upload left this run open. Reuse it rather than
            # forking a second experiment over the same cases — the results
            # endpoint rejects a repeated `case_id`, which is exactly the
            # mechanism that makes resuming safe.
            found = _experiment_by_name(name, workspace_id)
            if not found:
                raise
            return found

    body = {
        "name": name,
        "display_name": display_name,
        "description": description[:900] or None,
        "config": config,
        "workspace_id": workspace_id,
    }
    if dataset_id:
        try:
            return create({"dataset_id": dataset_id}), ""
        except RegistryError as e:
            if "unknown sort field" not in str(e):
                raise
            warning = ("the gateway refused to bind this run to its dataset "
                       "(unknown sort field `id`); results carry the per-case "
                       "join instead")
            return create({}), warning
    return create({}), ""


def _experiment_by_name(name: str, workspace_id: str) -> str | None:
    page = api("GET", f"/eval/v1/experiments?limit=100&workspace_id={workspace_id}")
    for item in page.get("items", []):
        if item.get("name") == name and item.get("workspace_id") == workspace_id:
            return item["id"]
    return None


def push_results(experiment_id: str, items: list[dict]) -> tuple[int, list[str]]:
    """Upload per-case results. Returns (created, rejections).

    The bulk response is index-aligned rather than fail-fast, so a partial
    upload has to be read per slot — reporting "201 created" off the HTTP status
    of the batch would hide cases the server refused.
    """
    if not items:
        return 0, []
    resp = api("POST", f"/eval/v1/experiments/{experiment_id}/results/bulk",
               {"items": items})
    created, problems = 0, []
    for slot in resp.get("items", []):
        if slot.get("status_code") == 201:
            created += 1
        else:
            detail = (slot.get("error") or {}).get("detail", "rejected")
            problems.append(f"case {slot.get('index')}: {detail}")
    return created, problems


def finalize_experiment(experiment_id: str, *, status: str,
                        summary: dict) -> str:
    """Freeze the run. Returns "" on success, or what went wrong.

    Same regression as the dataset binding: any status transition makes the
    server aggregate the run's results, and that aggregation sorts by a column
    it does not have. The run then cannot leave `running`, so the outcome is
    written into the description — which PATCH does accept — rather than lost.
    """
    try:
        api("PATCH", f"/eval/v1/experiments/{experiment_id}",
            {"experiment_status": status, "summary": summary})
        return ""
    except RegistryError as e:
        if "unknown sort field" not in str(e):
            raise

    scores = summary.get("scores", {}).get("matches_expectation", {})
    line = (f"{status.upper()} — {scores.get('mean', 0):.0%} of "
            f"{scores.get('count', 0)} case(s) matched expectation. "
            "Status could not be set: the gateway refuses every transition "
            "with `unknown sort field \'id\'`.")
    try:
        api("PATCH", f"/eval/v1/experiments/{experiment_id}",
            {"description": line})
    except RegistryError:
        pass
    return ("the run stays `running` — the gateway refuses status transitions "
            "(unknown sort field `id`); the outcome is in the description")


def personal_workspace() -> tuple[str, str] | None:
    """The caller's own workspace: (id, display name), or None.

    Exactly one workspace per account carries `kind: user_default`. Defaulting
    to it means a run that says nothing about destination lands somewhere
    private, rather than requiring the caller to know to ask for that. The
    alternative default — whatever workspace happens to be first — is
    usually a shared one.
    """
    try:
        page = api("GET", "/registry/v1/workspaces?limit=200")
    except RegistryError:
        return None
    for w in page.get("items", []):
        if w.get("kind") == "user_default":
            return w["id"], w.get("display_name") or w.get("name") or w["id"]
    return None
