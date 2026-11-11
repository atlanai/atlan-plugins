"""Pre-flight checks for the eval builder/runner workflow.

Every check answers one question: can the next step actually run?
A check never fixes anything. It reports state and the one action that clears it.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"       # workflow runs, but degraded (a source is missing)
    FAIL = "fail"       # workflow cannot run
    SKIP = "skip"       # not run: an earlier failure already gates it


@dataclass
class Result:
    name: str
    status: Status
    detail: str
    fix: str = ""
    # what this check gates; used to skip downstream checks cleanly
    gates: list[str] = field(default_factory=list)


Check = Callable[[], Result]


def requires(*capabilities: str) -> Callable[[Check], Check]:
    """Mark a check as meaningful only while `capabilities` are still unblocked.

    Re-running a warehouse probe after the config check already failed just
    re-reports the same missing env var in a less useful way.
    """

    def wrap(fn: Check) -> Check:
        fn.requires = capabilities  # type: ignore[attr-defined]
        return fn

    return wrap


def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"


# --------------------------------------------------------------------------
# Harness — the runtime the skill executes in
# --------------------------------------------------------------------------

def check_harness() -> Result:
    """A local coding agent must exist; Registry triggers the skill into it."""
    found = [c for c in ("claude", "codex") if shutil.which(c)]
    if not found:
        return Result(
            "harness", Status.FAIL,
            "no local coding agent on PATH",
            "install Claude Code (`npm i -g @anthropic-ai/claude-code`) or Codex",
            gates=["build", "run"],
        )
    return Result("harness", Status.OK, f"found {', '.join(found)}")


# --------------------------------------------------------------------------
# Registry — where datasets, experiments and results live
# --------------------------------------------------------------------------

def check_registry_cli() -> Result:
    if not shutil.which("atlanai"):
        return Result(
            "registry.cli", Status.FAIL, "atlanai not on PATH",
            "install the Atlan agent registry CLI", gates=["build", "run"],
        )
    code, out, _ = _run(["atlanai", "version"])
    if code != 0:
        return Result("registry.cli", Status.FAIL, "atlanai present but not runnable",
                      "run `atlanai doctor`", gates=["build", "run"])
    return Result("registry.cli", Status.OK, out.splitlines()[0] if out else "installed")


def check_registry_auth() -> Result:
    code, out, err = _run(["atlanai", "api", "GET", "/eval/v1/datasets?limit=1"])
    if code != 0:
        low = (err or out).lower()
        if "401" in low or "unauth" in low:
            return Result("registry.auth", Status.FAIL, "not authenticated",
                          "run `atlanai auth login`", gates=["build", "run"])
        if "404" in low or "not_found" in low:
            return Result(
                "registry.eval", Status.FAIL,
                "eval extension not visible on this workspace",
                "the eval extension is feature-flagged; ask your Atlan admin to enable it",
                gates=["build", "run"],
            )
        return Result("registry.auth", Status.FAIL, (err or out)[:160],
                      "run `atlanai auth login`", gates=["build", "run"])
    return Result("registry.auth", Status.OK, "eval API reachable")


def check_registry_role() -> Result:
    """What this identity may actually do, not merely that it authenticated.

    Creating an artifact and removing one are different rights, and the gateway
    exposes no capability matrix for a workspace — so the only honest thing is
    to report the role and say plainly what it does not imply. Learning that
    `delete` was absent by being refused, after five datasets and a skill had
    already been published, is the failure this exists to prevent.
    """
    code, out, err = _run(["atlanai", "api", "GET", "/registry/v1/auth/whoami"])
    if code != 0:
        return Result("registry.role", Status.WARN,
                      f"could not read identity: {(err or out)[:90]}",
                      "run `atlanai auth login`")
    try:
        me = json.loads(out)
    except json.JSONDecodeError:
        return Result("registry.role", Status.WARN, "whoami returned non-JSON", "")

    role = me.get("role") or "unknown"
    perms = me.get("permissions") or []
    detail = f"{role} in {me.get('org_id', '?')} ({len(perms)} org permission(s))"

    if role != "admin":
        return Result(
            "registry.role", Status.WARN,
            detail + " — creating an artifact does not imply removing it",
            "you can publish; archiving what you published may need an admin. "
            "Prefer a private workspace while iterating.",
        )
    return Result("registry.role", Status.OK, detail)


def probe_tables(conn, tables: list[str]) -> list[tuple[str, bool, str]]:
    """(table, readable, why) — can this identity actually SELECT from each?

    A zero-row probe rather than a metadata lookup: a grant on the schema does
    not guarantee a grant on the table, and INFORMATION_SCHEMA can list objects
    the caller cannot read.
    """
    out = []
    for t in tables:
        try:
            with conn.cursor() as cur:
                cur.execute(f"select * from {t} limit 0")
                cur.fetchall()
            out.append((t, True, ""))
        except Exception as e:  # noqa: BLE001
            out.append((t, False, str(e).splitlines()[0][:90]))
    return out


# --------------------------------------------------------------------------
# Warehouse — the source of schema and query history
# --------------------------------------------------------------------------

_SF_REQUIRED = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER")
_SF_SECRET = ("SNOWFLAKE_PASSWORD", "SNOWFLAKE_PRIVATE_KEY", "SNOWFLAKE_PRIVATE_KEY_PATH")

# Browser SSO carries no local secret at all — the browser round-trip is the
# credential. Accounts under an authentication policy often reject passwords
# outright while allowing this, so it must not be reported as "no credential".
_SF_BROWSER = "externalbrowser"


def _sf_authenticator() -> str:
    return (os.environ.get("SNOWFLAKE_AUTHENTICATOR") or "").strip().strip('"').lower()


def check_warehouse_config() -> Result:
    missing = [k for k in _SF_REQUIRED if not os.environ.get(k)]
    if missing:
        return Result("warehouse.config", Status.FAIL,
                      f"missing {', '.join(missing)}",
                      "export Snowflake connection env vars, or run "
                      "`build --from-files <dir>` to work from .sql/.json files instead",
                      gates=["warehouse"])

    auth = _sf_authenticator()
    if auth == _SF_BROWSER:
        return Result("warehouse.config", Status.OK,
                      f"{os.environ['SNOWFLAKE_ACCOUNT']} as {os.environ['SNOWFLAKE_USER']} "
                      f"(browser SSO)")
    if not any(os.environ.get(k) for k in _SF_SECRET):
        return Result("warehouse.config", Status.FAIL,
                      "no credential set",
                      f"set one of {', '.join(_SF_SECRET)}, "
                      f'or SNOWFLAKE_AUTHENTICATOR="{_SF_BROWSER}" for SSO',
                      gates=["warehouse"])
    return Result("warehouse.config", Status.OK,
                  f"{os.environ['SNOWFLAKE_ACCOUNT']} as {os.environ['SNOWFLAKE_USER']}")


def _sf_connect():
    import snowflake.connector  # imported lazily; optional dependency

    kw = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "login_timeout": 20,
    }
    for opt, key in (
        ("SNOWFLAKE_ROLE", "role"),
        ("SNOWFLAKE_WAREHOUSE", "warehouse"),
        ("SNOWFLAKE_DATABASE", "database"),
        ("SNOWFLAKE_SCHEMA", "schema"),
    ):
        if os.environ.get(opt):
            kw[key] = os.environ[opt]
    auth = _sf_authenticator()
    if auth == _SF_BROWSER:
        # No secret to pass: the connector opens a browser and completes SSO.
        kw["authenticator"] = _SF_BROWSER
        # The round-trip needs a human, so the doctor's short timeout won't do.
        kw["login_timeout"] = 120
    elif os.environ.get("SNOWFLAKE_PASSWORD"):
        kw["password"] = os.environ["SNOWFLAKE_PASSWORD"]
        if auth:
            kw["authenticator"] = auth
    else:
        from cryptography.hazmat.primitives import serialization

        pem = os.environ.get("SNOWFLAKE_PRIVATE_KEY")
        if not pem and os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"):
            pem = open(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]).read()
        pw = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
        key = serialization.load_pem_private_key(
            pem.encode(), password=pw.encode() if pw else None
        )
        kw["private_key"] = key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    return snowflake.connector.connect(**kw)


@requires("warehouse")
def check_warehouse_connect() -> Result:
    try:
        conn = _sf_connect()
    except ImportError as e:
        return Result("warehouse.connect", Status.FAIL, f"driver missing: {e}",
                      "uv add snowflake-connector-python", gates=["warehouse"])
    except Exception as e:  # noqa: BLE001 - surface the driver's own message
        return Result("warehouse.connect", Status.FAIL, str(e).splitlines()[0][:160],
                      "check account/user/credential", gates=["warehouse"])
    try:
        with conn.cursor() as cur:
            cur.execute("select current_role(), current_warehouse()")
            role, wh = cur.fetchone()
        if not wh:
            return Result("warehouse.connect", Status.FAIL, f"role {role}, no warehouse",
                          "set SNOWFLAKE_WAREHOUSE", gates=["warehouse"])
        return Result("warehouse.connect", Status.OK, f"role {role}, warehouse {wh}")
    finally:
        conn.close()


@requires("warehouse")
def check_query_history() -> Result:
    """The single highest-risk access in the whole workflow.

    ACCOUNT_USAGE.QUERY_HISTORY needs a grant most analyst roles do not have.
    Degrade to INFORMATION_SCHEMA.QUERY_HISTORY (7 days, own queries) when denied.
    """
    try:
        conn = _sf_connect()
    except Exception as e:  # noqa: BLE001
        return Result("warehouse.query_history", Status.FAIL, str(e).splitlines()[0][:160],
                      "fix warehouse.connect first", gates=["mine"])
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "select count(*) from snowflake.account_usage.query_history "
                    "where start_time > dateadd('day', -30, current_timestamp())"
                )
                n = cur.fetchone()[0]
                if n == 0:
                    return Result("warehouse.query_history", Status.WARN,
                                  "ACCOUNT_USAGE readable but empty for the last 30 days",
                                  "widen the window, or point at an account with real traffic")
                return Result("warehouse.query_history", Status.OK,
                              f"ACCOUNT_USAGE: {n:,} queries in the last 30 days")
            except Exception as acct_err:  # noqa: BLE001
                try:
                    cur.execute(
                        "select count(*) from table(information_schema.query_history("
                        "end_time_range_start => dateadd('day', -7, current_timestamp())))"
                    )
                    n = cur.fetchone()[0]
                    return Result(
                        "warehouse.query_history", Status.WARN,
                        f"no ACCOUNT_USAGE ({str(acct_err).splitlines()[0][:70]}); "
                        f"INFORMATION_SCHEMA fallback has {n:,} queries (7 days, your own)",
                        "grant imported privileges on database snowflake to your role "
                        "for org-wide history",
                    )
                except Exception as is_err:  # noqa: BLE001
                    return Result(
                        "warehouse.query_history", Status.FAIL,
                        f"no query history available: {str(is_err).splitlines()[0][:90]}",
                        "grant ACCOUNT_USAGE, or run `build --from-files <dir>`",
                        gates=["mine"],
                    )
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Optional enrichment — Atlan SQL Intelligence
# --------------------------------------------------------------------------

def check_sql_intelligence() -> Result:
    """Optional. Absence degrades ranking quality, never blocks the run."""
    base, token = os.environ.get("ATLAN_BASE_URL"), os.environ.get("ATLAN_API_KEY")
    if not (base and token):
        return Result("atlan.sql_intelligence", Status.WARN,
                      "ATLAN_BASE_URL / ATLAN_API_KEY unset",
                      "optional: set them to rank queries using SQL Intelligence insights")
    return Result("atlan.sql_intelligence", Status.OK, f"configured for {base}")


# `claude mcp list` reports every server the harness knows about, including
# account-level connectors, with live connection status. An earlier version of
# this check read local config only and declared the rest unknowable — that was
# wrong, and it mattered: the connectors it could not see are exactly the ones
# the skill recommends using.
_MCP_LINE = re.compile(r"^(?P<name>[^:]+):\s+(?P<url>\S+).*?-\s+(?P<state>.*)$")
_CONNECTED = "\u2714"

# Servers worth calling out by name, because the workflow has a use for them.
_WANTED = {
    "snowflake": "query history — use it instead of exporting credentials",
    "atlan": "glossary and SQL Intelligence — phrase questions in business terms",
}


# Set by the CLI when the caller passes --mcp-config, so a contained session
# gets a truthful answer. See _mcp_servers.
MCP_CONFIG: str = ""


# Cached: `claude mcp list` takes seconds, and two checks need its answer.
@functools.lru_cache(maxsize=1)
def _mcp_servers(timeout: int = 45) -> list[tuple[str, bool]]:
    """(server name, connected) for everything THIS SESSION can reach.

    `claude mcp list` runs as a separate process and reads the user's global
    configuration, so it knows nothing about a session started with
    `--mcp-config`/`--strict-mcp-config`. Measured in a sandbox declaring
    exactly two servers: it reported 80, and missed both of the two.

    That error runs in the dangerous direction — the doctor would report a
    Snowflake MCP as available and send the agent to a tool the session cannot
    reach. When the caller names the config, read it instead.
    """
    if MCP_CONFIG:
        return _mcp_servers_from_config(MCP_CONFIG)
    code, out, _ = _run(["claude", "mcp", "list"], timeout=timeout)
    if code not in (0, 1) or not out:
        return []
    servers = []
    for line in out.splitlines():
        m = _MCP_LINE.match(line.strip())
        if not m:
            continue
        servers.append((m.group("name").strip(), _CONNECTED in m.group("state")))
    return servers


def _mcp_servers_from_config(path: str) -> list[tuple[str, bool]]:
    """Servers a session was explicitly started with.

    Declared, not probed: whether each one authenticates is the session's to
    discover, and reporting them as connected would be the same overclaim in a
    new place. They are reported as declared and the wording says so.
    """
    import json as _json

    try:
        raw = _json.loads(Path(path).expanduser().read_text())
    except Exception:  # noqa: BLE001 - an unreadable config is a WARN, not a crash
        return []
    servers = raw.get("mcpServers") or raw.get("mcp_servers") or {}
    return [(name, True) for name in servers]


def check_mcp() -> Result:
    """Which MCP servers this harness has, and which of them this workflow wants.

    Reported rather than gated: a connected Snowflake MCP is the best source,
    but direct credentials and `--from-files` both work without one.
    """
    try:
        servers = _mcp_servers()
    except Exception as e:  # noqa: BLE001
        return Result("mcp", Status.WARN, f"could not list MCP servers: {e}",
                      "run `claude mcp list` yourself to see what is connected; "
                      "`build --from-files` needs no MCP either way")

    if not servers:
        return Result("mcp", Status.WARN, "no MCP servers reported",
                      "run `claude mcp list` — if the harness is not Claude Code, "
                      "check your own tool list instead; with no MCP at all, "
                      "`build --from-files` is the way in")

    connected = [n for n, ok in servers if ok]
    useful = {
        kind: [n for n in connected if kind in n.lower()]
        for kind in _WANTED
    }
    hits = {k: v for k, v in useful.items() if v}

    detail = (f"{len(connected)} declared in {MCP_CONFIG}" if MCP_CONFIG
              else f"{len(connected)}/{len(servers)} connected")
    if hits:
        detail += " · " + "; ".join(f"{k}: {', '.join(v[:2])}" for k, v in hits.items())

    # Snowflake is the one that changes what you have to do: with it, the
    # credential step disappears. An Atlan MCP improves the questions but does
    # not remove a prerequisite, so it is not enough to call this ok.
    if hits.get("snowflake"):
        return Result("mcp", Status.OK, detail,
                      "query it directly and pass the rows to `build --from-files` "
                      "rather than exporting warehouse credentials")
    if hits:
        return Result("mcp", Status.WARN,
                      f"{detail} · no Snowflake MCP",
                      "warehouse access still needs credentials or "
                      "`build --from-files`; `claude mcp list` shows which "
                      "Snowflake servers need authenticating")
    return Result("mcp", Status.WARN,
                  f"{detail} · none for: {', '.join(_WANTED)}",
                  "a connected Snowflake MCP would remove the credential step; "
                  "without one, use credentials or `build --from-files`")


ALL_CHECKS: list[Check] = [
    check_harness,
    check_registry_cli,
    check_registry_auth,
    check_registry_role,
    check_warehouse_config,
    check_warehouse_connect,
    check_query_history,
    check_sql_intelligence,
]


def run(checks: list[Check] | None = None) -> list[Result]:
    """Run checks in order, skipping any gated by an earlier failure."""
    results: list[Result] = []
    blocked: set[str] = set()
    for check in checks or ALL_CHECKS:
        needs = set(getattr(check, "requires", ()))
        hit = needs & blocked
        if hit:
            name = getattr(check, "__name__", "check").removeprefix("check_")
            results.append(Result(
                name.replace("_", ".", 1), Status.SKIP,
                f"not checked — {', '.join(sorted(hit))} already blocked above",
            ))
            continue
        try:
            r = check()
        except Exception as e:  # noqa: BLE001 - a broken check is a failed check
            r = Result(getattr(check, "__name__", "check"), Status.FAIL,
                       f"check raised: {e}", "report this")
        results.append(r)
        if r.status is Status.FAIL:
            blocked.update(r.gates)
    return results


_GLYPH = {Status.OK: "ok  ", Status.WARN: "warn", Status.FAIL: "FAIL", Status.SKIP: "--  "}


def render(results: list[Result]) -> str:
    width = max(len(r.name) for r in results)
    lines = []
    for r in results:
        lines.append(f"  [{_GLYPH[r.status]}] {r.name:<{width}}  {r.detail}")
        if r.fix and r.status is not Status.OK:
            lines.append(f"         {' ' * width}  → {r.fix}")

    fails = [r for r in results if r.status is Status.FAIL]
    warns = [r for r in results if r.status is Status.WARN]
    lines.append("")
    if fails:
        gated = sorted({g for r in fails for g in r.gates})
        lines.append(f"  {len(fails)} blocking. Cannot run: {', '.join(gated) or 'workflow'}.")
        if gated == ["warehouse"] or gated == ["mine", "warehouse"]:
            # The warehouse is the better source, not the only one. Saying
            # "cannot run" without naming the alternative is what stalled a
            # real session.
            lines.append("  The warehouse is the richer source, but not the only one:")
            lines.append("  `eval-builder build --from-files <dir>` reads .sql/.json files instead.")
    elif warns:
        lines.append(f"  Ready, degraded ({len(warns)} warnings above).")
    else:
        lines.append("  Ready.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="eval-doctor", description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = ap.parse_args(argv)

    results = run()
    if args.json:
        print(json.dumps([
            {"name": r.name, "status": r.status.value, "detail": r.detail,
             "fix": r.fix, "gates": r.gates}
            for r in results
        ], indent=2))
    else:
        print(render(results))
    return 1 if any(r.status is Status.FAIL for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())


# --------------------------------------------------------------------------
# Business vocabulary — makes the question sound like the org, not the schema
# --------------------------------------------------------------------------

def check_glossary() -> Result:
    """Atlan glossary over MCP, for phrasing questions in the org's own terms.

    Optional, and worth reporting either way. A question written only from
    column names inherits the schema's vocabulary; one written with the glossary
    to hand uses the words the business actually says, which is the difference
    between a question someone would ask and a question that reads like a
    rewritten SELECT.
    """
    base, token = os.environ.get("ATLAN_BASE_URL"), os.environ.get("ATLAN_API_KEY")
    if not (base and token):
        return Result("atlan.glossary", Status.WARN,
                      "not configured — questions will use schema vocabulary only",
                      "set ATLAN_BASE_URL and ATLAN_API_KEY to phrase questions "
                      "in the organisation's own terms")
    return Result("atlan.glossary", Status.OK, f"available at {base}")


ALL_CHECKS.append(check_glossary)
ALL_CHECKS.append(check_mcp)


# --------------------------------------------------------------------------
# Golden SQL — what will check the answer key
# --------------------------------------------------------------------------

def check_golden_sql() -> Result:
    """Which check the answer key will get, said before anyone drafts a question.

    Executing it on Snowflake is the strong check. Without a warehouse, build
    and review fall back to Atlan's schema: REST when a key is set, otherwise a
    file the agent writes from its Atlan MCP. With neither, every record is kept
    UNVERIFIED — a set that looks checked and is not.
    """
    import urllib.error

    from eval_builder import schema_check as sc

    atlan, problem = "", ""
    if sc.rest_configured():
        try:
            sc._search({"dsl": {"size": 0, "query": {"term": {"__typeName.keyword": "Table"}}}})
            atlan = f"Atlan REST at {os.environ['ATLAN_BASE_URL']}"
        except urllib.error.HTTPError as e:
            problem = f"Atlan REST configured but returned HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            problem = f"Atlan REST configured but unreachable ({type(e).__name__})"

    if check_warehouse_config().status is Status.OK:
        return Result("golden_sql.check", Status.OK,
                      "executed on Snowflake at review"
                      + (f"; if it cannot connect, columns checked via {atlan}" if atlan else ""))
    if atlan:
        return Result("golden_sql.check", Status.OK,
                      f"no warehouse → columns checked against {atlan} "
                      "(the catalog as of its last crawl)")

    mcp = [n for n, ok in _mcp_servers() if ok and "atlan" in n.lower()]
    if mcp:
        return Result("golden_sql.check", Status.WARN,
                      f"no warehouse, no Atlan API key · Atlan MCP: {mcp[0]}"
                      + (f" · {problem}" if problem else ""),
                      f"after build, fetch the tables in work/{sc.REQUEST_FILE} with the "
                      f"Atlan MCP, save them to work/{sc.SCHEMA_FILE}, re-run build")
    return Result("golden_sql.check", Status.WARN,
                  "golden SQL will NOT be checked — no warehouse, no Atlan"
                  + (f" ({problem})" if problem else ""),
                  "set SNOWFLAKE_*, or ATLAN_BASE_URL + ATLAN_API_KEY; with an Atlan MCP "
                  f"in your own tool list, save work/{sc.SCHEMA_FILE} after build. "
                  "Otherwise every record is kept UNVERIFIED")


ALL_CHECKS.append(check_golden_sql)


# --------------------------------------------------------------------------
# BI sources — what `build --from-sigma` and `build --from-bi` read
# --------------------------------------------------------------------------

def check_atlan_credentials() -> Result:
    """Whether `build --from-sigma` can read a workbook from Atlan.

    Sigma tiles come from Atlan's crawl -- elements and their SQL, fields,
    column lineage -- so for Sigma the key is the source itself, not an extra.
    A saved export replays without it.
    """
    missing = [k for k in ("ATLAN_BASE_URL", "ATLAN_API_KEY") if not os.environ.get(k)]
    if missing:
        return Result("atlan.credentials", Status.WARN,
                      f"{' / '.join(missing)} unset — `build --from-sigma` cannot read Atlan",
                      "set ATLAN_BASE_URL and ATLAN_API_KEY, or replay a saved export with "
                      "`build --from-sigma <workbook> --sigma-export <dir>`")
    return Result("atlan.credentials", Status.OK,
                  f"set for {os.environ['ATLAN_BASE_URL']} — "
                  "`build --from-sigma <workbook>` can read Sigma from Atlan")


SQLGLOT_TESTED = (30, 20)          # the release the test suite passes on; pyproject pins it


def check_sqlglot() -> Result:
    """Tile SQL is cut and checked with sqlglot's AST and optimizer, which
    change between minor releases: 30.21 fails 33 tests that 30.20 passes, and
    tiles come out wrong without an error. Anything but the tested release warns."""
    try:
        import sqlglot
    except ImportError:
        return Result("sqlglot.version", Status.FAIL, "sqlglot not installed",
                      "run `uv sync` in the skill directory", gates=["build"])
    version = getattr(sqlglot, "__version__", "")
    m = re.match(r"\d+", version)
    if not m or int(m.group()) < 30:
        return Result("sqlglot.version", Status.FAIL, f"{version or 'unknown'} — 30 or newer required",
                      "upgrade it; pyproject.toml pins sqlglot>=30.20,<30.21", gates=["build"])
    parts = tuple(int(x) for x in re.findall(r"\d+", version)[:2])
    if parts != SQLGLOT_TESTED:
        return Result("sqlglot.version", Status.WARN,
                      f"{version} — tile SQL is tested on {SQLGLOT_TESTED[0]}.{SQLGLOT_TESTED[1]}.x",
                      "run with the skill's pinned environment (`uvx --from <skill>`), or "
                      "install sqlglot>=30.20,<30.21")
    return Result("sqlglot.version", Status.OK, version)


ALL_CHECKS.append(check_atlan_credentials)
ALL_CHECKS.append(check_sqlglot)


def check_tableau_inputs(root: Path) -> Result:
    """What a `build --from-bi` folder holds. Run for the folder given to
    `doctor --from-bi`, not by default."""
    if not root.is_dir():
        return Result("tableau.inputs", Status.FAIL, f"no such folder: {root}",
                      "pass the folder the .twb/.tds exports were saved to")
    suffixes = [p.suffix.lower() for p in root.rglob("*") if p.is_file()]
    workbooks = sum(s in (".twb", ".twbx") for s in suffixes)
    sources = sum(s in (".tds", ".tdsx") for s in suffixes)
    detail = (f"{workbooks} workbook(s) (.twb/.twbx), "
              f"{sources} published datasource(s) (.tds/.tdsx) in {root}")
    if not workbooks:
        return Result("tableau.inputs", Status.WARN, detail,
                      "tiles come from workbooks; datasources alone give "
                      "`build --from-bi` nothing to read")
    return Result("tableau.inputs", Status.OK, detail)


def check_powerbi_inputs(root: Path) -> Result:
    """What a `build --from-powerbi` path holds, and whether each report's
    semantic model can be read. Run for `doctor --from-powerbi`, not by default."""
    from eval_builder.sources.powerbi import discover

    try:
        projects, skipped = discover(root)
    except FileNotFoundError as e:
        return Result("powerbi.inputs", Status.FAIL, str(e),
                      "pass a .pbix/.pbip/.pbit file or the folder holding them")
    if not projects:
        return Result("powerbi.inputs", Status.WARN, f"no Power BI report in {root}",
                      "save the report as .pbip (File > Save as) or pass the .pbix")
    kinds = {}
    for p in projects:
        kinds[p.path.suffix.lower() or "folder"] = kinds.get(p.path.suffix.lower() or "folder", 0) + 1
    detail = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) + f" in {root}"
    if any(p.path.suffix.lower() == ".pbix" for p in projects):
        try:
            import pbixray  # noqa: F401
        except ImportError:
            return Result("powerbi.inputs", Status.WARN, detail + "; .pbix models need pbixray",
                          "run with `uvx --with pbixray --from <skill> eval-builder …`, or save "
                          "the report as .pbip, whose TMDL model needs nothing extra")
    if skipped:
        detail += f"; skipped {len(skipped)}: {skipped[0]}"
    return Result("powerbi.inputs", Status.OK, detail)


# Databricks SQL warehouse, through databricks-sql-connector's own variables.
# Imported lazily, like Snowflake's driver: a build that never executes on
# Databricks never needs it.
_DBX_REQUIRED = ("DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN")


def _dbx_connect():
    missing = [k for k in _DBX_REQUIRED if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"missing {', '.join(missing)}")
    from databricks import sql as dbsql  # optional dependency

    return dbsql.connect(server_hostname=os.environ["DATABRICKS_SERVER_HOSTNAME"],
                         http_path=os.environ["DATABRICKS_HTTP_PATH"],
                         access_token=os.environ["DATABRICKS_TOKEN"])


def connect(dialect: str):
    """A DB-API connection to the warehouse a record's dialect runs on."""
    if dialect == "snowflake":
        return _sf_connect()
    if dialect == "databricks":
        return _dbx_connect()
    raise RuntimeError(f"no {dialect} connection configured in this skill")
