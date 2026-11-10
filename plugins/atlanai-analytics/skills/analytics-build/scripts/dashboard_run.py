"""One durable run index; reconcile evidence, never infer remote success from a local export."""
import copy
import json
from pathlib import Path

from dashboard_errors import FlowError
from trident.core import Service, clean, digest, source_link


class Run:
    def __init__(self, directory):
        self.root = Path(directory).expanduser().absolute()
        self.store = Service(self.root)
        self.path = self.root / 'run.json'
        self.context = self.root / 'context'
        self.jobs = self.root / 'jobs'

    def read(self, path):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_000_000:
            raise FlowError('local_io')
        return clean(json.loads(path.read_text()))

    def manifest(self):
        return self.read(self.path)

    def start(self, url):
        source = source_link(url)
        if self.path.exists():
            if self.manifest()['source'] != source:
                raise FlowError('run_mismatch')
            return self.status()
        service = Service(self.jobs)
        # Recover a local start interrupted before its index was written.
        candidates = [self.read(p) for p in self.jobs.glob('job_*/state.json')]
        matches = [s for s in candidates if s.get('source') == source]
        if candidates and (len(matches) != 1 or len(candidates) != 1):
            raise FlowError('run_mismatch')
        view = service.view(matches[0]) if matches else service.dispatch('dashboard_begin', {'url': url})
        self.store.write(self.path, {'version': 1, 'source': source, 'job_id': view['job_id'],
                                    'stages': {}, 'last_issue': None})
        return self.status()

    def fingerprint(self):
        state = Service(self.jobs).load(self.manifest()['job_id'])
        metrics = self.context / 'metrics.json'
        return {'capture_hash': state.get('capture_hash'), 'context_hash': state.get('context_hash'),
                'definitions_hash': digest([{k: m.get(k) for k in ('id', 'definition', 'translation')}
                                            for m in state.get('metrics', [])]),
                'metrics_hash': digest(self.read(metrics)) if metrics.exists() else None}

    def issue(self, result):
        if not self.path.exists():
            return
        manifest = self.manifest()
        manifest['last_issue'] = result
        self.store.write(self.path, manifest)

    def resolve_issue(self, operation):
        manifest = self.manifest()
        issue = manifest.get('last_issue')
        if issue and issue.get('operation') == operation:
            manifest.setdefault('resolved_issues', []).append(issue)
            manifest['last_issue'] = None
            self.store.write(self.path, manifest)

    def begin_stage(self, stage, request):
        """Call before remote agent creation or a paid eval, after the concrete plan is authorized."""
        manifest = self.manifest()
        allowed = ({'workspace_id', 'name', 'description', 'instructions'} if stage == 'agent'
                   else {'agent_id', 'runner', 'dataset_hash', 'context_snapshot_hash'})
        if set(request) != allowed or any(not isinstance(v, str) or not v.strip() for v in request.values()):
            raise FlowError('invalid_request')
        target = self.publication().get('workspace_id')
        if stage == 'agent' and target and request['workspace_id'] != target:
            raise FlowError('run_mismatch')
        registered = manifest['stages'].get('agent', {}).get('receipt', {}).get('artifact', {}).get('id')
        if stage == 'heldout_eval' and registered and request['agent_id'] != registered:
            raise FlowError('run_mismatch')
        current = self.fingerprint()
        if not current['metrics_hash']:
            raise FlowError('no_usable_metrics')
        binding = {'request': request, 'source': current}
        operation_id = digest({'stage': stage, **binding})
        existing = manifest['stages'].get(stage)
        if existing:
            if existing['state'] == 'pending':
                # Never replay an uncertain remote create or paid run, even if inputs changed.
                return {'status': 'needs_attention', 'code': 'pending_operation',
                        'operation_id': existing['operation_id'], 'recovery_request': existing['request'],
                        'next_action': 'Read back the original operation and acknowledge it before starting another.'}
            if existing['operation_id'] == operation_id:
                return {'status': 'already_complete', 'receipt': existing['receipt']}
            if stage == 'agent':
                return {'status': 'needs_attention', 'code': 'run_mismatch',
                        'next_action': 'Reconcile the existing agent and review its update; do not create a second agent.'}
            manifest.setdefault('history', []).append({'stage': stage, **existing})
        manifest['stages'][stage] = {**binding, 'operation_id': operation_id, 'state': 'pending'}
        self.store.write(self.path, manifest)
        return {'status': 'execute_once', 'operation_id': operation_id, 'request': request,
                'next_action': 'Execute the approved operation through the existing host tools, then acknowledge its real result.'}

    def acknowledge(self, stage, receipt):
        manifest = self.manifest()
        pending = manifest['stages'].get(stage)
        if not pending or receipt.get('operation_id') != pending['operation_id'] or receipt.get('read_succeeded') is not True:
            raise FlowError('invalid_request')
        if pending['state'] == 'complete':
            if pending['receipt'] != receipt:
                raise FlowError('run_mismatch')
            return self.status()
        if stage == 'agent':
            artifact = receipt.get('artifact', {})
            if (artifact.get('kind') != 'agent' or not isinstance(artifact.get('id'), str) or not artifact['id']
                    or set(artifact) != {'id', 'kind', *pending['request']}
                    or any(artifact.get(k) != v for k, v in pending['request'].items())):
                raise FlowError('run_mismatch')
        else:
            if any(receipt.get(k) != v for k, v in pending['request'].items()):
                raise FlowError('run_mismatch')
            counts = [receipt.get(k) for k in ('passed', 'scored', 'unmeasured')]
            if any(type(n) is not int or n < 0 for n in counts) or counts[0] > counts[1]:
                raise FlowError('invalid_request')
            if not receipt.get('evidence_ref'):
                raise FlowError('invalid_request')
        # Store only bounded receipts; never answers, SQL gold or raw transcripts.
        allowed = ({'operation_id', 'read_succeeded', 'artifact'} if stage == 'agent' else
                   {'operation_id', 'read_succeeded', 'agent_id', 'runner', 'dataset_hash',
                    'context_snapshot_hash', 'passed', 'scored', 'unmeasured', 'evidence_ref'})
        if set(receipt) != allowed:
            raise FlowError('invalid_request')
        pending.update(state='complete', receipt=receipt)
        self.store.write(self.path, manifest)
        return self.status()

    def publication(self):
        path = self.context / 'dashboard-publication.json'
        if not path.exists():
            return {'state': 'local_only', 'released': 0, 'drafts': 0}
        pub = self.read(path)
        current = self.fingerprint()
        exported = self.context / 'dashboard-source.json'
        if not exported.exists() or any(self.read(exported).get(k) != current[k] for k in ('capture_hash', 'context_hash', 'definitions_hash')):
            return {'state': 'needs_reconciliation', 'pending_operation': bool(pub.get('inflight'))}
        metrics = self.context / 'metrics.json'
        if not metrics.exists() or pub['reviewed']['source_hash'] != digest(self.read(metrics)):
            return {'state': 'needs_reconciliation', 'pending_operation': bool(pub.get('inflight'))}
        receipts = pub['published']
        released = sum(bool(p.get('release_id')) for p in receipts)
        done = pub['cursor'] == len(pub['reviewed']['actions'])
        return {'state': 'pending_readback' if pub.get('inflight') else 'refused' if pub.get('refused')
                else 'released' if done and released == len(receipts) and released else 'drafts_saved' if done else 'in_progress',
                'workspace_id': pub['reviewed']['workspace_id'], 'released': released,
                'drafts': len(receipts) - released, 'total': len(pub['reviewed']['actions'])}

    def status(self):
        manifest = self.manifest()
        service = Service(self.jobs)
        state = service.load(manifest['job_id'])
        view = service.view(state)
        source = self.fingerprint()
        stages = {}
        for name, record in manifest['stages'].items():
            stages[name] = {'state': record['state'] if record['source'] == source else 'stale',
                            'pending_readback': record['state'] == 'pending'}
            if record['state'] == 'complete' and record['source'] == source:
                if name == 'agent':
                    stages[name]['id'] = record['receipt']['artifact']['id']
                else:
                    stages[name].update({k: record['receipt'][k] for k in ('runner', 'passed', 'scored', 'unmeasured')})
        publication = self.publication()
        view['registry_status'] = publication['state']
        evaluation = stages.get('heldout_eval', {})
        if evaluation.get('state') == 'complete':
            view['summary']['heldout_accuracy_percent'] = (round(100 * evaluation['passed'] / evaluation['scored'], 1)
                                                          if evaluation['scored'] else None)
            view['summary']['heldout_runner'] = evaluation['runner']
            view['summary']['heldout_scored'] = evaluation['scored']
            view['summary']['heldout_unmeasured'] = evaluation['unmeasured']
        usable = view['agent_readiness']['usable_metrics']
        agent = stages.get('agent', {})
        agent_matches = (agent.get('state') == 'complete' and
                         manifest['stages']['agent']['request']['workspace_id'] == publication.get('workspace_id'))
        next_action = view['next_action']
        if usable and not view['summary']['pending_cases']:
            if publication['state'] == 'pending_readback':
                next_action = 'recover_publication_or_ask_local'
            elif publication['state'] in ('refused', 'needs_reconciliation'):
                next_action = 'reconcile_publication_or_ask_local'
            elif publication['state'] == 'released':
                next_action = ('ask_questions' if agent_matches else 'recover_agent_or_ask_local'
                               if agent.get('pending_readback') or agent.get('state') == 'stale' else 'register_agent')
            else:
                next_action = 'review_publication_or_ask_local' if publication['state'] == 'local_only' else 'continue_publication_or_ask_local'
        workflow = {'job_id': manifest['job_id'], 'revision': view['revision'],
                    'publication': publication, 'stages': stages, 'usable_metrics': usable,
                    'remaining_metrics': view['agent_readiness']['remaining_metrics'],
                    'questions_available': bool(usable), 'last_issue': manifest.get('last_issue'),
                    'context_dir': str(self.context), 'workspace': str(self.jobs),
                    'next_action': next_action,
                    'label': 'Agent ready to try' if usable and publication['state'] == 'released'
                             and agent_matches
                             else 'Local metrics ready to try' if usable else 'Preparing dashboard'}
        counts = view['summary']
        workflow['display_summary'] = (f"{workflow['label']}. {usable} of {counts['measures']} metrics available. "
            f"Dashboard checks: {counts['passed_cases']} of {counts['measured_cases']} compared passed; "
            f"{counts['unmeasured_cases']} unmeasured. "
            + (f"Held-out questions: {evaluation['passed']} of {evaluation['scored']} scored passed; "
               f"{evaluation['unmeasured']} unmeasured." if evaluation.get('state') == 'complete'
               else 'Held-out accuracy: Not run.')) if state.get('title') else None
        # This is a derived read model. Never rewrite underlying source or publication evidence.
        manifest['progress'] = copy.deepcopy(workflow)
        self.store.write(self.path, manifest)
        return {'status': 'ok', 'workflow': workflow, 'dashboard': view}
