#!/usr/bin/env python3
"""Inspect task-scoped publication authorization; makes no Registry calls.

The assistant records trusted user/session decisions in a private JSON file. Plan permission
is checked against the selected workspace and source identities, never inferred from a BI file.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "metrics-generation"))
from modules.workflow import Authorization
from modules.metrics.publication import publication_identity
from modules.metrics.holds import enforce_context


def _inventory_mismatch(context, workspace):
    """Why the reconciled inventory cannot back a plan for `workspace`, or None.

    The checked plan is only as good as the live inventory it was reconciled against. Two Atlan
    connections in one host can reach different organizations, each with its own personal
    workspace; an inventory read through one says nothing about a workspace written through the
    other. Every live row, and the optional `inventory.json` record, must name the target."""
    rows = json.loads((context / 'existing.json').read_text()) if (context / 'existing.json').exists() else []
    other = sorted({r.get('workspace_id') for r in rows if isinstance(r, dict)
                    and r.get('workspace_id') and r.get('workspace_id') != workspace})
    if other:
        return ('The live inventory was read from another workspace (' + ', '.join(other) + '). '
                'Read the target workspace through the connection that will write, then check again.')
    record = context / 'inventory.json'
    if record.exists():
        seen = json.loads(record.read_text()).get('workspace_id')
        if seen and seen != workspace:
            return ('The inventory record names workspace ' + str(seen) + ', not the target. '
                    'Read the target workspace through the connection that will write, then check again.')
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authorization', type=Path, required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--source', action='append', required=True)
    args = parser.parse_args()
    try:
        policy = Authorization.from_record(json.loads(args.authorization.read_text()))
        documents = {name: json.loads((args.context / (name + '.json')).read_text())
                     for name in ('plan', 'metrics', 'queries', 'links')}
        holds = enforce_context(args.context, documents['metrics'], documents['queries'], documents['links'])
        if holds is not None:
            documents['publication_holds'] = holds
        inventory_mismatch = _inventory_mismatch(args.context, args.workspace)
        if inventory_mismatch:
            print(json.dumps({'decision': 'needs_context', 'reason': inventory_mismatch}))
            return 2
        plan_id, revisions = publication_identity(workspace_id=args.workspace,
                                                  sources=args.source, documents=documents)
        result = policy.publication(workspace_id=args.workspace, plan_id=plan_id,
                                    sources=args.source, revisions=revisions)
        print(json.dumps({**result, 'plan_id': plan_id, 'revisions': revisions}))
        return 0 if result['decision'] == 'continue' else 2
    except (ValueError, OSError) as exc:
        print(json.dumps({'decision': 'needs_context', 'reason': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
