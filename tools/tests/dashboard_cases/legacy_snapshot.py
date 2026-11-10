"""Synthetic persisted legacy jobs for readback tests. No metric generation lives here."""
import copy
import json
from pathlib import Path
from trident.core import digest

ROOT = Path(__file__).parent / 'fixtures'


def restore(service, job, dialect='sqlite'):
    state = service.load(job)
    saved = {m['id']: m for m in json.loads((ROOT / f'legacy-metrics-{dialect}.json').read_text())}
    state['metrics'] = [copy.deepcopy(saved[m['id']]) if m['id'] in saved else m for m in state['metrics']]
    state.pop('eval_plan', None)
    state['evals'] = []
    state['status'] = 'translated'
    state['revision'] += 1
    service.write(service.path(job, 'state.json'), state)
    return service.view(state)


def context(service, job, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    state = service.load(job)
    ids = {m['id'] for m in state['metrics']}
    bodies = [m for m in json.loads((ROOT / 'legacy-context.json').read_text()) if m['name'] in ids]
    from trident.evaluation import validation_status
    for body in bodies:
        metric = next(m for m in state['metrics'] if m['id'] == body['name'])
        body['definition']['extensions']['atlan_analytics']['dashboard_validation'] = validation_status(state, metric)
    source = {'job_id': job, 'capture_hash': state['capture_hash'], 'context_hash': state['context_hash'],
              'definitions_hash': digest([{k: m.get(k) for k in ('id', 'definition', 'translation')}
                                          for m in state['metrics']])}
    for filename, data in {'metrics.json': bodies, 'queries.json': [], 'links.json': [],
                           'dashboard-source.json': source}.items():
        (destination / filename).write_text(json.dumps(data))
