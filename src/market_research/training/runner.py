"""Fixed fold runner: fit sees train only, inference receives feature payloads only."""
from collections import Counter
from pathlib import Path
import json
import numpy as np
from ..http import utc_now
from ..storage import canonical, sha256, write_json
from ..pit.time import timestamp
from ..stage4.inputs import load_fixed, eligibility_report, key
from ..stage4.splits import make_splits
from ..stage4.predictions import validate_predictions, PREDICTION_VERSION
from ..stage4.metrics import evaluate
from ..stage4.runs import save_run, read_run, replay_run
from .pipeline import check_features, project_features, fit_pipeline, predict, MODELS
from .artifacts import save_model, load_model
from . import VERSION


def validate_config(c):
    if c.get('version') != VERSION: raise ValueError('training_config_version_required')
    if c.get('mode') not in ('strict', 'research', 'synthetic'): raise ValueError('explicit_training_mode_required')
    if c.get('task') not in MODELS or c.get('target_id') not in ('price_return', 'cash_total_return'): raise ValueError('single_task_target_required')
    if c.get('horizon') not in (1, 5, 20) or c['split'].get('horizon') != c['horizon']: raise ValueError('single_horizon_contract_required')
    if c.get('data_scope'):
        from ..observed.scope import SCOPES
        from ..observed.operation import MINIMUM
        if c['data_scope'] not in SCOPES or c['mode'] != ('strict' if c['data_scope']=='strict_history' else 'research'):
            raise ValueError('scope_and_training_mode_mismatch')
        if c['data_scope'] != 'strict_history' and any(c['split'].get('min_samples',{}).get(p,0)<MINIMUM[p] for p in ('train','validation','test')):
            raise ValueError('scoped_training_partition_minimum_not_met')
    check_features(c['features'])
    if not c.get('models') or len(set(c['models'])) != len(c['models']) or set(c['models']) - set(MODELS[c['task']]): raise ValueError('fixed_model_set_required')
    if not isinstance(c.get('seed'), int) or isinstance(c['seed'], bool): raise ValueError('integer_seed_required')
    if c.get('one_class_policy') not in ('error', 'constant'): raise ValueError('one_class_policy_required')
    if c.get('model_selection') != 'none': raise ValueError('model_selection_not_implemented_no_test_selection')
    s = c['simulation']
    if s['horizon'] != c['horizon']: raise ValueError('simulation_horizon_mismatch')
    for field in ('regression_threshold', 'classification_threshold'):
        v = c[field]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v): raise ValueError('finite_fixed_threshold_required')
    if not 0 <= c['classification_threshold'] <= 1: raise ValueError('probability_threshold_out_of_range')
    timestamp(c['evaluation_asof'])
    return c


def check_input(pit, store, c):
    validate_config(c)
    fixed, rows = load_fixed(store, pit, c['dataset_snapshot_id'], c['dataset_content_sha256'], c['mode'])
    if any(r['feature'].get('scope_contract') for r in rows) and not c.get('data_scope'):
        raise ValueError('scoped_dataset_requires_explicit_training_scope')
    eligibility = eligibility_report(store, pit, c['dataset_snapshot_id'], rows, c['evaluation_asof'], c['mode'])
    splits = make_splits(store, pit, c['dataset_snapshot_id'], rows, c['split'], c['mode'])
    scoped = None
    if c.get('data_scope') and c['data_scope'] != 'strict_history':
        from ..datasets.store import training_selection
        scoped = training_selection(store,pit,c['dataset_snapshot_id'],training_asof=c['evaluation_asof'],scope=c['data_scope'])
    status = 'BLOCKED' if c['mode'] == 'strict' and not eligibility['selected_count'] else splits['status']
    if scoped is not None:
        from ..observed.operation import MINIMUM
        from datetime import date
        selected_ids={r['sample_id'] for r in scoped['selected'] if r['horizon_sessions']==c['horizon']}
        dates=sorted({r['feature']['decision_at'][:10] for r in rows if r['sample_id'] in selected_ids})
        span=(date.fromisoformat(dates[-1])-date.fromisoformat(dates[0])).days if dates else 0
        if not selected_ids: status='BLOCKED'
        elif len(selected_ids)<MINIMUM['samples'] or span<MINIMUM['date_span_days']: status='INSUFFICIENT_DATA'
    task_audit = []
    for fold in splits['folds']:
        _, y, members, excluded = training_rows(rows, fold['partitions']['train'], c, fold['training_asof'])
        if c.get('data_scope') and c['task']=='classification' and len(set(y))<2 and status!='BLOCKED': status='INSUFFICIENT_DATA'
        task_audit.append({'fold_id': fold['fold_id'], 'selected_count': len(y), 'membership': members, 'excluded': excluded,
                           'exclusion_counts': dict(Counter(reason for item in excluded for reason in item['reasons']))})
    if status == 'OK' and any(a['selected_count'] < max(8, c['split'].get('min_samples', {}).get('train', 1)) for a in task_audit): status = 'INSUFFICIENT_DATA'
    report = {'status': status, 'scope_selection': scoped, 'task_input_audit': task_audit, 'mode': c['mode'], 'eligibility': eligibility, 'splits': splits,
              'reason': 'strict_dataset_has_no_eligible_samples' if status == 'BLOCKED' else None}
    return fixed, rows, report


