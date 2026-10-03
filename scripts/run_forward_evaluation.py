"""One Linux-only frozen forward experiment; no fitting, scheduling or trading."""
import argparse
from collections import Counter
from datetime import date, datetime
import fcntl
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from market_research.collect import collect_batch
from market_research.http import HTTPClient, utc_now
from market_research.storage import Store, canonical, sha256, write_json
from market_research.pit import Database, import_artifacts, query, create_snapshot, replay_snapshot
from market_research.pit.importer import import_calendar
from market_research.pit.time import timestamp
from market_research.datasets.calendar import SessionCalendar
from market_research.datasets.builder import generate_feature, generate_labels, request_contract
from market_research.datasets.features import compute_features
from market_research.datasets.store import DatasetStore
from market_research.training.artifacts import load_model
from market_research.training.pipeline import project_features, predict
from market_research.stage4.metrics import evaluate

PROJECT = Path(__file__).resolve().parents[1]
CONFIG_SHA256 = '090a1360915249aa3752528526a6f7be5d8ca77dce02a00a0bfe4e9f5861c29d'


def read_sealed(path):
    a = json.loads(Path(path).read_text())
    if a['sha256'] != sha256(canonical(a['content'])):
        raise ValueError('record_hash_mismatch')
    return a['content']


def seal(path, content):
    """Called under the one-root flock; never replace a different existing record."""
    path = Path(path)
    if path.exists():
        if read_sealed(path) != content:
            raise ValueError('immutable_record_conflict')
    else:
        write_json(path, {'content': content, 'sha256': sha256(canonical(content))})


def load_frozen(config):
    if sha256(canonical(config)) != CONFIG_SHA256:
        raise ValueError('frozen_config_hash_mismatch')
    pipelines = []
    for spec in config['models']:
        p = PROJECT / spec['path']
        if sha256(p.read_bytes()) != spec['artifact_sha256']:
            raise ValueError('frozen_artifact_file_hash_mismatch')
        a = load_model(p, p.parent, spec['model_id'], 'research', PROJECT)
        c, v = a['content'], a['content']['provenance']
        pipeline = c['pipeline']
        if (v['task'] != 'classification' or v['target_id'] != config['target_id'] or
            v['horizon'] != 5 or v['target_contract'] != config['target_contract'] or
            v['training_membership_sha256'] != config['training_membership_sha256'] or
            v['dataset_snapshot_id'] != config['dataset_snapshot_id'] or
            v['training_asof'] != config['training_asof'] or v['seed'] != config['seed'] or
            len(v['training_membership']) != config['training_count'] or
            sha256(canonical(v['training_membership'])) != v['training_membership_sha256'] or
            c['pipeline_sha256'] != spec['pipeline_sha256'] or
            pipeline['settings'] != spec['settings'] or
            pipeline['preprocessing']['features'] != config['features'] or
            sha256(canonical(pipeline['preprocessing'])) != spec['preprocessing_sha256']):
            raise ValueError('frozen_model_contract_mismatch')
        pipelines.append(pipeline)
    if pipelines[0]['model']['value'] != config['baseline_probability']:
        raise ValueError('frozen_baseline_probability_mismatch')
    p = PROJECT / config['calendar_path']
    if sha256(p.read_bytes()) != config['calendar_sha256']:
        raise ValueError('frozen_calendar_hash_mismatch')
    return pipelines


def timing(cal, decision_date, now, earliest='2026-10-05'):
    """The same close-to-decision window as observed-run; no Tuesday substitute."""
    if decision_date not in cal.index:
        return 'dry_run', 'NON_TRADING_DAY'
    if date.fromisoformat(decision_date).weekday() != 0:
        return 'dry_run', 'NOT_MONDAY'
    plan = cal.plan(decision_date, (5,))
    if not plan['intended_entry_at'] or not plan['exits'][5]:
        return 'blocked', 'CALENDAR_TARGET_UNAVAILABLE'
    if now >= plan['intended_entry_at'] or now > plan['decision_at']:
        return 'missed', 'MISSED_DECISION'
    if decision_date < earliest:
        return 'missed', 'BEFORE_FROZEN_FORWARD_START'
    if now < cal.by_date[decision_date]['close_utc']:
        return 'dry_run', 'SESSION_NOT_CLOSED'
    return 'prospective', None


