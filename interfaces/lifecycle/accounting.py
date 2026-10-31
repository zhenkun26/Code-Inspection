"""Invocation observations and existing ledger capacity; no charging or writes."""
import copy
import time


class InvocationMetrics:
    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self._started = self._clock()
        self._finished = None
        self.git_subprocess_attempts = 0
        self.repository_scan_attempts = 0
        self.repository_entries_scanned = 0
        self.merge_tree_write_attempts = 0

    def git_attempt(self, merge_tree_write=False):
        self.git_subprocess_attempts += 1
        if merge_tree_write:
            self.merge_tree_write_attempts += 1

    def repository_scan(self):
        self.repository_scan_attempts += 1

    def repository_entry(self):
        self.repository_entries_scanned += 1

    def finish(self):
        if self._finished is None:
            self._finished = {
                'scope': 'current_cli_invocation',
                'wall_seconds': self._clock() - self._started,
                'git_subprocess_attempts': self.git_subprocess_attempts,
                'repository_scan_attempts': self.repository_scan_attempts,
                'repository_entries_scanned': self.repository_entries_scanned,
                'merge_tree_write_attempts': self.merge_tree_write_attempts,
            }
        return dict(self._finished)


def budget_report(session, metrics):
    """Unknown usage stays unknown; capacity never grants execution authority."""
    ledger = {
        'scope': 'host_lifecycle_ledger', 'status': 'unknown',
        'basis': 'unavailable', 'durability': 'unknown',
        'durability_scope': {'save_or_reload_visibility': 'unknown', 'power_loss': 'unknown',
                             'host_qualified_checkpoint': 'not_asserted'},
        'operation_attempts': {'unit': 'started_lifecycle_operation', 'used': None,
                               'limit': None, 'remaining': None, 'exhausted': None},
        'review_rounds': {'unit': 'review_round', 'used': None, 'limit': None,
                         'remaining': None, 'exhausted': None},
        'capacity': {'next_operation': None, 'next_review_round': None},
    }
    if session is not None and session.accounting_trusted and isinstance(session.state, dict):
        used = session.state.get('budget')
        limit = session.policy.get('budget')
        if (isinstance(used, dict) and isinstance(limit, dict)
                and all(type(used.get(k)) is int and used[k] >= 0 for k in ['operations', 'rounds'])
                and all(type(limit.get(k)) is int and limit[k] > 0 for k in ['max_operations', 'max_rounds'])):
            durable = session.accounting_durability == 'persisted'
            ledger.update(status='known', basis='persisted_ledger' if durable else 'session_snapshot',
                          durability='persisted' if durable else 'unknown')
            ledger['durability_scope']['save_or_reload_visibility'] = 'confirmed' if durable else 'unknown'
            for name, used_key, limit_key in [('operation_attempts', 'operations', 'max_operations'),
                                               ('review_rounds', 'rounds', 'max_rounds')]:
                ledger[name].update(used=used[used_key], limit=limit[limit_key],
                                    remaining=max(0, limit[limit_key] - used[used_key]),
                                    exhausted=used[used_key] >= limit[limit_key])
            ledger['capacity'] = {
                'next_operation': used['operations'] + 1 <= limit['max_operations']
                                  and used['rounds'] <= limit['max_rounds'],
                'next_review_round': used['operations'] + 1 <= limit['max_operations']
                                     and used['rounds'] + 1 <= limit['max_rounds'],
            }
    attempt = None
    if session is not None and session.current_attempt is not None:
        attempt = dict(copy.deepcopy(session.current_attempt), durability=ledger['durability'])
    return {'schema': 'lifecycle-budget-report/v1', 'ledger': ledger,
            'current_attempt': attempt,
            'observed_work': metrics.finish(),
            'external': {'cost': {'status': 'unknown', 'value': None},
                         'runtime_seconds': {'status': 'unknown', 'value': None}},
            'authorizes_action': False}