def training_rows(rows, items, c, asof):
    selected = {key(i) for i in items}
    x, y, membership, excluded = [], [], [], []
    for r in rows:
        if key(r) not in selected: continue
        l = r['label']
        try:
            if l['status'] != 'ready' or not l.get('label_available_at') or timestamp(l['label_available_at']) > timestamp(asof): raise ValueError('label_not_mature_at_training_asof')
            if c.get('data_scope') and c['data_scope'] != 'strict_history':
                from ..observed.scope import learning_reasons
                if c['features'] != r['feature'].get('required_features'): raise ValueError('scoped_model_feature_list_mismatch')
                blocked=learning_reasons(r['feature'],l,c['data_scope'],asof,
                    rights_asof=c['evaluation_asof'] if c['data_scope'] == 'fixed_security_research' else None)
                if blocked: raise ValueError('scoped_training_rejected:'+','.join(blocked))
            if c['mode'] != 'synthetic':
                if l.get('learning_rights_status') != 'verified_assertion' or l.get('learning_rights_reason'): raise ValueError('research_training_rights_unconfirmed')
                rights = []
                for ref in l.get('evidence_dependencies', []):
                    body = Path(ref['path']).read_bytes()
                    if sha256(body) != ref['sha256']: raise ValueError('learning_rights_evidence_hash_mismatch')
                    value = json.loads(body)
                    if value.get('kind') == 'learning_rights': rights.append(value)
                if not rights or not all(v.get('verified') is True and v.get('input_domain') == 'market' and v.get('model_training_allowed') is True for v in rights): raise ValueError('model_training_permission_not_granted')
                rights_asof = c['evaluation_asof'] if c.get('data_scope') == 'fixed_security_research' else asof
                if not l.get('learning_rights_available_at') or timestamp(l['learning_rights_available_at']) > timestamp(rights_asof): raise ValueError('learning_rights_not_available_at_training_asof')
            if c['target_id'] == 'cash_total_return' and l.get('cash_total_return_status') != 'ready': raise ValueError('total_return_not_ready_no_price_fallback')
            target = l.get(c['target_id'])
            if isinstance(target, bool) or not isinstance(target, (int, float)) or not np.isfinite(target): raise ValueError('target_missing_or_nonfinite')
            projected = project_features(r['feature'], c['features'])
            x.append(projected); y.append(float(target) if c['task'] == 'regression' else int(target > 0))
            membership.append({k: r[k] for k in ('sample_id', 'horizon_sessions', 'feature_version_id', 'label_version_id')})
        except ValueError as exc: excluded.append({'sample_id': r['sample_id'], 'reasons': [str(exc)]})
    return x, y, membership, excluded


def feature_inputs(rows, items):
    chosen = {key(i) for i in items}
    # No label value/state/availability reaches prediction generation.
    return [{k: r[k] for k in ('sample_id', 'feature_version_id', 'feature')} for r in rows if key(r) in chosen]