def input_gate(rows, feature, cutoff):
    if any(timestamp(r['availability']['available_at']) > cutoff for r in rows):
        return 'INPUT_AFTER_CUTOFF'
    if not rows or feature['last_observation_date'] != feature['anchor_date']:
        return 'STALE_OR_MISSING_CURRENT_SESSION'
    if any(feature['features'].get(n, {}).get('status') != 'ready' for n in feature['required_features']):
        return 'REQUIRED_FEATURE_UNAVAILABLE'
    if feature.get('sample_status', feature.get('status')) != 'ready':
        return 'FEATURE_CONTRACT_UNMET'
    return None


def save_pair(root, record):
    path = root / 'predictions' / (record['pair_id'] + '.json')
    if path.exists():
        old = read_sealed(path)
        if old['input_sha256'] != record['input_sha256']:
            raise ValueError('PREDICTION_INPUT_CONFLICT')
        return old, True
    seal(path, record)
    return record, False


def label_config(c, symbol, cal, asof):
    # An external reviewed evidence index may add new label versions, never model/input settings.
    evidence_path = PROJECT / c['label_evidence_index']
    evidence = json.loads(evidence_path.read_text()).get(symbol, {}) if evidence_path.exists() else {}
    if set(evidence) - {'action_coverage', 'trading_state'}:
        raise ValueError('unsupported_label_evidence_key')
    return {'series': {'source': c['source'], 'symbol': symbol, 'price_kind': 'ohlcv',
                      'price_semantics': 'as_traded', 'currency': 'USD'},
            'input_domain': 'market', 'calendar_version': cal.version,
            'feature_policy': 'observed', 'label_policy': 'observed',
            'identifier_requirement': 'verified', 'symbol_mode': 'provider_label',
            'as_traded_mode': 'price_change_only', 'required_features': c['features'],
            'label_asof': asof, 'simulation_asof': asof, 'normal_label_delay_hours': 48,
            'include_dividend_receivables': True, **evidence}


def collect_current(c, root, cal, end, refresh):
    specs = [{'source': 'eodhd', 'kind': k, 'symbol': s, 'start': c['lookback_start'],
              'end': end, 'auth_mode': 'demo'} for s in c['symbols']
             for k in ('daily', 'splits', 'dividends', 'identity')]
    results = collect_batch(specs, root / 'collection', PROJECT, canonical(c), refresh=refresh,
                            client=HTTPClient(timeout=20, max_attempts=1))
    by_key = {(r['manifest_record']['request_parameters']['symbol'],
               r['manifest_record']['request_parameters']['kind']): r['manifest_record'] for r in results}
    failures = {}
    for s in c['symbols']:
        records = {k: by_key[s, k] for k in ('daily', 'splits', 'dividends', 'identity')}
        bad = [k + ':' + v['status'] for k, v in records.items() if v['status'] not in ('success', 'empty_response')]
        if records['daily']['status'] != 'success' or records['identity']['status'] != 'success' or bad:
            failures[s] = bad or ['DAILY_OR_IDENTITY_EMPTY']; continue
        identity_record = records['identity']
        p = root / 'collection' / identity_record['normalized_path']
        if sha256(p.read_bytes()) != identity_record['normalized_sha256']:
            failures[s] = ['IDENTITY_HASH_MISMATCH']; continue
        identity = json.loads(p.read_text())['metadata']
        if (identity.get('Code') != s or identity.get('Type') != 'Common Stock' or
            identity.get('CurrencyCode') != 'USD' or
            'ISIN:' + str(identity.get('ISIN')) != c['identities'][s]['external_security_id']):
            failures[s] = ['SECURITY_IDENTITY_CHANGED']; continue
        for k in ('daily', 'splits', 'dividends'):
            m = records[k]
            if m['status'] == 'empty_response': continue
            p = root / 'collection' / m['normalized_path']
            if sha256(p.read_bytes()) != m['normalized_sha256']:
                failures[s] = ['NORMALIZATION_HASH_MISMATCH']; break
            out = json.loads(p.read_text())
            # Reuse provider field semantics; attach only a matched current security identity.
            # Never claim regular-auction verification or historical no-halt/action completeness.
            claim = c['identities'][s]
            out['provenance']['current_identity_match'] = {
                'method': 'matched frozen ISIN, symbol, Common Stock and USD against current provider response',
                'path': str(root / 'collection' / identity_record['raw_path']), 'sha256': identity_record['sha256']}
            for row in out['bars'] + out['actions']:
                row.update(security_identity=claim, local_instrument_id='reviewed:' + claim['external_security_id'])
                if k == 'daily' and row['trading_date'] in cal.index:
                    row.update(observation_end_utc=cal.by_date[row['trading_date']]['close_utc'],
                               observation_end_evidence='reference_calendar_not_vendor_session_proof')
            norm_path = Path('normalized') / 'eodhd' / m['request_id'] / (sha256(canonical(out)) + '.json')
            full = root / 'collection' / norm_path
            # A cached overlay already processed under this exact identity remains unchanged.
            if full == p: continue
            write_json(full, out)
            completed = utc_now()
            overlay = m | {'normalized_path': str(norm_path), 'normalized_sha256': sha256(full.read_bytes()),
                'network_download_performed': False, 'origin_receipt_id': m.get('receipt_id', m.get('origin_receipt_id')),
                'recorded_at_utc': completed, 'processing_completed_at_utc': completed}
            overlay.pop('receipt_id', None)
            Store(root / 'collection').save_record(overlay)
    return failures


