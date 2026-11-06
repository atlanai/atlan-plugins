#!/usr/bin/env python3
"""Verify the offline Claude journey from independent adapter records and durable receipts."""
import argparse
import json
from pathlib import Path


def verify(root):
    calls = [json.loads(line) for line in (root/'fixture/calls.jsonl').read_text().splitlines()]
    manifest = json.loads((root/'.atlan-analytics/example/run.json').read_text())
    state = json.loads((root/'.atlan-analytics/example/jobs'/manifest['job_id']/'state.json').read_text())
    registry = json.loads((root/'fixture/registry.json').read_text())
    creates = [c for c in calls if c['name']=='fixture_registry' and c['arguments']['name']=='registry__create_artifact']
    metrics = [a for a in registry.values() if a['kind']=='metric']
    agents = [a for a in registry.values() if a['kind']=='agent']
    last_query = [c for c in calls if c['name']=='fixture_query'][-1]
    progress = manifest['progress']
    checks = {
        'all_measures_retained': len(state['metrics'])==4,
        'partial_validation_preserved': sorted(c['verdict'] for c in state['evals'])==['pass','snapshot_mismatch','wrong_result'],
        'usable_subset_saved': len(metrics)==2 and {a['display_name'] for a in metrics}=={'Active accounts','Total amount'},
        'releases_read_back': len(metrics)==2 and all(a.get('release_id') for a in metrics) and progress['publication']['state']=='released',
        'no_duplicate_creates': len(creates)==3 and len(agents)==1,
        'agent_receipt_verified': manifest['stages'].get('agent',{}).get('state')=='complete',
        'questions_remain_available': progress['questions_available'] and progress['usable_metrics']==2,
        'ready_label_requires_saved_agent': progress['label']=='Agent ready to try',
        'fresh_answer_after_data_change': last_query['result']['rows']==[[50]],
        'one_data_advance': sum(c['name']=='fixture_advance' for c in calls)==1,
        'heldout_not_fabricated': 'heldout_eval' not in manifest['stages'],
    }
    events = [json.loads(line) for line in (root/'transcript.jsonl').read_text().splitlines()]
    result = next((event for event in reversed(events) if event.get('type')=='result'), {})
    checks['claude_completed'] = result.get('subtype')=='success' and not result.get('is_error')
    report = {'status':'passed' if all(checks.values()) else 'failed','checks':checks,
              'scope':'Claude Code with synthetic Power BI export, SQLite warehouse and offline Registry adapter; not live service validation',
              'final_message':result.get('result'), 'cost_usd':result.get('total_cost_usd')}
    (root/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('directory');args=parser.parse_args()
    report=verify(Path(args.directory).resolve())
    print(json.dumps(report,indent=2))
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
