# Threat model

What the agent protects, who could attack it and where, and how each threat is met. Every
mitigation records its verification intent. The historical engine suite is omitted from this
repository; focused plugin integration checks cover the port. Changes must preserve these protections. Follow the repository SECURITY.md to report a
vulnerability.

**On this page:** [what the agent is](#what-the-agent-is) · [assets](#assets) ·
[actors](#actors) · [trust boundaries](#trust-boundaries) · [threats](#threats-and-mitigations) ·
[out of scope](#out-of-scope)

## What the agent is

A command-line program that runs on the operator's machine. It reads a data estate's query
history and BI definitions through plugs, builds verified SQL and metric definitions in a run
directory, and optionally publishes them to the Atlan Agent Registry and an Atlan glossary.
Judgement calls are made at **authored gates**: the agent writes a prompt bundle, the operator's
own AI assistant answers it, and the agent validates the answer. The agent holds no model key
and calls no model.

## Assets

| Asset | Why it matters |
|---|---|
| Credentials: warehouse DSN, Atlan, Registry, trace, Fabric, Tableau and catalogue secrets | they reach the customer's systems |
| The warehouse and its data | the agent runs SQL against it |
| Query history | holds the SQL people ran, user names and literal values typed into it |
| BI definitions | hold business logic and prose people wrote |
| The published layer: Registry artifacts, glossary terms and their links | other people and other agents rely on it |
| The run directory | holds everything above that the run has read or built |

## Actors

| Actor | Trusted for | Not trusted for |
|---|---|---|
| The operator | choosing the inputs, the destinations and the plugs; answering the gates | nothing is relaxed by asking: no flag lowers a gate |
| The operator's AI assistant | answering a gate's bundle | its answer is checked, never trusted; it may have been misled by the data |
| Authors of SQL and BI content (analysts, report builders) | : | anything they wrote is data: SQL text, comments, titles, descriptions, names |
| Plug authors | a plug the operator installs is trusted code | a plug's name, its import, and the records it returns are checked |
| Destination tenants (the Registry, an Atlan tenant) | storing what they are sent | being reached with a credential at an address the policy does not allow |

## Trust boundaries

1. **Warehouse:** the agent sends SQL to the customer's warehouse through a plug.
2. **BI files and fetched extracts:** archives and folders the agent unpacks and parses.
3. **Gate bundles:** the agent's prompt bundle goes to the assistant; the answer comes back.
4. **Plug loading:** packages register plugs through entry points, and the agent imports them.
5. **Destinations:** the agent sends the layer, with a credential, to the Registry and Atlan.
6. **Tracing:** an optional observer sends run records to the Registry.

## Threats and mitigations

### Warehouse

| Threat | Mitigation | Proved by |
|---|---|---|
| A statement that writes, or a write hidden in a "read", reaches the warehouse | every statement passes a read-only gate before it is sent, in every dialect; every shipped warehouse plug is wrapped by it; no gate takes a parameter that could relax it | `test_socket_gates.py`, `test_security_network.py` (`test_anything_that_is_not_a_pure_read_is_refused_in_every_dialect`, `test_every_statement_the_core_executes_passes_the_gate_first`, `test_every_shipped_warehouse_plug_is_wrapped_by_the_gate`) |
| A hostile table or database name becomes SQL injection in a catalogue query | names are checked before they are put in a statement, and a name that is not plain is refused rather than quoted | `test_security_injection.py` (`test_a_hostile_table_name_never_reaches_a_catalogue_query`, `test_a_warehouse_plug_refuses_a_hostile_table_before_building_sql`) |
| A warehouse error prints the connection string | errors are reported without the DSN | `test_security_secrets.py::test_a_warehouse_error_never_prints_the_dsn` |

### BI files and extracts

| Threat | Mitigation | Proved by |
|---|---|---|
| An archive member or a path inside an extract escapes its directory (path traversal) | every archive entry, part path, item id and worksheet name is checked before it becomes a path; a symlink out of an extract is dropped | `test_security_files.py` |
| Prose in a BI file (a title, caption, tooltip or description) carries an instruction | prose is evidence only: it changes no SQL, no label and no structure, and no prose source can authorise anything | `test_security_injection.py` (`test_an_instruction_in_tableau_titles_and_captions_changes_nothing`, `test_an_instruction_in_power_bi_titles_and_descriptions_changes_nothing`, `test_no_prose_source_can_authorise_anything`) |
| Personal data typed into BI prose or SQL literals reaches an artifact | literals matching the policy's PII patterns are redacted before anything is written; a literal is kept only when several queries or several people used it | `test_security_pii.py`, `test_pii_names.py` |

### Gate bundles and the assistant

| Threat | Mitigation | Proved by |
|---|---|---|
| An instruction planted in the data (an SQL comment, a column description) steers the assistant | it reaches a bundle only inside the statement or evidence it came with, never in the bundle's instruction | `test_prompt_injection.py` |
| A fooled assistant returns an answer that changes the SQL, invents an expression or sneaks a write onto a card | each gate's validator refuses it, so a misled session publishes nothing | `test_prompt_injection.py` (`test_obeying_the_comment_at_x1_is_refused`, `test_obeying_the_comment_at_x3_is_refused`), `test_validators.py` |
| The agent itself calls a model with a key, spending someone else's budget | no shipped code imports a model SDK or reads a model key | `test_security_secrets.py::test_no_shipped_code_imports_a_model_sdk_or_reads_a_model_key` |
| A user name in the query history reaches a destination | names are counted, not written, by default; every write stage refuses an artifact that holds one, whatever the policy says | `test_pii_names.py` (`test_every_write_stage_refuses_a_name_in_any_artifact`, `test_no_output_plug_is_ever_handed_a_user_name`) |

### Plug loading

| Threat | Mitigation | Proved by |
|---|---|---|
| A plug registers under a name the core already uses, to stand in for it | a duplicate name is refused | `test_security_supply_chain.py::test_a_plug_claiming_a_core_name_is_refused_as_a_duplicate` |
| A broken plug crashes every run | a plug that raises on import is reported, never a crash | `test_security_supply_chain.py::test_a_plug_that_raises_on_import_is_reported_never_a_crash` |
| A plug reads data a third party holds in a run declared `standalone` | custody is checked at S0, before any capability is called: a third-party plug, or a file whose sidecar says it passed through a third party, is refused by name | `test_security_custody.py`, `test_socket_gates.py::test_an_atlan_plug_in_a_standalone_run_is_refused_by_name_before_any_call` |
| A dependency is swapped or typosquatted | every dependency is pinned and its name reviewed; CI audits the pinned set; every GitHub Action is pinned to a commit SHA | `test_security_supply_chain.py` |

### Destinations

| Threat | Mitigation | Proved by |
|---|---|---|
| A credential is sent to an attacker's host, an internal address or a cloud metadata address (SSRF) | every request that carries a credential checks the destination first: TLS only, never an internal or metadata address, and only a host under the policy's allowlist (`.atlan.com` by default) | `test_security_network.py` |
| The Tableau token is sent to a host other than the operator's Tableau site, or follows a redirect or a URL a workbook names | `TABLEAU_SERVER_URL` is checked before the sign-in (TLS, no path, never an internal or metadata address, a host under `tableau.allowed_host_suffixes`); every request goes to that server on a path built from checked LUIDs; a redirect is refused, never followed; a workbook URL on another host is refused | `test_tableau_cloud.py` (`test_the_token_never_goes_to_a_host_it_must_not_reach`, `test_a_redirect_is_refused_never_followed`, `test_only_a_luid_becomes_a_url_segment`), `test_security_secrets.py::test_the_tableau_fetch_refuses_a_host_without_a_request_or_an_echo` |
| A value from the environment or a name changes the path a request calls | URLs built from the environment are checked, and a workspace name is one URL segment, sent as a parameter | `test_security_network.py::test_every_place_a_url_is_built_from_the_environment_is_checked`, `test_security_injection.py::test_a_workspace_name_is_one_url_segment_never_a_path` |
| The agent overwrites or deletes what people curated | it only reads and creates, except for documented, flagged operations; archiving runs only behind its flag; a person's edit is detected and skipped | `test_security_network.py` (`test_the_agent_sends_only_reads_and_creates_except_the_documented_ones`, `test_every_archive_call_sits_behind_its_flag`) |
| Markup in a card becomes active HTML in a published glossary README | Markdown is escaped on the way to HTML and links only to the tenant | `test_security_injection.py` (`test_md_to_html_escapes_markup_and_links_only_to_the_tenant`, `test_a_card_carrying_markup_publishes_a_safe_readme`) |
| Output goes somewhere the operator did not choose | `publish` in `run.yml` has no default, and every write stage refuses a destination outside it | `test_security_secrets.py`, `test_docs.py` |

### Credentials and local files

| Threat | Mitigation | Proved by |
|---|---|---|
| A secret is printed or written to an artifact, a log or a trace | a whole run with a sentinel in every credential variable is searched afterwards: no output, file or payload holds one; refusals never echo a value | `test_security_secrets.py` (`test_a_whole_run_opens_no_socket_and_no_secret_reaches_any_output`, `test_s0_refusals_never_echo_a_secret`) |
| A credential is written into `run.yml` | it is refused, without echoing it | `test_security_secrets.py::test_a_credential_written_into_run_yml_is_refused_without_echoing_it` |
| Another local user reads the run directory, a cached token or a revealed key | they are created owner-only | `test_security_files.py` (`test_a_run_directory_is_owner_only_from_creation`, `test_a_revealed_agent_key_is_owner_only_even_over_an_older_file`) |
| The core opens a network connection of its own | the core opens no socket; only plugs do, through the checks above | `test_security_secrets.py::test_the_core_opens_no_socket_of_its_own` |

### Tracing

| Threat | Mitigation | Proved by |
|---|---|---|
| The trace key leaks, or the exporter is pointed at an internal host | the key is held in memory only and never printed; the exporter is never attached to an internal host | `test_security_secrets.py::test_the_trace_observer_never_prints_its_key`, `test_security_network.py::test_the_trace_exporter_is_never_attached_to_an_internal_host` |

## Out of scope

- **The assistant's own security.** What the operator's AI assistant, and its model provider, do
  with a bundle is governed by the operator's agreement with them. The README's "What leaves
  your machine" table lists exactly what a bundle holds.
- **A plug the operator installs is trusted code.** Installing a package runs its code with the
  operator's permissions; the agent checks a plug's name, import and records, not its intent.
  Install plugs only from sources you trust.
- **The security of the destinations themselves**, the warehouse, and the machine the agent
  runs on.
- **What the operator chooses to publish.** The agent checks the layer against its rules; whether
  a metric should be shared is the operator's decision.