def update_labels(pit, store, cal, root, c, clock=utc_now):
    results = []
    for path in sorted((root / 'predictions').glob('*.json')):
        record = read_sealed(path)
        if record['execution_kind'] != 'prospective': continue
        now = timestamp(clock())
        if now < record['target_end_at']:
            results.append({'pair_id': record['pair_id'], 'status': 'pending', 'reason': 'target_open_not_yet_reached'})
            continue
        try:
            feature = store.payload('feature', record['feature_version_id'])
            replay_snapshot(pit, feature['feature_snapshot_id'])
            cfg = label_config(c, record['symbol'], cal, now)
            labels = generate_labels(pit, cal, {'decision_date': record['decision_date']}, cfg)
            label = next(l for l in labels if l['horizon_sessions'] == 5)
            completed = timestamp(clock())
            label.update(sample_id=feature['sample_id'], label_materialized_at=completed,
                         label_processing_time_basis='actual_operational_record')
            if label['status'] == 'ready': label['label_available_at'] = max(label['label_available_at'], completed)
            lid = store.add_label(label)
            link = {'pair_id': record['pair_id'], 'label_version_id': lid, 'evaluated_at': completed,
                    'status': label['status'], 'reason': label['reason'],
                    'evidence_index_sha256': sha256(canonical({k:cfg[k] for k in ('action_coverage','trading_state') if k in cfg}))}
            seal(root / 'label_updates' / record['pair_id'] / (sha256(canonical(link)) + '.json'), link)
            results.append(link)
        except (ValueError, OSError) as exc:
            results.append({'pair_id': record['pair_id'], 'status': 'blocked', 'reason': str(exc)})
    return results


