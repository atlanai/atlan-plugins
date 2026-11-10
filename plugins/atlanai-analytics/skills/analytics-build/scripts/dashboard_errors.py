"""Public error vocabulary. Never serialize arbitrary exception text or subprocess output."""

ERRORS = {
    'invalid_request': ('operation', 'The request could not be applied.', 'Read this operation schema and correct the request.', 'after_correction'),
    'local_io': ('operation', 'The local run could not be read or saved.', 'Check access to the private run directory, then resume.', 'after_correction'),
    'revision_conflict': ('operation', 'The run advanced while this step was prepared.', 'Read the current run and resume its pending step.', 'read_state'),
    'metric_not_usable': ('questions', 'This metric needs correction or validation.', 'Choose another available metric while this one is repaired.', 'after_correction'),
    'validation_case_required': ('validation', 'This check is not bound to the current evaluation plan.', 'Get the current check and use its case ID and generation.', 'read_state'),
    'scope_incomplete': ('conversion', 'The report scope is incomplete.', 'Confirm its filters, caller security scope and data cutoff.', 'after_correction'),
    'decoder_missing': ('extraction', 'The Power BI decoder is not installed.', 'Approve the bundled PBIXRay setup once, then resume this run.', 'after_setup'),
    'decode_failed': ('extraction', 'The downloaded model could not be decoded.', 'Supply a supported model export for this report.', 'after_correction'),
    'linked_model_required': ('extraction', 'This report has no embedded readable model.', 'Supply its linked semantic model or an authorized model export.', 'after_correction'),
    'source_file_required': ('extraction', 'The report file is unavailable or unsupported.', 'Supply an ordinary local PBIX, PBIT or BIM file.', 'after_correction'),
    'warehouse_cli_missing': ('warehouse', 'Snowflake CLI is unavailable.', 'Install an approved Snowflake CLI, then resume.', 'after_setup'),
    'warehouse_connection_required': ('warehouse', 'This run has no warehouse connection yet.', 'Discover an existing profile using the report account, role and warehouse.', 'after_setup'),
    'warehouse_sql_rejected': ('validation', 'Snowflake rejected this query.', 'Repair its SQL or object access; keep the existing connection.', 'after_correction'),
    'warehouse_auth_required': ('warehouse', 'Snowflake sign-in needs attention.', 'Complete sign-in once, then explicitly reconnect and resume pending checks.', 'after_sign_in'),
    'warehouse_paused': ('warehouse', 'Warehouse checks are paused.', 'Resolve the connection issue, then explicitly reconnect once.', 'explicit_reconnect'),
    'warehouse_timeout': ('warehouse', 'The warehouse operation timed out.', 'Check warehouse availability and query duration, then reconnect once to resume.', 'explicit_reconnect'),
    'warehouse_cli_incompatible': ('warehouse', 'This Snowflake CLI could not run the requested operation.', 'Check CLI compatibility before retrying.', 'after_setup'),
    'warehouse_profile_ambiguous': ('warehouse', 'More than one Snowflake profile matches.', 'Choose one of the matching profile names; no credentials are needed.', 'after_choice'),
    'no_usable_metrics': ('conversion', 'No usable SQL metrics are available yet.', 'Resolve a supported measure and its mapping before continuing.', 'after_correction'),
    'run_mismatch': ('resume', 'This directory belongs to another dashboard or context.', 'Resume its original dashboard or choose a new run directory.', 'after_choice'),
    'pending_operation': ('publication', 'A previous operation has no verified result.', 'Read back its saved identity before any retry; continue with local questions.', 'readback_only'),
    'release_receipt_required': ('publication', 'The latest-release receipt is incomplete.', 'Copy the actual latest-read release selector into receipt.release_id or artifact.release_id, then acknowledge again.', 'readback_only'),
    'publication_readback_mismatch': ('publication', 'The readback differs from the reviewed publication.', 'Reread the exact artifact in the selected workspace and reconcile its definition before proceeding.', 'readback_only'),
    'publication_plan_changed': ('publication', 'The context or approved publication plan changed.', 'Read current inventory and review a new plan; keep local questions available.', 'after_review'),
}

