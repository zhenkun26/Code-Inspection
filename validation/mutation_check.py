"""Bounded regression-quality checks on retained, self-owned source copies.

This is a fixed list of known guard removals, not a general mutation framework
or a claim about comprehensive mutation coverage. Never mutates the checkout.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation.test_support import temporary_directory

CASES = [
    ('temp-unknown-link-count', 'interfaces/lifecycle/temporary.py', "'hardlinked-regular' if info.st_nlink > 1 else 'regular'", "'hardlinked-regular' if info.st_nlink != 1 else 'regular'", 'validation/lifecycle/test_temporary_inspection.py', 'TemporaryInspection.test_unknown_link_count_is_not_a_hardlink_claim'),
    ('temp-json-encoding', 'interfaces/lifecycle/cli.py', 'print(json.dumps(out,ensure_ascii=True,indent=2,allow_nan=False));return 0', 'print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False));return 0', 'validation/lifecycle/test_temporary_inspection.py', 'TemporaryInspection.test_cli_escapes_surrogate_names_without_losing_identity'),
    ('temp-policy-pin', 'interfaces/lifecycle/temporary.py', "hashlib.sha256(read_bytes(selected, 'policy.json')).hexdigest() == policy_sha,\n                'host policy pin mismatch'", "True,\n                'host policy pin mismatch'", 'validation/lifecycle/test_temporary_inspection.py', 'TemporaryInspection.test_pin_and_lock_refuse_before_enumeration'),
    ('temp-root-lock', 'interfaces/lifecycle/temporary.py', 'with root_lock(root) as selected:', "with __import__('contextlib').nullcontext(root) as selected:", 'validation/lifecycle/test_temporary_inspection.py', 'TemporaryInspection.test_pin_and_lock_refuse_before_enumeration'),
    ('temp-entry-cap', 'interfaces/lifecycle/temporary.py', 'if scanned >= MAX_SCANNED_ENTRIES:', 'if False:', 'validation/lifecycle/test_temporary_inspection.py', 'TemporaryInspection.test_enumeration_and_output_caps_are_incomplete'),
    ('temp-output-cap', 'interfaces/lifecycle/temporary.py', 'if len(rows) >= MAX_REPORTED_ENTRIES:', 'if False:', 'validation/lifecycle/test_temporary_inspection.py', 'TemporaryInspection.test_enumeration_and_output_caps_are_incomplete'),
    ('temp-no-follow', 'interfaces/lifecycle/temporary.py', 'entry.stat(follow_symlinks=False)', 'entry.stat(follow_symlinks=True)', 'validation/lifecycle/test_temporary_inspection.py', 'TemporaryInspection.test_each_entry_uses_nonfollowing_metadata'),
    ('temp-prefix', 'interfaces/lifecycle/temporary.py', "if not entry.name.startswith('.lifecycle-'):", 'if False:', 'validation/lifecycle/test_temporary_inspection.py', 'TemporaryInspection.test_empty_report_is_not_a_lifecycle_decision'),
    ('temp-policy-recheck', 'interfaces/lifecycle/temporary.py', "hashlib.sha256(read_bytes(selected, 'policy.json')).hexdigest() == policy_sha,\n                'host policy changed during inspection'", "True,\n                'host policy changed during inspection'", 'validation/lifecycle/test_temporary_inspection.py', 'TemporaryInspection.test_disappearing_entry_and_changed_policy_fail_closed'),
    ('temp-no-payload', 'interfaces/lifecycle/temporary.py', 'info = entry.stat(follow_symlinks=False)', 'read_bytes(selected, entry.name)\n                    info = entry.stat(follow_symlinks=False)', 'validation/lifecycle/test_temporary_inspection.py', 'TemporaryInspection.test_metadata_only_retains_every_kind_and_never_reads_residue'),
    ('temp-retain', 'interfaces/lifecycle/temporary.py', "'ownership': 'UNKNOWN', 'action': 'RETAIN'", "'ownership': 'OWNED', 'action': 'DELETE'", 'validation/lifecycle/test_temporary_inspection.py', 'TemporaryInspection.test_metadata_only_retains_every_kind_and_never_reads_residue'),
    ('artifact-algorithm', 'interfaces/lifecycle/artifacts.py', "proof['algorithm'] == 'sha256'", 'True', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_unknown_algorithm_and_actual_digest_mismatch_have_distinct_read_boundaries'),
    ('artifact-immutable-bytes', 'interfaces/lifecycle/artifacts.py', 'isinstance(snapshot, Snapshot) and type(snapshot.data) is bytes', 'isinstance(snapshot, Snapshot)', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_snapshot_check_recomputes_actual_bytes_and_requires_bytes_type'),
    ('artifact-apply-snapshot', 'interfaces/lifecycle/delivery.py', 'if consumes:artifacts.check_snapshot(snapshot,content)', 'if consumes:None', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_apply_rejects_forged_snapshot_and_query_rejects_forged_consumption_receipt'),
    ('artifact-consumption-receipt', 'interfaces/lifecycle/delivery.py', "require(type(consumed['size_bytes']) is int and consumed=={\n                name:content['proof'][name] for name in ['sha256','size_bytes']},", 'require(True,', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_apply_rejects_forged_snapshot_and_query_rejects_forged_consumption_receipt'),
    ('artifact-required-proof', 'interfaces/lifecycle/artifacts.py', 'not required(s, action)', 'True', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_missing_optional_and_source_only_are_not_product_failures'),
    ('artifact-isolation', 'interfaces/lifecycle/artifacts.py', "config['isolation'] == 'host-controlled'", 'True', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_policy_invalid_and_unconfirmed_isolation_refuse_before_payload_read'),
    ('artifact-policy-cap', 'interfaces/lifecycle/artifacts.py', "0 < config['max_bytes'] <= MAX_BYTES", "0 < config['max_bytes']", 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_policy_invalid_and_unconfirmed_isolation_refuse_before_payload_read'),
    ('artifact-raw-locator', 'interfaces/lifecycle/artifacts.py', "    locator(container['locator'])", '    None', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_descriptor_raw_paths_and_unknown_fields_are_rejected_before_read'),
    ('artifact-proof-source', 'interfaces/lifecycle/artifacts.py', "proof['source_subject_digest'] == digest(source_subject)", 'True', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_full_proof_source_tree_config_validation_is_pure_and_strict'),
    ('artifact-proof-tree', 'interfaces/lifecycle/artifacts.py', "proof['candidate_tree'] == tree", 'True', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_full_proof_source_tree_config_validation_is_pure_and_strict'),
    ('artifact-proof-config', 'interfaces/lifecycle/artifacts.py', "proof['ci_config_digest'] == source_subject['ci_config_digest']", 'True', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_full_proof_source_tree_config_validation_is_pure_and_strict'),
    ('artifact-provenance-claim', 'interfaces/lifecycle/artifacts.py', "proof['provenance'] == 'host-declared-association'", 'True', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_full_proof_source_tree_config_validation_is_pure_and_strict'),
    ('artifact-read-cap', 'interfaces/lifecycle/artifacts.py', "limit=config['max_bytes'], single_link=True", 'limit=MAX_BYTES, single_link=True', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_exact_byte_limit_empty_file_and_growth_detection'),
    ('artifact-single-link', 'interfaces/lifecycle/artifacts.py', 'single_link=True', 'single_link=False', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_artifact_reader_refuses_links_nonregular_files_and_intermediate_escape'),
    ('artifact-actual-bytes', 'interfaces/lifecycle/artifacts.py', "hashlib.sha256(snapshot.data).hexdigest() == proof['sha256']", 'True', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_snapshot_check_recomputes_actual_bytes_and_requires_bytes_type'),
    ('artifact-acquire-check', 'interfaces/lifecycle/artifacts.py', '    check_snapshot(snapshot, container)\n    return snapshot', '    return snapshot', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_snapshot_check_recomputes_actual_bytes_and_requires_bytes_type'),
    ('artifact-merge-fresh', 'interfaces/lifecycle/merge.py', "snapshot=artifacts.fresh(s,'merge-execute',plan.get('artifact_content'))", 'snapshot=None', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_merge_byte_change_after_approval_holds_before_cas_and_charge'),
    ('artifact-full-plan-id', 'interfaces/lifecycle/merge.py', "require(plan['id']==digest({k:v for k,v in plan.items() if k!='id'}),'merge plan digest mismatch')", 'None', 'validation/lifecycle/test_artifact_content.py', 'ArtifactContent.test_every_proof_field_is_part_of_merge_plan_identity'),
    ('attempt-round-type', 'interfaces/lifecycle/core.py',
     'type(rounds) is int', 'isinstance(rounds,int)',
     'validation/lifecycle/test_budget_attempts.py', 'BudgetAttempts.test_round_arguments_reject_before_memory_disk_or_persist_effects'),
    ('attempt-round-delta', 'interfaces/lifecycle/core.py',
     'rounds in {0,1}', 'rounds in {0,1,-1}',
     'validation/lifecycle/test_budget_attempts.py', 'BudgetAttempts.test_round_arguments_reject_before_memory_disk_or_persist_effects'),
    ('attempt-operation-tag', 'interfaces/lifecycle/core.py',
     'operation in OPERATIONS', 'True',
     'validation/lifecycle/test_budget_attempts.py', 'BudgetAttempts.test_round_arguments_reject_before_memory_disk_or_persist_effects'),
    ('attempt-history-shape', 'interfaces/lifecycle/core.py',
     "type(self.state.get('events')) is list", 'True',
     'validation/lifecycle/test_budget_attempts.py', 'BudgetAttempts.test_malformed_event_history_rejects_charge_before_any_session_effect'),
    ('attempt-start-record', 'interfaces/lifecycle/core.py',
     "self.state['events'].append(started)", 'None',
     'validation/lifecycle/test_budget_attempts.py', 'BudgetAttempts.test_charge_saves_counters_and_unique_started_record_once_and_resets_report'),
    ('attempt-charge-save', 'interfaces/lifecycle/core.py',
     'self.persist() # started attempts count even if later processing fails', 'None',
     'validation/lifecycle/test_budget_attempts.py', 'BudgetAttempts.test_charge_saves_counters_and_unique_started_record_once_and_resets_report'),
    ('attempt-unique-id', 'interfaces/lifecycle/core.py',
     'uuid.uuid4().hex', "'0'*32",
     'validation/lifecycle/test_budget_attempts.py', 'BudgetAttempts.test_charge_saves_counters_and_unique_started_record_once_and_resets_report'),
    ('attempt-business-link', 'interfaces/lifecycle/core.py',
     "if self.current_attempt is not None:event['attempt_id']=self.current_attempt['attempt_id']",
     "if False:event['attempt_id']=self.current_attempt['attempt_id']",
     'validation/lifecycle/test_budget_attempts.py', 'BudgetAttempts.test_failed_ingest_keeps_started_consumption_and_success_links_business_event'),
    ('attempt-save-fence', 'interfaces/lifecycle/core.py',
     'require(not self._persistence_failed,', 'require(True,',
     'validation/lifecycle/test_budget_attempts.py', 'BudgetAttempts.test_save_faults_fence_same_session_and_preserve_exact_fresh_recovery'),
    ('attempt-latch-on-failure', 'interfaces/lifecycle/core.py',
     'self._persistence_failed = True', 'self._persistence_failed = False',
     'validation/lifecycle/test_budget_attempts.py', 'BudgetAttempts.test_ingest_save_fault_keeps_speculative_snapshot_until_valid_reload'),
    ('attempt-uncertain-snapshot', 'interfaces/lifecycle/review.py',
     'if not s._persistence_failed:s.state=staged', 's.state=staged',
     'validation/lifecycle/test_budget_attempts.py', 'BudgetAttempts.test_ingest_save_fault_keeps_speculative_snapshot_until_valid_reload'),
    ('attempt-merge-request-link', 'interfaces/lifecycle/merge.py',
     "s.event('merge-requested',{'operation':plan['id']});s.persist()", 's.persist()',
     'validation/lifecycle/test_budget_attempts.py', 'BudgetAttempts.test_save_faults_fence_same_session_and_preserve_exact_fresh_recovery'),
    ('attempt-delivery-request-link', 'interfaces/lifecycle/delivery.py',
     "s.event('delivery-requested',{'operation':operation});s.persist()", 's.persist()',
     'validation/lifecycle/test_budget_attempts.py', 'BudgetAttempts.test_save_faults_fence_same_session_and_preserve_exact_fresh_recovery'),
    ('hygiene-private-selection', 'validation/hygiene_check.py',
     'if _forbidden(parts):', 'if False:',
     'validation/test_hygiene.py', 'ReadBoundaries.test_private_and_ignored_selections_read_no_payload_in_both_entries'),
    ('hygiene-ignore-policy', 'validation/hygiene_check.py',
     'for name in sorted(ignored_candidates(names)):', 'for name in []:',
     'validation/test_hygiene.py', 'ReadBoundaries.test_private_and_ignored_selections_read_no_payload_in_both_entries'),
    ('hygiene-raw-path', 'validation/hygiene_check.py',
     "if any(part in {'', '.', '..'} or part.endswith((' ', '.')) for part in parts):", 'if False:',
     'validation/test_hygiene.py', 'ReadBoundaries.test_selected_path_aliases_do_not_bypass_preflight'),
    ('hygiene-component-preflight', 'validation/hygiene_check.py',
     'info = _file_info(root, name)', 'info = (root / name).stat()',
     'validation/test_hygiene.py', 'ReadBoundaries.test_intermediate_link_or_junction_is_rejected_before_payload'),
    ('hygiene-file-limit', 'validation/hygiene_check.py',
     'if info.st_size > MAX_FILE_BYTES:', 'if False:',
     'validation/test_hygiene.py', 'ReadBoundaries.test_oversized_markdown_and_probe_fail_before_any_payload'),
    ('hygiene-selection-count', 'validation/hygiene_check.py',
     'if len(names) > MAX_SELECTED_FILES:', 'if False:',
     'validation/test_hygiene.py', 'ReadBoundaries.test_file_count_and_aggregate_preflight_are_bounded'),
    ('hygiene-preflight-total', 'validation/hygiene_check.py',
     'if sum(info.st_size for info in metadata.values()) > MAX_SCAN_BYTES:', 'if False:',
     'validation/test_hygiene.py', 'ReadBoundaries.test_file_count_and_aggregate_preflight_are_bounded'),
    ('hygiene-live-budget', 'validation/hygiene_check.py',
     'limit = min(MAX_FILE_BYTES, MAX_SCAN_BYTES - self.used)', 'limit = MAX_FILE_BYTES',
     'validation/test_hygiene.py', 'ReadBoundaries.test_growth_after_preflight_cannot_exceed_actual_scan_budget'),
    ('hygiene-bounded-payload', 'validation/hygiene_check.py',
     'data = read_bytes(self.root, name, limit=limit)', 'data = (self.root / name).read_bytes()',
     'validation/test_hygiene.py', 'ReadBoundaries.test_markdown_and_eight_character_files_use_bounded_io'),
    ('hygiene-probe-cap', 'validation/hygiene_check.py',
     'limit = min(limit, 4)', 'limit = limit',
     'validation/test_hygiene.py', 'ReadBoundaries.test_probe_is_exactly_four_bytes_and_has_its_own_read_cap'),
    ('hygiene-link-target', 'validation/hygiene_check.py',
     '_file_info(root, relative)', 'None',
     'validation/test_hygiene.py', 'ReadBoundaries.test_link_target_rechecks_metadata_without_reading_external_payload'),
    ('native-portable-temp-policy', 'validation/test_native_files.py',
     '    def test_unicode_spaces_and_crlf_bytes(self):\n        with temporary_directory() as d:',
     '    def test_unicode_spaces_and_crlf_bytes(self):\n        with tempfile.TemporaryDirectory() as d:',
     'validation/test_native_files.py', 'TemporaryDirectoryCompatibility.test_keep_on_retains_real_portable_fixture_in_explicit_external_root'),
    ('native-windows-temp-policy', 'validation/test_native_files.py',
     '        self.w=wf;self.temp=temporary_directory()', '        self.w=wf;self.temp=tempfile.TemporaryDirectory()',
     'validation/test_native_files.py', 'TemporaryDirectoryCompatibility.test_windows_fixture_uses_shared_factory_and_cleanup_survives_setup_failure'),
    ('native-fixture-cleanup', 'validation/test_native_files.py',
     '        self.w=wf;self.temp=temporary_directory()\n        self.addCleanup(self.temp.cleanup)',
     '        self.w=wf;self.temp=temporary_directory()',
     'validation/test_native_files.py', 'TemporaryDirectoryCompatibility.test_windows_fixture_uses_shared_factory_and_cleanup_survives_setup_failure'),
    ('runner-load-tests-coverage', 'validation/run_checks.py',
     "or (isinstance(node, ast.Name) and node.id == 'load_tests') for node in ast.walk(tree)):\n        raise ValueError(error)",
     "or (isinstance(node, ast.Name) and node.id == 'load_tests') for node in ast.walk(tree)):\n        pass",
     'validation/test_check_runner.py', 'CheckRunner.test_unsupported_discovery_shapes_fail_closed'),
    ('runner-timeout-failure', 'validation/run_checks.py',
     "record.update(status='TIMEOUT', exit_code=124, timeout_seconds=timeout,",
     "record.update(status='PASS', exit_code=0, timeout_seconds=timeout,",
     'validation/test_check_runner.py', 'CheckRunner.test_real_timeout_preserves_partial_log_and_marks_remaining_not_run'),
    ('runner-shard-coverage', 'validation/run_checks.py',
     'return [names[::2], names[1::2]]', 'return [names[::2], names[::2]]',
     'validation/test_check_runner.py', 'CheckRunner.test_shards_cover_actual_loader_once_and_include_new_cases'),
    ('legacy-applicability', 'interfaces/lifecycle/review.py',
     "if check['na_reason']!='':", 'if False:',
     'validation/lifecycle/test_applicability.py', 'Applicability.test_legacy_reason_and_whitespace_are_unknown_even_with_pass'),
    ('applicability-basis', 'interfaces/lifecycle/review.py',
     "for field in ['reason','basis','decider','source_subject_digest']:text(record[field])",
     "for field in ['reason','basis','decider','source_subject_digest']:None",
     'validation/lifecycle/test_applicability.py', 'Applicability.test_structured_record_loads_and_malformed_records_hold'),
    ('optional-check-blocking', 'interfaces/lifecycle/core.py',
     "x.get('required',True) and x['status']", "x['status']",
     'validation/lifecycle/test_applicability.py', 'Applicability.test_optional_failure_visible_without_blocking_and_required_control'),
    ('budget-git-attempts', 'interfaces/lifecycle/accounting.py',
     'self.git_subprocess_attempts += 1', 'self.git_subprocess_attempts += 0',
     'validation/lifecycle/test_budget_accounting.py', 'BudgetAccounting.test_git_attempts_include_nonzero_timeout_and_oserror'),
    ('budget-next-round', 'interfaces/lifecycle/accounting.py',
     "used['rounds'] + 1 <= limit['max_rounds']", "used['rounds'] <= limit['max_rounds']",
     'validation/lifecycle/test_budget_accounting.py', 'BudgetAccounting.test_ready_verdict_does_not_hide_round_exhaustion_or_write_status_telemetry'),
    ('budget-counter-type', 'interfaces/lifecycle/core.py',
     'type(used.get(name)) is int', 'isinstance(used.get(name),int)',
     'validation/lifecycle/test_budget_accounting.py', 'BudgetAccounting.test_malformed_usage_cannot_start_or_persist_a_charge'),
    ('merge-cas', 'interfaces/lifecycle/merge.py',
     "plan['candidate'],plan['base'])", "plan['candidate'])",
     'validation/lifecycle/test_lifecycle.py', 'Lifecycle.test_execute_cas_rejects_drift_after_preflight'),
    ('leaf-no-follow', 'support/local_files.py',
     'os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK', 'os.O_RDONLY | os.O_NONBLOCK',
     'validation/security-integration/test_local_files.py', 'LocalFileSafetyTests.test_symlink_evidence_rejected_even_with_matching_digest'),
    ('evidence-status', 'interfaces/lifecycle/review.py',
     "require(e['status'] in STATES,'unknown status')", 'None',
     'validation/lifecycle/test_lifecycle.py', 'Lifecycle.test_unknown_status_raw_digest_and_duplicate_id_rejected'),
    ('evidence-expiry', 'interfaces/lifecycle/review.py',
     "if not timestamp(e['observed_at'])<=now()<=timestamp(e['expires_at']):return 'UNKNOWN'", 'if False:return \'UNKNOWN\'',
     'validation/lifecycle/test_lifecycle.py', 'Lifecycle.test_invalid_evidence_overrides_an_otherwise_ready_baseline'),
    ('raw-digest', 'interfaces/lifecycle/review.py',
     "require(bytes_digest(read_bytes(root,e['raw']['path']))==e['raw']['sha256'],'raw evidence digest mismatch')", 'None',
     'validation/lifecycle/test_lifecycle.py', 'Lifecycle.test_unknown_status_raw_digest_and_duplicate_id_rejected'),
    ('duplicate-evidence', 'interfaces/lifecycle/review.py',
     "require(not any(old['id']==e['id'] for old in s.state['evidence']),'evidence ID already present; new attempt needs new ID')", 'None',
     'validation/lifecycle/test_lifecycle.py', 'Lifecycle.test_unknown_status_raw_digest_and_duplicate_id_rejected'),
    ('git-timeout', 'interfaces/lifecycle/merge.py', 'timeout=10)', 'timeout=None)',
     'validation/lifecycle/test_lifecycle.py', 'Lifecycle.test_git_subprocess_timeout_is_enforced'),
    ('duplicate-json', 'support/local_files.py',
     "require(key not in result, 'duplicate JSON key')", 'None',
     'validation/security-integration/test_local_files.py', 'LocalFileSafetyTests.test_duplicate_keys_in_otherwise_valid_records'),
    ('unrouted-resource', 'validation/pre-pr-review/structure_check.py',
     "require({root_relative / name for name in files if name != 'SKILL.md'} <= routed,\n            'unrouted resource')", 'None',
     'validation/security-integration/test_helpers.py', 'HelperSafetyTests.test_unrouted_resource_rejected'),
    ('merge-plan-digest', 'interfaces/lifecycle/merge.py',
     "require(plan['id']==digest({k:v for k,v in plan.items() if k!='id'}),'merge plan digest mismatch')", 'None',
     'validation/lifecycle/test_merge_integrity.py', 'MergeIntegrity.test_every_plan_field_is_digest_bound'),
    ('merge-parents', 'interfaces/lifecycle/merge.py',
     "require(parent_headers==['parent '+plan['base'],'parent '+plan['head']],\n            'candidate ordered parents mismatch')", 'None',
     'validation/lifecycle/test_merge_integrity.py', 'MergeIntegrity.test_exact_candidate_tree_and_ordered_parents_are_required'),
    ('merge-combination', 'interfaces/lifecycle/merge.py',
     "require(combined.returncode==0 and combined.stdout.splitlines() and combined.stdout.splitlines()[0]==plan['tree'],\n            'merge combination tree mismatch or unavailable')", 'None',
     'validation/lifecycle/test_merge_integrity.py', 'MergeIntegrity.test_coherent_wrong_merge_tree_is_rejected_before_new_attempt'),
    ('merge-receipt-intent', 'interfaces/lifecycle/merge.py',
     "require(receipt['operation']==plan['id'] and all(receipt[name]==plan[name] for name in\n            ['repository','target_ref','policy_sha','base','candidate']),'merge receipt intent mismatch')", 'None',
     'validation/lifecycle/test_merge_integrity.py', 'MergeIntegrity.test_mismatched_or_empty_receipts_never_confirm_or_retry'),
    ('merge-known-cas-failure', 'interfaces/lifecycle/merge.py',
     "result='failed' if x.returncode!=0 or actual==plan['base'] else 'confirmed'", "result='failed' if actual==plan['base'] else 'confirmed'",
     'validation/lifecycle/test_merge_integrity.py', 'MergeIntegrity.test_failed_cas_same_candidate_never_confirms'),
    ('delivery-plan-digest', 'interfaces/lifecycle/delivery.py',
     "require(operation==digest({k:v for k,v in proposal.items() if k!='id'}),'delivery proposal digest mismatch')", 'None',
     'validation/lifecycle/test_delivery_integrity.py', 'DeliveryIntegrity.test_canonical_payload_fields_and_map_key_are_checked'),
    ('delivery-rollback-compatibility', 'interfaces/lifecycle/delivery.py',
     "require(p.config['rollback_compatibility'].get(value['artifact'])==state['data_version'],\n                'BLOCKED: compatibility with current data not established')", 'None',
     'validation/lifecycle/test_delivery_integrity.py', 'DeliveryIntegrity.test_fresh_rollback_rechecks_compatibility_and_module_capability'),
    ('delivery-receipt-intent', 'interfaces/lifecycle/delivery.py',
     "require(digest(receipt[key])==digest(intent[key]),'delivery receipt intent mismatch: '+key)", 'None',
     'validation/lifecycle/test_delivery_integrity.py', 'DeliveryIntegrity.test_receipt_operation_mismatch_with_pending_request_is_rejected'),
]


def main():
    os.environ['CODE_INSPECTION_KEEP_TEST_ARTIFACTS'] = '1'
    output = Path(temporary_directory().name)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    sources = []
    for folder in ('interfaces', 'support', 'skills', 'validation', 'docs'):
        for path in (ROOT / folder).rglob('*'):
            if (path.is_file() and not path.is_symlink() and path.suffix in {'.py', '.md', '.json', '.txt'}
                    and not any(part in {'results', 'history', 'reviews', '__pycache__'} for part in path.relative_to(ROOT).parts)):
                sources.append(path)
    cases = CASES
    if os.name == 'nt':
        cases = [x for x in CASES if x[0] != 'leaf-no-follow'] + [
            ('windows-no-reparse', 'support/windows_files.py', 'OBJ_DONT_REPARSE = 0x1000', 'OBJ_DONT_REPARSE = 0',
             'validation/test_native_files.py', 'WindowsFiles.test_junction_and_symlink_all_positions'),
            ('windows-lock', 'support/windows_files.py',
             'wincheck(LockFileEx(handle, 3, 0, 1, 0, c.byref(ov))); acquired = True', 'acquired = False',
             'validation/test_native_files.py', 'PortableFiles.test_lock_contention_and_exception_release'),
        ]
    results = []
    for name, filename, before, after, test_file, test in cases:
        copy = output / name
        for path in sources:
            target = copy / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        target = copy / filename
        source = target.read_text(encoding='utf-8')
        if source.count(before) != 1:
            raise ValueError('mutation anchor must be unique: ' + name)
        # The same copied dependency closure must pass before guard removal.
        baseline = subprocess.run([sys.executable, '-B', test_file, test], cwd=copy, env=env,
                                  text=True, capture_output=True, timeout=90)
        (output / (name + '-baseline.log')).write_bytes((baseline.stdout + baseline.stderr).encode('utf-8'))
        if baseline.returncode != 0:
            record = {'mutation': name, 'result': 'NOT_VERIFIED', 'baseline_exit_code': baseline.returncode,
                      'reason': 'unmodified control failed; mutation not run'}
            results.append(record); print(json.dumps(record), flush=True)
            continue
        (target).write_bytes((source.replace(before, after, 1)).encode('utf-8'))
        run = subprocess.run([sys.executable, '-B', test_file, test], cwd=copy, env=env,
                             text=True, capture_output=True, timeout=90)
        (output / (name + '.log')).write_bytes((run.stdout + run.stderr).encode('utf-8'))
        # An import/runtime error is not proof that the intended invariant was tested.
        killed = run.returncode == 1 and 'FAIL:' in run.stderr and 'ERROR:' not in run.stderr
        record = {'mutation': name, 'result': 'KILLED' if killed else 'NOT_VERIFIED',
                  'baseline_exit_code': baseline.returncode, 'exit_code': run.returncode}
        results.append(record)
        print(json.dumps(record), flush=True)
    (output / 'summary.json').write_bytes((json.dumps(results, indent=2)).encode('utf-8'))
    print(json.dumps({'receipt_directory': str(output), 'scope': str(len(cases)) + ' fixed platform guard removals'}))
    return int(any(r['result'] != 'KILLED' for r in results))


if __name__ == '__main__':
    sys.exit(main())