def summary(pit, store, root, c, asof, current=None):
    rows, preds = [], [[], []]
    counts = Counter(); per_symbol = {s: 0 for s in c['symbols']}
    for path in sorted((root / 'predictions').glob('*.json')):
        p = read_sealed(path)
        if p['execution_kind'] != 'prospective': continue
        links = [read_sealed(x) for x in (root / 'label_updates' / p['pair_id']).glob('*.json')]
        link = max(links, key=lambda l: l['evaluated_at']) if links else None
        label = store.payload('label', link['label_version_id'] if link else p['pending_label_version_id'])
        counts[label['status']] += 1
        if label['status'] != 'ready' or not label['label_available_at'] or label['label_available_at'] > asof: continue
        feature = store.payload('feature', p['feature_version_id'])
        rows.append({'sample_id': feature['sample_id'], 'horizon_sessions': 5, 'feature': feature, 'label': label})
        per_symbol[p['symbol']] += 1
        for i, prediction in enumerate(p['predictions']): preds[i].append(prediction)
    # Count latest attempted decision/security slots, not repeated execution attempts.
    attempts = [read_sealed(p) for p in (root / 'executions').glob('*.json')]
    if current: attempts.append(current)
    latest = {}
    for attempt in sorted(attempts, key=lambda a: a['completed_at']):
        for result in attempt['results']:
            if result['execution_kind'] in ('prospective', 'missed'):
                latest[attempt['decision_date'], result['symbol']] = result['status']
    attempted_counts = Counter(latest.values())
    predicted_slots = {(read_sealed(p)['decision_date'], read_sealed(p)['symbol'])
                       for p in (root / 'predictions').glob('*.json')}
    scores = []
    for spec, prediction in zip(c['models'], preds):
        metrics = evaluate(rows, prediction, evaluation_asof=asof, mode='research', horizon=5)
        scores.append({'model_id': spec['model_id'], 'count': metrics['count'],
                       'brier': metrics['metrics_row_weighted']['brier']['value'],
                       'log_loss': metrics['metrics_row_weighted']['binary_log_loss']['value'],
                       'excluded': metrics['exclusion_counts']})
    return {'unique_decision_dates': len({r['feature']['decision_at'][:10] for r in rows}),
            'mature_samples': len(rows), 'mature_samples_by_symbol': per_symbol,
            'actual_up_rate': sum(r['label']['up_5'] for r in rows)/len(rows) if rows else None,
            'missing_prediction_count': len(set(latest) - predicted_slots),
            'failed_count': attempted_counts['blocked'], 'missed_count': attempted_counts['missed'],
            'label_status_counts': dict(counts), 'models': scores,
            'interpretation': 'Five securities on one date are one market decision date; descriptive only.'}