def infer(artifact, inputs, dataset_id, dataset_hash, mode):
    c, pr = artifact['content'], artifact['content']['provenance']
    if pr['mode'] != mode: raise ValueError('model_mode_promotion_forbidden')
    if pr['dataset_snapshot_id'] != dataset_id or pr['dataset_content_sha256'] != dataset_hash: raise ValueError('model_dataset_binding_mismatch')
    # Offline models must never invent historical operational existence.
    if mode == 'strict': raise ValueError('offline_artifact_not_authorized_for_operational_predictions')
    generated, predictions, excluded = utc_now(), [], []
    for r in inputs:
        f = r['feature']
        try:
            if timestamp(pr['training_asof']) > timestamp(f['decision_at']): raise ValueError('model_logical_asof_after_decision')
            if f['sample_status'] != 'ready': raise ValueError('prediction_feature_not_ready')
            value = predict(c['pipeline'], [project_features(f, pr['features'])])[0]
            p = {'prediction_id': sha256(canonical([artifact['model_id'], r['sample_id'], pr['horizon']])),
                 'prediction_version': PREDICTION_VERSION, 'sample_id': r['sample_id'], 'model_or_signal_id': artifact['model_id'],
                 'model_version': VERSION, 'training_asof': pr['training_asof'], 'generated_at': generated,
                 'simulated_generated_at': f['decision_at'], 'logical_model_usable_at': pr['training_asof'],
                 'model_training_completed_at': artifact['execution_metadata']['training_completed_at'],
                 'generation_kind': 'replay', 'decision_at': f['decision_at'], 'intended_entry_at': f['intended_entry_at'],
                 'horizon': pr['horizon'], 'output_task': pr['task'], 'target_id': pr['target_id'],
                 'predicted_return': value if pr['task'] == 'regression' else None,
                 'probability_up': value if pr['task'] == 'classification' else None,
                 'dataset_snapshot_id': dataset_id, 'dataset_content_sha256': dataset_hash, 'feature_version_id': r['feature_version_id'],
                 'feature_snapshot_id': f['feature_snapshot_id'], 'mode': mode, 'oracle': False}
            predictions.append(p)
        except ValueError as exc: excluded.append({'sample_id': r['sample_id'], 'reasons': [str(exc)]})
    return predictions, excluded


def save_result(output_root, result):
    digest = sha256(canonical(result))
    result['result_content_sha256'] = digest
    write_json(Path(output_root)/'results'/f'{digest}.json', result)
    write_json(Path(output_root)/'result.json', result)


