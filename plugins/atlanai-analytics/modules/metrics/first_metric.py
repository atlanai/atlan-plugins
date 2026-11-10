"""Check a provisional engine card through the same read-only SQL socket and witness."""


def check(card, inputs):
    from metrics_agent import plug_runtime
    from metrics_agent.lib.card_sql import canonical_statement
    from metrics_agent.lib.witness import witness

    sql = canonical_statement(card)
    dialect = card.get('dialect') or inputs['dialect']
    mode = inputs.get('witness_mode', 'parse')
    # Parameterized/viewer-scoped previews need the engine's full binding evidence. Never
    # invent defaults or widen a population just to display an early number.
    if '{{' in sql or card.get('viewer_binds') or card.get('withheld_binds'):
        return {'result': 'untested', 'reason': 'Binding evidence is pending full engine validation.'}
    if mode not in ('execute', 'explain'):
        return witness(sql, dialect, mode, parameters=card.get('parameters'))
    try:
        with plug_runtime.sql_runner(inputs, dialect) as runner:
            return witness(sql, dialect, mode, eng=runner, limit=50, sample_rows=1,
                           parameters=card.get('parameters'))
    except Exception:
        # Connector failures may contain credentials; retain no exception text or raw rows.
        return {'result': 'untested', 'reason': 'SQL check unavailable; inspect the configured input plug.'}