def run(c, args):
    pipelines = load_frozen(c)
    root = PROJECT / c['output_root']; root.mkdir(parents=True, exist_ok=True)
    started = timestamp(utc_now())
    today = datetime.fromisoformat(started.replace('Z', '+00:00')).astimezone(ZoneInfo(c['timezone'])).date().isoformat()
    decision_date = args.decision_date or today
    with (root / 'operation.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with Database(root / 'pit.sqlite', initialize=True) as pit, DatasetStore(root / 'datasets.sqlite') as store:
            import_calendar(pit, PROJECT / c['calendar_path'], domain='market')
            cal_version = pit.conn.execute('SELECT calendar_version FROM trading_session_versions LIMIT 1').fetchone()[0]
            cal = SessionCalendar(pit, cal_version)
            kind, reason = timing(cal, decision_date, started, c['earliest_decision_date'])
            if decision_date > today: kind, reason = 'dry_run', 'FUTURE_DECISION_DIAGNOSTIC_ONLY'
            if args.dry_run: kind, reason = 'dry_run', reason or 'EXPLICIT_DRY_RUN'
            # Bootstrap only this NEW database. Original collection/DB/models/snapshots are read-only inputs.
            imported = import_artifacts(pit, PROJECT / c['seed_collection'], domain='market')
            if imported['quarantined']: raise ValueError('seed_collection_quarantined')
            closed = [r['trading_date'] for r in cal.rows if timestamp(r['close_utc']) <= started]
            if not closed: raise ValueError('calendar_has_no_closed_session')
            collection_failures = {}
            if not args.cache_only:
                collection_failures = collect_current(c, root, cal, closed[-1], args.refresh)
            if (root / 'collection' / 'manifest').exists():
                imported = import_artifacts(pit, root / 'collection', domain='market')
                if imported['quarantined']:
                    # Imports commit per artifact. Preserve unaffected securities and block
                    # every security linked to a quarantined manifest in this import.
                    for issue in pit.conn.execute('SELECT manifest_path,reason FROM ingest_issues WHERE run_id=?', (imported['run_id'],)):
                        spec = json.loads(Path(issue['manifest_path']).read_text())['request_parameters']
                        collection_failures.setdefault(spec['symbol'], []).append('IMPORT_QUARANTINE:' + issue['reason'])
            cutoff = timestamp(utc_now())
            if kind == 'prospective':
                kind, reason = timing(cal, decision_date, cutoff, c['earliest_decision_date'])
            outcome = []
            if not args.update_only:
                for symbol in c['symbols']:
                    result = {'symbol': symbol, 'execution_kind': kind, 'status': kind, 'reason': reason}
                    try:
                        if symbol in collection_failures:
                            result.update(status='blocked', reason='COLLECTION_FAILED', details=collection_failures[symbol])
                        elif kind in ('missed', 'blocked'):
                            if decision_date in cal.index:
                                plan = cal.plan(decision_date, (5,))
                                pid = sha256(canonical([symbol, plan['decision_at'], [m['model_id'] for m in c['models']]]))
                                path = root / 'predictions' / (pid + '.json')
                                if path.exists():
                                    saved = read_sealed(path)
                                    feature = store.payload('feature', saved['feature_version_id'])
                                    replay_snapshot(pit, feature['feature_snapshot_id'])
                                    result.update(status='pending', reason='FROZEN_PREDICTION_REPLAY_NO_NEW_GENERATION',
                                                  pair_id=pid, idempotent_replay=True, generated_at=saved['generated_at'])
                        else:
                            cfg = label_config(c, symbol, cal, cutoff)
                            anchor = decision_date if kind == 'prospective' else closed[-1]
                            data = query(pit, cutoff=cutoff, start=cal.window(anchor, 61)[0], end=anchor,
                                         **request_contract(cfg, 'feature_policy'))
                            if kind == 'prospective':
                                feature = generate_feature(pit, cal, {'decision_date': decision_date, 'feature_cutoff': cutoff}, cfg)
                                feature.update(feature_ready_at=timestamp(utc_now()), feature_ready_basis='actual_operational_record')
                            else:
                                feature = compute_features(data, cal, anchor_date=anchor, price_kind='ohlcv',
                                    as_traded_mode='price_change_only', expected_semantics='as_traded', expected_currency='USD')
                                feature.pop('selected_inputs')
                                snapshot = create_snapshot(pit, data)
                                result.update(feature_snapshot_id=snapshot['snapshot_id'],
                                              feature_snapshot_sha256=snapshot['content_sha256'])
                            feature['required_features'] = c['features']
                            failure = input_gate(data['data'], feature, cutoff)
                            if failure: result.update(status='blocked', reason=failure, query_exclusions=data['exclusion_counts'])
                            else:
                                projected = project_features(feature, c['features'])
                                probabilities = [predict(pipeline, [projected])[0] for pipeline in pipelines]
                                generated = timestamp(utc_now())
                                result.update(probabilities=probabilities, anchor_date=anchor, cutoff=cutoff,
                                              generated_at=generated, last_observation_date=feature['last_observation_date'],
                                              projected_features_sha256=sha256(canonical(projected)),
                                              maximum_input_available_at=max(r['availability']['available_at'] for r in data['data']))
                                if kind == 'dry_run':
                                    result.update(status='dry_run', prospective_prediction_saved=False)
                                else:
                                    k, why = timing(cal, decision_date, generated, c['earliest_decision_date'])
                                    if k != 'prospective': result.update(status=k, reason=why)
                                    else:
                                        snapshot = replay_snapshot(pit, feature['feature_snapshot_id'])['result']
                                        used = [r for r in snapshot['data'] if r['version_id'] in feature['used_version_ids']]
                                        plan = cal.plan(decision_date, (5,))
                                        fid = store.add_feature(feature)
                                        pending = next(l for l in generate_labels(pit, cal, {'decision_date': decision_date}, cfg) if l['horizon_sessions']==5)
                                        pending['sample_id'] = feature['sample_id']; lid = store.add_label(pending)
                                        pair_id = sha256(canonical([symbol, plan['decision_at'], [m['model_id'] for m in c['models']]]))
                                        input_hash = sha256(canonical({'projected': projected, 'versions': sorted(r['version_id'] for r in used),
                                                                      'calendar': cal.version, 'config': CONFIG_SHA256}))
                                        record = {'pair_id': pair_id, 'symbol': symbol, 'security_identifier': c['identities'][symbol]['external_security_id'],
                                            'decision_date': decision_date, 'decision_at': plan['decision_at'], 'feature_cutoff': cutoff,
                                            'generated_at': generated, 'target_id': 'price_return', 'target_up_rule': 'price_return_5>0',
                                            'target_start_at': timestamp(plan['intended_entry_at']), 'target_end_at': timestamp(plan['exits'][5]['open_utc']),
                                            'feature_version_id': fid, 'feature_snapshot_id': feature['feature_snapshot_id'],
                                            'feature_snapshot_sha256': feature['feature_snapshot_sha256'], 'input_sha256': input_hash,
                                            'config_sha256': CONFIG_SHA256, 'script_sha256': sha256(Path(__file__).read_bytes()),
                                            'execution_kind': 'prospective', 'status': 'pending', 'reason': 'target_open_not_yet_reached',
                                            'pending_label_version_id': lid, 'limitation': c['limitation'],
                                            'receipt_processing_evidence': [{'version_id': r['version_id'], 'availability': r['availability'],
                                                                          'provenance': r['provenance']} for r in used],
                                            'predictions': [{'prediction_id': sha256(canonical([pair_id, m['model_id']])),
                                                'sample_id': feature['sample_id'], 'horizon': 5, 'target_id': 'price_return',
                                                'model_id': m['model_id'], 'artifact_sha256': m['artifact_sha256'],
                                                'settings_id': sha256(canonical(m['settings'])), 'baseline_value': c['baseline_probability'] if i==0 else None,
                                                'probability_up': probabilities[i], 'predicted_return': None, 'generated_at': generated}
                                                for i,m in enumerate(c['models'])]}
                                        if timing(cal, decision_date, timestamp(utc_now()), c['earliest_decision_date'])[0] != 'prospective':
                                            raise ValueError('MISSED_DECISION_BEFORE_RECORD_COMMIT')
                                        saved, replay = save_pair(root, record)
                                        result.update(status='pending', reason=saved['reason'], pair_id=pair_id, idempotent_replay=replay,
                                                      generated_at=saved['generated_at'], probabilities=[p['probability_up'] for p in saved['predictions']])
                    except (ValueError, OSError, KeyError) as exc:
                        result.update(status='missed' if str(exc).startswith('MISSED_DECISION') else 'blocked', reason=str(exc))
                    outcome.append(result)
            updates = update_labels(pit, store, cal, root, c, clock=utc_now)
            now = timestamp(utc_now())
            report = {'started_at': started, 'completed_at': now, 'execution_kind': kind,
                      'decision_date': decision_date, 'config_sha256': CONFIG_SHA256,
                      'models_verified': [m['model_id'] for m in c['models']], 'results': outcome,
                      'label_updates': updates, 'collection_failures': collection_failures,
                      'run_status_counts': dict(Counter(r['status'] for r in outcome)),
                      'time_attestation': 'Local timestamps and hashes are reproducibility records, not independent timestamp certification.'}
            report['evaluation'] = summary(pit, store, root, c, now, report)
            seal(root / 'executions' / (sha256(canonical(report)) + '.json'), report)
            write_json(root / 'latest.json', report)
            return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='configs/forward_evaluation.json')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--cache-only', action='store_true', help='No HTTP: use preserved actual receipts; freshness still required')
    parser.add_argument('--refresh', action='store_true', help='One new bounded HTTP attempt per request, preserve old receipts')
    parser.add_argument('--update-only', action='store_true')
    parser.add_argument('--decision-date', help='Diagnostic/missed reporting; cannot backdate generation')
    args = parser.parse_args()
    if args.cache_only and args.refresh: parser.error('--cache-only and --refresh conflict')
    try:
        c = json.loads((PROJECT / args.config).read_text())
        report = run(c, args)
        print(json.dumps({k:report[k] for k in ('execution_kind','decision_date','run_status_counts','evaluation')}, ensure_ascii=False, indent=2))
        return 3 if any(r['status'] in ('blocked','missed') for r in report['results']) or report['collection_failures'] else 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'status':'ERROR','reason':str(exc)},ensure_ascii=False)); return 1


if __name__ == '__main__':
    raise SystemExit(main())