def run_training(pit, store, c, output_root, project_root='.'):

    fixed, rows, checked = check_input(pit, store, c)
    result = {'version': VERSION, 'status': checked['status'], 'mode': c['mode'], 'config': c,
              'input_check': checked, 'folds': [], 'real_predictive_power': 'NOT_EVALUATED', 'model_selection': 'none'}
    output_root = Path(output_root)
    if checked['status'] == 'BLOCKED':
        result['reason'] = checked['reason']; save_result(output_root, result); return result
    if c.get('data_scope') and checked['status'] != 'OK':
        save_result(output_root,result); return result
    for fold in checked['splits']['folds']:
        fr = {'fold_id': fold['fold_id'], 'status': fold['status'], 'training_asof': fold['training_asof'], 'models': []}
        result['folds'].append(fr)
        if fold['status'] != 'OK': continue
        x, y, members, excluded = training_rows(rows, fold['partitions']['train'], c, fold['training_asof'])
        fr.update(training_count=len(y), training_membership=members, training_excluded=excluded,
                  training_exclusion_counts=dict(Counter(reason for r in excluded for reason in r['reasons'])))
        minimum = max(8, c['split'].get('min_samples', {}).get('train', 1))
        if len(y) < minimum:
            fr.update(status='INSUFFICIENT_DATA', reason='post_contract_training_below_minimum'); continue
        train_ids = {m['sample_id'] for m in members}
        if any(train_ids & {m['sample_id'] for m in fold['partitions'][p]} for p in ('validation', 'test')): raise ValueError('duplicate_train_evaluation_sample')
        for name in c['models']:
            mr = {'model_name': name, 'status': 'OK'}; fr['models'].append(mr)
            started = utc_now()
            try: pipeline = fit_pipeline(x, y, c['features'], c['task'], name, c['seed'], one_class=c['one_class_policy'], minimum=minimum)
            except ValueError as exc: mr.update(status='BLOCKED', reason=str(exc)); continue
            prov = {k: c[k] for k in ('mode', 'target_id', 'horizon', 'task', 'features', 'dataset_snapshot_id', 'dataset_content_sha256', 'seed')}
            if c.get('data_scope'): prov['data_scope']=c['data_scope']
            prov.update(target_contract={'label_definition_versions': sorted({r['label']['definition_version'] for r in rows}),
                                         'entry': 'next session open', 'exit': 'entry+h sessions open',
                                         'include_dividend_receivables': fixed['metadata']['config'].get('include_dividend_receivables')},
                        training_asof=fold['training_asof'], fold_id=fold['fold_id'], training_membership=members,
                        training_membership_sha256=sha256(canonical(members)), split_contract=c['split'], run_config=c,
                        data_conditions=[{'sample_id': r['sample_id'], 'price_contract': r['feature']['price_contract'],
                                          'security_record_ids': r['feature']['security_record_ids'], 'identifier_verification': r['feature']['identifier_verification'],
                                          'learning_rights_status': r['label'].get('learning_rights_status'),
                                          'learning_rights_available_at': r['label'].get('learning_rights_available_at'),
                                          'query_policy': r['feature']['query_policy'], 'execution_status': r['feature']['execution_status']}
                                         for r in rows if key(r) in {key(m) for m in members}])
            saved = save_model(output_root/'models', pipeline, prov, project_root, started)
            artifact = load_model(saved['path'], output_root/'models', saved['model_id'], c['mode'], project_root)
            if not np.allclose(predict(pipeline, x), predict(artifact['content']['pipeline'], x), rtol=1e-12, atol=1e-12): raise ValueError('reload_prediction_mismatch')
            mr.update(artifact=saved, reload_predictions='PASS', export_validation=pipeline['export_validation'], notes=pipeline['notes'], partitions={})
            for partition in ('validation', 'test'):
                items = fold['partitions'][partition]
                preds, rejects = infer(artifact, feature_inputs(rows, items), c['dataset_snapshot_id'], c['dataset_content_sha256'], c['mode'])
                eval_rows = [r for r in rows if key(r) in {key(i) for i in items}]
                pc = validate_predictions(preds, eval_rows, c['dataset_snapshot_id'], c['dataset_content_sha256'], c['mode'])
                if pc['status'] == 'INVALID': raise ValueError('generated_prediction_contract_invalid')
                metrics = evaluate(eval_rows, pc['accepted'], evaluation_asof=c['evaluation_asof'], mode=c['mode'], horizon=c['horizon'])
                pred_path = output_root/'predictions'/f'{saved["model_id"]}-{partition}.json'
                write_json(pred_path, preds)
                mr['partitions'][partition] = {'predictions_path': str(pred_path), 'predictions_sha256': sha256(canonical(preds)),
                                              'prediction_validation': pc['status'], 'excluded': rejects, 'metrics': metrics}
                if partition == 'test':
                    if c['simulation'].get('enabled') is False:
                        mr['simulation_summary'] = {'status': 'NOT_RUN', 'reason': c['simulation']['disabled_reason']}
                        continue
                    sc = {k: c[k] for k in ('mode', 'dataset_snapshot_id', 'dataset_content_sha256', 'evaluation_asof', 'split')}
                    sc.update(fold_index=int(fold['fold_id']), simulation=c['simulation'] | {'signal_threshold': c['classification_threshold'] if c['task'] == 'classification' else c['regression_threshold']},
                              cost_scenarios=c.get('cost_scenarios', {}))
                    run = save_run(output_root/'stage4_runs', pit, store, sc, preds, project_root)
                    mr['stage4'] = run
                    replay = replay_run(run['path'], pit, store, project_root)
                    if replay['status'] != 'PASS': raise ValueError('stage4_fixed_replay_failed')
                    mr['stage4_replay'] = replay['status']
                    sr = read_run(run['path'])['result']
                    mr['simulation_summary'] = (sr.get('simulation') or {}).get('performance')
                    mr['cash_summary'] = ((sr.get('comparisons') or {}).get('cash') or {}).get('performance')
                    if run['status'] not in ('OK', 'PARTIAL'): mr['status'] = run['status']
            if any(v['prediction_validation'] != 'OK' or v['metrics']['status'] != 'OK' for v in mr['partitions'].values()): mr['status'] = 'PARTIAL'
        if any(m['status'] != 'OK' for m in fr['models']): fr['status'] = 'PARTIAL'
    result['status'] = 'OK' if result['folds'] and all(f['status'] == 'OK' for f in result['folds']) else 'INSUFFICIENT_DATA' if not result['folds'] or all(f['status'] == 'INSUFFICIENT_DATA' for f in result['folds']) else 'PARTIAL'
    if c['mode'] == 'research' and result['status'] == 'OK':
        result['real_predictive_power'] = 'EVALUATED_LIMITED_RESEARCH_NOT_STRICT_PIT'
    result['parameter_retraining_scope'] = 'Compare pipeline hashes in pinned environment; byte-identical retraining across platforms is not promised. Reload rtol=atol=1e-12.'
    save_result(output_root, result)
    return result
