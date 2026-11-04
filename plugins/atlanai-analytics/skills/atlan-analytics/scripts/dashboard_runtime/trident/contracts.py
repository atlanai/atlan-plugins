"""Closed MCP inputs. No raw executable commands or credential fields."""
from .metric_contract import _shape, _json_limits


def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


def string(n=2000):
    return {'type': 'string', 'minLength': 1, 'maxLength': n}


def array(items, maximum=200):
    return {'type': 'array', 'items': items, 'maxItems': maximum}


SCALAR = {'type': ['string', 'number', 'boolean', 'null']}
UNAVAILABLE = {'type': 'string', 'enum': ['source_connection_required', 'warehouse_connection_required',
               'source_value_unavailable', 'safe_binding_unavailable', 'snapshot_mismatch']}
FILTER = obj({'table': string(256), 'column': string(256), 'values': array(SCALAR, 200)}, ('table', 'column', 'values'))
CONTEXT = obj({'page': string(256), 'filters': array(FILTER, 50), 'data_as_of': string(100),
               'security_scope': string(500), 'timezone': string(100), 'visual_scope': string(500)},
              ('page', 'filters', 'data_as_of', 'security_scope'))
BUSINESS_CONTEXT = obj({'population': string(2000), 'grain': string(1000), 'unit': string(100),
                        'exclusions': array(string(1000), 50), 'evidence_ref': string(1000)}, ('evidence_ref',))
MEASURE = obj({'source_id': string(512), 'identity_kind': {'type': 'string', 'enum': ['upstream_id', 'name_fallback']},
               'table': string(256), 'name': string(256), 'expression': string(50000),
               'datatype': {'type': 'string', 'enum': ['integer', 'decimal', 'string', 'boolean', 'date', 'timestamp']},
               'description': {'type': 'string', 'maxLength': 4000}, 'business_context': BUSINESS_CONTEXT, 'evidence_ref': string(1000)},
              ('source_id', 'identity_kind', 'table', 'name', 'expression', 'datatype', 'evidence_ref'))
CAPTURE = obj({'source_url': string(4096), 'model_ref': string(256), 'title': string(256),
               'mode': {'type': 'string', 'enum': ['browser', 'connector', 'export']},
               'scope_complete': {'type': 'boolean'}, 'context': CONTEXT, 'measures': array(MEASURE, 500)},
              ('source_url', 'model_ref', 'title', 'mode', 'scope_complete', 'context', 'measures'))
OBSERVATION = obj({'value': SCALAR, 'context_hash': string(64), 'evidence_ref': string(1000),
                   'executor': {'type': 'string', 'enum': ['powerbi', 'warehouse']}, 'query_hash': string(64),
                   'unavailable': UNAVAILABLE,
                   'error': {'type': ['string', 'null'], 'maxLength': 1000}}, ('context_hash', 'evidence_ref'))
CASE = obj({'case_id': string(100), 'question': string(2000), 'metric_id': string(100),
            'capture_hash': string(64), 'definition_hash': string(64), 'reference': OBSERVATION,
            'actual': OBSERVATION, 'absolute_tolerance': string(40)},
           ('case_id', 'question', 'metric_id', 'capture_hash', 'definition_hash', 'reference', 'actual'))
CHECK = obj({'observation': OBSERVATION, 'unavailable': UNAVAILABLE})
MUTATION = {'job_id': string(28), 'expected_revision': {'type': 'integer', 'minimum': 0}}
COLUMN_MAPPING = obj({'semantic': string(256), 'physical': string(256),
                      'datatype': {'type': 'string', 'enum': ['integer','decimal','string','boolean','date','timestamp']}},
                     ('semantic','physical','datatype'))
MAPPING = obj({'semantic_table': string(256), 'relation': array(string(256), 3),
               'rowset_sql': string(100000),
               'columns': array(COLUMN_MAPPING, 500), 'rowset_equivalent': {'type':'boolean'},
               'security_preserved': {'type':'boolean'}, 'mandatory_filters': array(FILTER, 50), 'evidence_ref':string(1000)},
              ('semantic_table','relation','columns','rowset_equivalent','security_preserved','evidence_ref'))
