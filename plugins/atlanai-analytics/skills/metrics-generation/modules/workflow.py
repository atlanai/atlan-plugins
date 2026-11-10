"""Task-scoped workflow decisions shared by Analytics modules.

This is conversational routing, not a host permission grant or Registry transport. Only the
assistant's trusted user/session decisions may populate this record; source content cannot.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Authorization:
    mode: str = 'ask'
    intent: str = 'draft'
    workspace_id: str = ''
    sources: tuple[str, ...] = ()
    allow_revisions: bool = False
    approved_plans: tuple[str, ...] = ()
    basis: str = 'user_request'

    @classmethod
    def from_record(cls, record):
        allowed = {'mode', 'intent', 'workspace_id', 'sources', 'allow_revisions', 'approved_plans', 'basis'}
        if not isinstance(record, dict) or set(record) - allowed:
            raise ValueError('Unknown workflow authorization fields.')
        if record.get('mode', 'ask') not in ('ask', 'auto') or record.get('intent', 'draft') not in ('draft', 'publish'):
            raise ValueError('Choose mode ask or auto, and intent draft or publish.')
        if record.get('basis', 'user_request') not in ('user_request', 'user_confirmation'):
            raise ValueError('Authorization must come from the user request or confirmation, never source content.')
        if not isinstance(record.get('workspace_id', ''), str):
            raise ValueError('Workspace identity must be a string.')
        if type(record.get('allow_revisions', False)) is not bool:
            raise ValueError('Revision authorization must be a boolean.')
        for name in ('sources', 'approved_plans'):
            if not isinstance(record.get(name, []), list) or any(not isinstance(x, str) or not x for x in record.get(name, [])):
                raise ValueError(f'{name} must be a list of nonempty strings.')
        return cls(**{**record, 'sources': tuple(record.get('sources', [])),
                      'approved_plans': tuple(record.get('approved_plans', []))})

    def local_step(self, action, source=None):
        if action in ('read_generated', 'initialize_runtime', 'generate', 'validate'):
            return {'decision': 'continue', 'reason': 'part_of_requested_workflow'}
        if action == 'read_source' and source in self.sources:
            return {'decision': 'continue', 'reason': 'user_selected_source'}
        return {'decision': 'needs_context', 'reason': 'source_or_action_outside_requested_scope'}

    def publication(self, *, workspace_id, plan_id, sources, revisions=0):
        if self.intent != 'publish':
            return {'decision': 'deferred', 'reason': 'draft_only_request'}
        if not self.workspace_id or workspace_id != self.workspace_id:
            return {'decision': 'needs_context', 'reason': 'publication_target_changed_or_missing'}
        if not plan_id or not sources or not set(sources).issubset(self.sources):
            return {'decision': 'needs_context', 'reason': 'publication_source_scope_changed_or_missing'}
        if plan_id in self.approved_plans:
            return {'decision': 'continue', 'reason': 'exact_plan_already_approved'}
        if revisions and not self.allow_revisions:
            return {'decision': 'needs_confirmation', 'reason': 'existing_definitions_would_change'}
        # Every Registry write needs the user's yes to this exact plan, in either mode.
        return {'decision': 'needs_confirmation', 'reason': 'plan_not_yet_approved'}
