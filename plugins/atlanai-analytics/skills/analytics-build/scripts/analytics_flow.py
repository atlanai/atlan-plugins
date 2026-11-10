#!/usr/bin/env python3
"""Inspect task-scoped publication authorization; makes no Registry calls.

The assistant records trusted user/session decisions in a private JSON file. Plan permission
is checked against the selected workspace and source identities, never inferred from a BI file.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "analytics-metrics"))
from modules.workflow import Authorization
from modules.metrics.publication import publication_identity
from modules.metrics.holds import enforce_context


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