SNOW_CONFIG = obj({'connection': string(128), 'account': string(256), 'role': string(256), 'warehouse': string(256),
                   'database': string(256), 'schema': string(256), 'user': string(256),
                   'authenticator': {'type':'string','enum':['OAUTH_AUTHORIZATION_CODE','externalbrowser']}},
                  ('role','warehouse'))
SCHEMAS = {
    'dashboard_import_file': obj({**MUTATION,'path':string(4096),'model_ref':string(256),'context':CONTEXT,'scope_complete':{'type':'boolean'}},(*MUTATION,'path','model_ref','context')),
    'dashboard_snowflake_connect': obj({**MUTATION,'connection':SNOW_CONFIG}, (*MUTATION,'connection')),
    'dashboard_snowflake_execute': obj({'job_id':string(28),'metric_id':string(100),'context_hash':string(64)},
                                     ('job_id','metric_id','context_hash')),
    'dashboard_begin': obj({'url': string(4096)}, ('url',)),
    'dashboard_identify': obj({**MUTATION, 'source_url': string(4096), 'title': string(256),
                               'evidence_ref': string(1000)}, (*MUTATION, 'source_url', 'title', 'evidence_ref')),
    'dashboard_capture': obj({**MUTATION, 'capture': CAPTURE}, (*MUTATION, 'capture')),
    'dashboard_read': obj({'job_id': string(28), 'include_definitions': {'type': 'boolean'}, 'definition_offset': {'type':'integer','minimum':0}, 'definition_limit': {'type':'integer','minimum':1,'maximum':10}}, ('job_id',)),
    'dashboard_translate': obj({**MUTATION, 'dialect': {'type':'string','enum':['snowflake','postgres','sqlite']},
                                'mappings':array(MAPPING,100)}, (*MUTATION,'dialect','mappings')),
    'dashboard_evaluate': obj({**MUTATION, 'cases': array(CASE)}, (*MUTATION, 'cases')),
    'dashboard_plan_evals': obj({**MUTATION, 'questions': array(obj({'metric_id': string(100),
                               'question': string(2000), 'evidence_ref': string(1000)},
                               ('metric_id','question','evidence_ref')),500)}, tuple(MUTATION)),
    'dashboard_next_check': obj({'job_id': string(28), 'case_id': string(100)}, ('job_id',)),
    'dashboard_review': obj({'job_id': string(28)}, ('job_id',)),
    'dashboard_record_check': obj({**MUTATION, 'case_id': string(100), 'capture_hash': string(64),
                                   'definition_hash': string(64), 'plan_generation': string(24), 'reference': CHECK, 'actual': CHECK,
                                   'absolute_tolerance': string(40)},
                                  (*MUTATION, 'case_id', 'capture_hash', 'definition_hash', 'plan_generation', 'reference', 'actual')),
    'dashboard_answer_plan': obj({'job_id': string(28), 'metric_id': string(100), 'context_hash': string(64)},
                                 ('job_id', 'metric_id', 'context_hash')),
    'dashboard_export': obj({**MUTATION, 'workspace_id': string(128)}, tuple(MUTATION)),
}
DESCRIPTIONS = {
    'dashboard_import_file': 'Decode the explicitly downloaded Power BI file locally with PBIXRay and import every measure into the current identified dashboard. Confirm the file belongs to this report and supply its model reference and observed context. Reads PBIX binary or textual PBIT/BIM plus legacy/PBIR report layout; never executes M/DAX or uploads the file. Defaults to incomplete scope. Returns inventory UI and a private metadata file with mappings, relationships and security evidence for SQL authoring. Uses the preinstalled pinned runtime; never auto-installs dependencies.',
    'dashboard_snowflake_connect': 'Connect with the installed Snowflake CLI using a named profile or temporary browser OAuth. Empty saved connection lists do not prevent temporary OAuth. Discovers existing local installations without installing anything. Requires an explicit approved query role and warehouse; no credentials are accepted. Persists non-secret connection metadata only after a read-only probe succeeds.',
    'dashboard_snowflake_execute': 'Execute the selected stored Snowflake metric through the connected CLI. Values are separately bound with Snowflake Scripting USING, not -D templates. Requires exact context, returns a fresh scalar observation and query hash. Does not claim the warehouse data cutoff matches the dashboard; establish snapshot equivalence separately before grading.',
    'dashboard_review': 'Show one compact readiness card after the check loop, or when the user requests interim progress. Includes all planned cases, unresolved issues and checked question suggestions. Read-only; never reruns queries or exposes gold. Avoid calling after every individual check.',
    'dashboard_plan_evals': 'After metric SQL is built, freeze one dashboard-grounded question per captured measure. Optionally supply business questions with evidence references; they must preserve the compiled metric scope and scalar answer. Missing questions use explicit measure baselines. No gold values are invented. Resets earlier checks. Continue to real source/warehouse execution. This is dashboard parity, not held-out accuracy.',
    'dashboard_next_check': 'Get the next unrun planned check with exact source-measure request and warehouse SQL/bindings. No gold is returned. Host must execute both requests through authorized tools, then record results. Optional case_id retries an existing check. No network execution occurs in this local tool.',
    'dashboard_record_check': 'Record one planned check using observed source/warehouse results or explicit unavailable reasons. Require the task capture and definition hashes. Accumulates results without dropping other checks; pending and unsupported measures remain in coverage. Returns compact readiness and verified question suggestions.',
    'dashboard_answer_plan': 'Route a resolved metric question to fresh warehouse SQL with separate bindings and current validation status. Requires exact context hash; changed scope requires new translation/checks. Does not execute a query or return eval gold. Use native chat for the final observed answer.',
    'dashboard_identify': 'Show the first compact dashboard card only after authorized source access confirms its actual name. Bind the observed title and evidence to the exact source URL. Do not call from URL parsing alone. Inventory appears only after dashboard_capture completes.',
    'dashboard_translate': 'Compile supported captured DAX into parameterized warehouse SQL using exact physical rowset/column mappings. Preserves CALCULATE filter replacement and blank/count semantics. Requires source transformation and security mappings, refuses unsupported constructs, records per-metric gaps, and invalidates prior evaluations. SQL candidates are not parity-validated until actual warehouse results are checked. No warehouse or source calls.',
    'dashboard_begin': 'Start from the exact Power BI report URL. Creates local job state and returns a scoped capture request. Does not access the report, authenticate, install a connector or claim success. Continue through the host browser/connector using the dashboard-agent skill.',
    'dashboard_capture': 'Normalize host-captured Power BI measures into native Registry draft-4 definitions, preserving DAX and source execution requirements. Capture all report filters, source types and identities. No guessed SQL, remote save or certification. Recapture invalidates old evals.',
    'dashboard_read': 'Read current source readiness, parity summary and optionally exact metric definitions. Never returns gold values or grader files. Use for reconnect/resume and answering with the declared warehouse executor and separate SQL bindings. Unconverted DAX remains a migration gap.',
    'dashboard_evaluate': 'Compare host-reported scalar dashboard references to executed answers at matching definition and filter hashes. Invalid scopes are unmeasured; errors are failures; zero and missing differ. Stores grader-only references locally. This is parity, not held-out accuracy or independent execution verification.',
    'dashboard_export': 'Write local Registry metric draft envelope templates, runtime context, eval record drafts and the report. No network writes. Gold stays local and out of runtime context; Registry dataset drafts contain only gold digests. Existing source identity and live capabilities must be reconciled before any approved remote save.',
}


def validate(name, args):
    if name not in SCHEMAS or _json_limits(args):
        raise ValueError('Unsupported tool or oversized request.')
    errors = []
    _shape(args, SCHEMAS[name], 'arguments', errors)
    # The reused metric validator implements typed structure; enforce generic bounds here too.
    def bounds(schema, value):
        if isinstance(value, str) and not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 262144):
            raise ValueError('Text is outside the supported bounds.')
        if 'enum' in schema and value not in schema['enum']:
            raise ValueError('Unsupported option.')
        if isinstance(value, list):
            if len(value) > schema.get('maxItems', 1024):
                raise ValueError('Too many items.')
            for item in value:
                bounds(schema.get('items', {}), item)
        if isinstance(value, dict):
            for key, item in value.items():
                bounds(schema.get('properties', {}).get(key, {}), item)
    if errors:
        raise ValueError('Tool arguments do not match the supported schema.')
    bounds(SCHEMAS[name], args)