# Exact, developer-authored messages only. Unknown messages never leave the process.
MESSAGES = {
    'Dashboard changed; read its current revision before continuing.': 'revision_conflict',
    'This metric needs correction or validation before answering.': 'metric_not_usable',
    'Validation requires the current planned case and generation.': 'validation_case_required',
    'Complete the selected scope before SQL conversion.': 'scope_incomplete',
    'Complete filters, security scope and data cutoff before preparing context.': 'scope_incomplete',
    'No warehouse metrics are ready to prepare.': 'no_usable_metrics',
    'Supply the absolute path to the downloaded PBIX, PBIT or BIM file.': 'source_file_required',
    'PBIXRay runtime is missing. Run the packaged scripts/setup_pbixray.py once after authorizing dependency installation.': 'decoder_missing',
    'Local PBIX extraction failed. Check the file and installed parser; existing job state is unchanged.': 'decode_failed',
    'Local PBIX extraction could not complete within two minutes. Existing job state is unchanged.': 'decode_failed',
    'This file has no embedded readable model. Retrieve the linked semantic model through the existing authorized connection.': 'linked_model_required',
    'Snowflake CLI is not installed or discoverable. Supply an approved installation; no automatic install was attempted.': 'warehouse_cli_missing',
    'Snowflake CLI could not start.': 'warehouse_cli_missing',
    'Connect Snowflake CLI for this job first.': 'warehouse_connection_required',
    'Snowflake CLI discovery or sign-in is unavailable. Check CLI compatibility or complete browser sign-in; raw output was withheld.': 'warehouse_cli_incompatible',
    'Browser sign-in did not produce a matching connection. Pause setup; do not guess a username.': 'warehouse_auth_required',
    'Snowflake rejected the SQL. Check the translation and object permissions; do not reconnect or request another login for this query error.': 'warehouse_sql_rejected',
    'Snowflake CLI options are unsupported or invalid. Check CLI compatibility; do not retry with credential reuse disabled.': 'warehouse_cli_incompatible',
    'Snowflake authentication needs attention. Warehouse checks are paused; reconnect once after sign-in is resolved, then resume the pending cases.': 'warehouse_auth_required',
    'Snowflake operation timed out; the warehouse checks are paused. A timeout does not prove the login expired. Resolve the connection or query timeout, then explicitly reconnect once.': 'warehouse_timeout',
    'Snowflake CLI could not complete the operation. Warehouse checks are paused; inspect connection availability and scope before an explicit reconnect. No authentication failure was confirmed.': 'warehouse_paused',
    'Warehouse checks are paused. Resolve the connection issue and explicitly reconnect once; do not retry sign-in for every metric.': 'warehouse_paused',
    'Browser sign-in was already attempted. Pause setup; retry_login is only for a user-requested retry, never a per-metric loop.': 'warehouse_paused',
    'Resolve the outstanding call before preparing another plan.': 'pending_operation',
    'Record the release selector returned by the latest read.': 'release_receipt_required',
    'Release selectors disagree; reread the current release.': 'publication_readback_mismatch',
    'Readback differs from the reviewed definition or workspace.': 'publication_readback_mismatch',
    'Receipt names another artifact.': 'publication_readback_mismatch',
    'Approval must name the exact reviewed plan.': 'publication_plan_changed',
    'Context changed; reconcile and review a new plan.': 'publication_plan_changed',
}


class FlowError(ValueError):
    def __init__(self, code):
        self.code = code if code in ERRORS else 'invalid_request'
        super().__init__(self.code)


def public_error(exc):
    code = getattr(exc, 'code', None) or MESSAGES.get(str(exc))
    if code not in ERRORS:
        code = 'local_io' if isinstance(exc, OSError) else 'invalid_request'
    stage, message, action, retry = ERRORS[code]
    result = {'status': 'needs_attention', 'code': code, 'stage': stage,
            'message': message, 'next_action': action, 'retry': retry,
            'progress_preserved': True, 'other_usable_metrics': 'continue'}
    if code == 'warehouse_profile_ambiguous':
        import re
        choices = getattr(exc, 'choices', [])
        result['choices'] = [s for s in choices if isinstance(s, str) and re.fullmatch(r'[A-Za-z0-9_.@+\-]{1,128}', s)][:8]
    return result
