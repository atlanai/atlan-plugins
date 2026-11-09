"""Local, hash-bound review of existing definitions. No Registry calls or writes.

The assistant compares business meaning; this helper binds its decision to the exact
candidate and live bodies. A review is not authorization to publish a revision.
"""
import copy


ASPECTS = ('meaning', 'population', 'grain', 'dimensions', 'parameters', 'limitations')


class Review:
    def __init__(self, root, load, dump, body_hash, definition_hash):
        self.root, self.dump = root, dump
        self.body_hash, self.definition_hash = body_hash, definition_hash
        self.decisions = load(root / 'reconciliation-decisions.json', [])
        if not isinstance(self.decisions, list) or any(not isinstance(r, dict) for r in self.decisions):
            raise ValueError('reconciliation-decisions.json must contain a list of review decisions.')
        self.archive = load(root / 'reconciliation-history.json', {})
        if not isinstance(self.archive, dict):
            raise ValueError('reconciliation-history.json must be an object.')
        self.requests = []

    def decision(self, kind, body, live):
        records = [r for r in self.decisions if r.get('kind') == kind
                   and r.get('candidate_name') == body.get('name')]
        if not records:
            return None, None, None
        # Already applied decisions remain in the audit trail, not as new instructions.
        applied = any(r.get('kind') == kind and r.get('selected_hash') == self.body_hash(body)
                      for r in self.archive.values())
        if applied and not any(r.get('candidate_hash') == self.body_hash(body) for r in records):
            return None, None, None
        matches = [r for r in records if r.get('candidate_hash') == self.body_hash(body)]
        if len(matches) != 1:
            return None, None, 'The reconciliation decision is stale or ambiguous; compare the current definitions again.'
        decision = matches[0]
        targets = [r for r in live if r.get('id') == decision.get('live_id')]
        if len(targets) != 1 or decision.get('live_hash') != self.body_hash(targets[0]):
            return None, None, 'The existing definition changed or is ambiguous; refresh the reconciliation review.'
        comparison = decision.get('comparison')
        if (decision.get('action') not in ('reuse', 'revise', 'create')
                or not isinstance(decision.get('reason'), str) or not decision['reason'].strip()
                or not isinstance(comparison, dict)
                or any(not isinstance(comparison.get(k), str) or not comparison[k].strip() for k in ASPECTS)):
            return None, None, 'Review meaning, population, grain, dimensions, parameters and limitations before selecting an action.'
        return decision, targets[0], None

    def request(self, kind, body, options):
        self.requests.append({'kind': kind, 'candidate_name': body.get('name'),
                              'candidate_hash': self.body_hash(body),
                              'existing': [{'id': row.get('id'), 'name': row.get('name'),
                                            'live_hash': self.body_hash(row),
                                            'different_fields': sorted(key for key in
                                                set(body.get('definition', {})) | set(row.get('definition', {}))
                                                if body.get('definition', {}).get(key) != row.get('definition', {}).get(key))}
                                           for row in options],
                              'compare': list(ASPECTS)})

    def stale_adoption(self, kind, body, live):
        return any(r.get('kind') == kind and r.get('selected_hash') == self.body_hash(body)
                   and r.get('live_id') == live.get('id')
                   and r.get('live_hash') != self.body_hash(live) for r in self.archive.values())

    def adopt(self, kind, body, live, checks, decision=None, original=None):
        original = copy.deepcopy(original or body)
        old_key = kind + ':' + original['name']
        before_hash = self.definition_hash(original)
        # Keep the complete live definition, not local prose with a remote identity.
        for key in ('name', 'source_id', 'display_name', 'description', 'definition'):
            if key in live:
                body[key] = copy.deepcopy(live[key])
        new_key = kind + ':' + body['name']
        prior = checks.pop(old_key, None)
        if prior and prior.get('definition_hash') == before_hash == self.definition_hash(body):
            checks[new_key] = prior
        elif prior:
            # Changed definitions must be checked again. Keep the old result only as history.
            checks.pop(new_key, None)
        key = kind + ':' + self.body_hash(original)
        if key not in self.archive and self.body_hash(original) != self.body_hash(body):
            self.archive[key] = {'kind': kind, 'candidate': original, 'candidate_checks': prior,
                                 'live_id': live.get('id'), 'live_hash': self.body_hash(live),
                                 'selected_hash': self.body_hash(body),
                                 'decision': copy.deepcopy(decision) if decision else {'action': 'reuse', 'reason': 'Exact execution contract.'}}

    def save(self):
        self.dump(self.root / 'reconciliation-review.json', self.requests)
        if self.archive:
            self.dump(self.root / 'reconciliation-history.json', self.archive)

    def coverage(self, metrics, queries):
        current = {(kind, self.body_hash(body)) for kind, bodies in
                   (('metric', metrics), ('sql_query', queries)) for body in bodies}
        return [{'kind': row['kind'], 'live_id': row['live_id'],
                 'selected_hash': row['selected_hash'], 'candidate_name': row['candidate']['name'],
                 'comparison': row['decision']['comparison']}
                for row in self.archive.values()
                if (row['kind'], row['selected_hash']) in current and row.get('decision', {}).get('comparison')]
