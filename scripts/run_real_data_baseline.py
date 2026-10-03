"""One fixed, personal/noncommercial EODHD demo evaluation. Raw data stay local."""
import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
from statistics import fmean

import numpy as np

from market_research.collect import collect_batch
from market_research.http import utc_now
from market_research.storage import Store, canonical, sha256, write_json
from market_research.validation.quality import calendar_rows, validate_bars
from market_research.pit import Database, import_artifacts
from market_research.pit.importer import import_calendar
from market_research.pit.time import timestamp
from market_research.datasets import build_dataset
from market_research.datasets.store import DatasetStore, dataset_metadata, training_selection
from market_research.training.runner import run_training, training_rows, feature_inputs
from market_research.training.pipeline import fit_pipeline, predict, project_features, PRESETS
from market_research.training.artifacts import load_model, environment

PROJECT = Path(__file__).resolve().parents[1]
SYMBOLS = ['AAPL', 'MSFT', 'AMZN', 'TSLA', 'MCD']
START, END = '2021-07-01', '2026-10-02'
FEATURES = ['close_price_change_5', 'close_to_ma_20', 'realized_log_price_change_volatility_20',
            'intraday_high_low_ratio', 'volume_to_prior20_mean']
SPLIT = {'kind': 'explicit', 'horizon': 5, 'min_samples': {'train': 60, 'validation': 20, 'test': 20},
         'folds': [{'train': ['2021-10-01', '2024-09-30'], 'validation': ['2024-10-01', '2025-09-30'],
                    'test': ['2025-10-01', '2026-09-25'], 'training_asof': '2024-10-01T00:00:00Z'}]}
DOCS = {
    'terms': 'https://eodhd.com/financial-apis/terms-conditions',
    'prices': 'https://eodhd.com/financial-apis/api-for-historical-data-and-volumes',
    'actions': 'https://eodhd.com/financial-apis/api-splits-dividends',
}


def checked_json(root, record):
    for path_key, hash_key in [('raw_path', 'sha256'), ('normalized_path', 'normalized_sha256')]:
        if sha256((root / record[path_key]).read_bytes()) != record[hash_key]:
            raise ValueError('collection_file_hash_mismatch')
    return json.loads((root / record['normalized_path']).read_text())


def assertion(root, name, value):
    path = root / 'evidence' / (name + '.json')
    write_json(path, value)
    return {'path': str(path), 'sha256': sha256(path.read_bytes())}


def prepare(root):
    # Fix all choices before any evaluation. Empty action responses remain preserved.
    spec = [{'source': 'eodhd', 'kind': kind, 'symbol': s, 'start': START, 'end': END, 'auth_mode': 'demo'}
            for s in SYMBOLS for kind in ('daily', 'splits', 'dividends', 'identity')]
    result_file = root / 'collection_result.json'
    if result_file.exists():
        results = json.loads(result_file.read_text())
        if [r['manifest_record']['request_parameters'] for r in results] != spec:
            raise ValueError('existing_collection_configuration_mismatch')
    else:
        results = collect_batch(spec, root / 'collection', PROJECT, canonical(spec))
        write_json(result_file, results)
    docs = collect_batch([{'source': 'official_document', 'kind': k, 'document_url': u} for k, u in DOCS.items()],
                         root / 'documents', PROJECT, canonical(DOCS))
    if any(r['manifest_record']['status'] != 'success' for r in docs):
        raise ValueError('terms_or_field_document_unavailable')
    doc_refs = [{'path': str(root / 'documents' / r['manifest_record']['raw_path']),
                 'sha256': r['manifest_record']['sha256'], 'url': DOCS[r['manifest_record']['request_parameters']['kind']]}
                for r in docs]
    sessions = calendar_rows(START, '2026-12-31')
    by_date = {s['trading_date']: s for s in sessions}
    write_json(root / 'calendar.json', sessions)
    records = {(r['manifest_record']['request_parameters']['symbol'], r['manifest_record']['request_parameters']['kind']):
               r['manifest_record'] for r in results}
    now = utc_now()
    configs, quality, source_files = [], {}, []
    for symbol in SYMBOLS:
        source = {k: records[(symbol, k)] for k in ('daily', 'splits', 'dividends', 'identity')}
        if any(v['status'] not in ('success', 'empty_response') or v['http_status'] != 200 for v in source.values()):
            raise ValueError('fixed_symbol_collection_failed:' + symbol)
        data = {k: checked_json(root / 'collection', v) for k, v in source.items()}
        identity = data['identity']['metadata']
        if identity['Code'] != symbol or identity['Type'] != 'Common Stock' or identity['CurrencyCode'] != 'USD' or not identity.get('ISIN'):
            raise ValueError('security_identity_or_currency_unconfirmed:' + symbol)
        raw_refs = [{'path': str(root / 'collection' / v['raw_path']), 'sha256': v['sha256'],
                     'kind': k, 'received_at': v['fetched_at_utc']} for k, v in source.items()]
        source_files.extend(raw_refs)
        claim = {'verification': 'verified', 'external_security_id': 'ISIN:' + identity['ISIN'],
                 'share_class': 'Common Stock', 'evidence': {'provider_identity': raw_refs[-1],
                 'method': 'matched provider General Code/ISIN/Type/USD; no historical identifier archive'}}
        bars = data['daily']['bars']
        issues = validate_bars(bars, sessions=set(by_date))
        if any(i['severity'] == 'error' for i in issues):
            raise ValueError('invalid_market_prices:' + symbol)
        expected = {d for d in by_date if START <= d <= END}
        quality[symbol] = {'daily_rows': len(bars), 'start': min(b['trading_date'] for b in bars),
                           'end': max(b['trading_date'] for b in bars), 'isin': identity['ISIN'],
                           'missing_reference_sessions': sorted(expected - {b['trading_date'] for b in bars}),
                           'issues': dict(Counter(i['code'] for i in issues)),
                           'dividends': len(data['dividends']['actions']), 'splits': len(data['splits']['actions'])}
        common = {'input_domain': 'market', 'source': 'eodhd', 'symbol': symbol, 'verified': True,
                  'verification_method': 'reviewed documented fields and hash-bound current responses; historical completeness is assumed',
                  'received_at': now, 'usable_at': now, 'published_at': None,
                  'research_assumed_available_at': START + 'T00:00:00Z',
                  'research_assumption': 'Current vendor vintage assumed usable on declared historical schedule; first versions, halts and special actions unverified',
                  'source_files': raw_refs, 'documentation': doc_refs}
        review = assertion(root, symbol + '-price', common | {'kind': 'price_contract_review',
            'security_identity': claim, 'price_semantics': 'as_traded', 'currency': 'USD',
            'session_scope': 'vendor_eod_indicative_not_official_auction',
            'volume_unit': 'shares', 'volume_adjustment_basis': 'split_adjusted_current_vintage'})
        coverage = assertion(root, symbol + '-actions', common | {'kind': 'corporate_action_coverage',
            'status': 'research_assumed_complete', 'start_date': START, 'end_date': END,
            'same_day_order': 'split_then_dividend_postsplit',
            'limitation': 'Complete vendor split/dividend responses, including empty arrays; no independent special-action completeness proof'})
        state = assertion(root, symbol + '-state', common | {'kind': 'security_trading_state',
            'listed_from': identity['IPODate'], 'verified_through': END, 'halted_dates': [], 'delisted_date': None,
            'limitation': 'Current identity and supplied session bars only; historical no-halt/no-delist continuity assumed'})
        rights = assertion(root, symbol + '-rights', {k: v for k, v in common.items()
            if k not in ('research_assumed_available_at', 'research_assumption')} | {'kind': 'learning_rights',
            'model_training_allowed': True, 'usage': 'private noncommercial one-off demo evaluation',
            'verification_method': 'User confirmed individual noncommercial research; terms allow private storage/manipulation/analysis; statistical model fitting interpreted as analysis, not a separately granted ML license',
            'redistribution_allowed': False, 'scope': 'local evaluation only; no commercial use, data/model publication, or perpetual-retention assertion'})
        # Append a new normalization, preserving original bytes and actual receipt times.
        for kind in ('daily', 'splits', 'dividends'):
            original, normalized = source[kind], data[kind]
            for row in normalized['bars'] + normalized['actions']:
                row.update(security_identity=claim, local_instrument_id='reviewed:' + claim['external_security_id'])
                if kind == 'daily':
                    row.update(observation_end_utc=by_date[row['trading_date']]['close_utc'],
                               observation_end_evidence='reference_calendar_bound_research_assumption_not_vendor_session_proof')
            normalized['provenance']['contract_review_evidence'] = review
            path = Path('normalized') / 'eodhd' / original['request_id'] / (sha256(canonical(normalized)) + '.json')
            write_json(root / 'collection' / path, normalized)
            overlay = original | {'normalized_path': str(path),
                'normalized_sha256': sha256((root / 'collection' / path).read_bytes()),
                'network_download_performed': False, 'origin_receipt_id': original['receipt_id'],
                'recorded_at_utc': utc_now(), 'processing_completed_at_utc': utc_now(), 'contract_review_evidence': review}
            overlay.pop('receipt_id', None)
            Store(root / 'collection').save_record(overlay)
        configs.append({'series': {'source': 'eodhd', 'symbol': symbol, 'price_kind': 'ohlcv',
                                   'price_semantics': 'as_traded', 'currency': 'USD'},
            'input_domain': 'market', 'feature_policy': 'research_assumed', 'label_policy': 'research_assumed',
            'identifier_requirement': 'verified', 'symbol_mode': 'provider_label', 'as_traded_mode': 'price_change_only',
            'scope_contract': {'version': '5.5.0', 'scope': 'fixed_security_research', 'source': 'eodhd',
                               'symbol': symbol, 'market_representative': False},
            'required_features': FEATURES, 'price_contract_evidence': review, 'action_coverage': coverage,
            'trading_state': state, 'learning_rights': rights, 'include_dividend_receivables': True,
            'assumption_rule': {'id': 'date_end_plus_delay', 'version': '1', 'timezone': 'America/New_York',
                'delay_hours': 0, 'allow_reference_calendar': True,
                'allow_observation_date_for_unknown_publication': True, 'bar_close_delay_seconds': 900},
            'samples': [{'decision_date': s['trading_date']} for s in sessions
                        if '2021-10-01' <= s['trading_date'] <= '2026-09-25' and date.fromisoformat(s['trading_date']).weekday() == 0]})
    label_asof = utc_now()
    with Database(root / 'pit.sqlite', initialize=True) as pit, DatasetStore(root / 'datasets.sqlite') as store:
        imported = import_artifacts(pit, root / 'collection', domain='market')
        if imported['quarantined']:
            raise ValueError('import_quarantine:' + str(imported['quarantine_reasons']))
        calendar = import_calendar(pit, root / 'calendar.json', domain='market')
        items, child_datasets = [], []
        for cfg in configs:
            cfg.update(calendar_version=calendar['calendar_version'], simulation_asof=label_asof, label_asof=label_asof)
            fixed = build_dataset(pit, store, cfg)
            _, membership = dataset_metadata(store, fixed['snapshot_id'])
            items.extend(i for i in membership if i['horizon_sessions'] == 5)
            child_datasets.append(fixed)
            print(cfg['series']['symbol'], 'dataset', fixed['feature_status_counts'], fixed['label_status_counts'], flush=True)
        fc, lc, reasons = Counter(), Counter(), Counter()
        ready = 0
        for i in items:
            f, l = store.payload('feature', i['feature_version_id']), store.payload('label', i['label_version_id'])
            fc[f['sample_status']] += 1; lc[l['status']] += 1; reasons.update(f['sample_reasons'])
            if l['reason']: reasons[l['reason']] += 1
            ready += int(f['sample_status'] == 'ready' and l['status'] == 'ready')
        fixed = store.freeze(items, {'config': {'include_dividend_receivables': True, 'series_configs': configs},
            'strictness': 'NOT_STRICT_PIT', 'feature_status_counts': dict(fc), 'label_status_counts': dict(lc),
            'reason_counts': dict(reasons), 'ready_pair_count': ready, 'learning_ready_pair_count': 0,
            'child_datasets': child_datasets, 'calendar': calendar, 'input_scope': 'five fixed demo securities; no market representation'})
    prepared = {'dataset': fixed, 'quality': quality, 'source_files': source_files + doc_refs,
                'evaluation_asof': label_asof, 'source': 'eodhd', 'symbols': SYMBOLS,
                'decision_sampling': 'Monday sessions only; chosen before evaluation', 'split': SPLIT,
                'features': FEATURES, 'presets': PRESETS, 'seed': 20261003}
    write_json(root / 'input.json', prepared)
    return prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='data/real_data_baseline')
    parser.add_argument('--personal-noncommercial', action='store_true', required=True,
                        help='Confirm the stated private individual demo evaluation purpose')
    args = parser.parse_args()
    root = (PROJECT / args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    prepared = json.loads((root / 'input.json').read_text()) if (root / 'input.json').exists() else prepare(root)
    for ref in prepared['source_files']:
        if sha256(Path(ref['path']).read_bytes()) != ref['sha256']:
            raise ValueError('source_file_hash_mismatch')
    summary = {k: prepared[k] for k in ('source', 'symbols', 'quality', 'decision_sampling', 'split', 'features', 'presets', 'seed')}
    summary.update(status='OK', strictness='NOT_STRICT_PIT', environment=environment(),
        dataset_snapshot_id=prepared['dataset']['snapshot_id'], dataset_count=prepared['dataset']['items'],
        dataset_ready_count=prepared['dataset']['selected_count'], strict_learning_ready_count=0,
        simulation={'status': 'NOT_RUN', 'reason': 'Vendor indicative opening prices are not verified auction fills; historical halt/special-action completeness assumed'},
        documentation=DOCS, models=[], reproducibility={'rtol': 1e-12, 'atol': 1e-12, 'models': []})
    received = [ref['received_at'] for ref in prepared['source_files'] if ref.get('received_at')]
    summary['collection_received_at_range'] = {'start': min(received), 'end': max(received)}
    base = {'version': '5.0.0', 'mode': 'research', 'data_scope': 'fixed_security_research',
        'dataset_snapshot_id': prepared['dataset']['snapshot_id'], 'dataset_content_sha256': prepared['dataset']['content_sha256'],
        'target_id': 'price_return', 'horizon': 5, 'features': FEATURES, 'seed': 20261003,
        'one_class_policy': 'error', 'model_selection': 'none', 'evaluation_asof': prepared['evaluation_asof'],
        'split': SPLIT, 'regression_threshold': .005, 'classification_threshold': .55,
        'simulation': {'enabled': False, 'horizon': 5, 'disabled_reason': summary['simulation']['reason']}}
    with Database(root / 'pit.sqlite') as pit, DatasetStore(root / 'datasets.sqlite') as store:
        regression_ics = {}
        for task, models in [('regression', ['zero', 'mean', 'ridge', 'tree']),
                             ('classification', ['frequency', 'logistic', 'tree'])]:
            config = base | {'task': task, 'models': models}
            write_json(root / (task + '.json'), config)
            result = run_training(pit, store, config, root / task, PROJECT)
            if result['status'] != 'OK':
                raise ValueError('training_not_completed:' + task + ':' + result['status'])
            # run_training already verified the frozen sources and split. Read its
            # immutable payloads for the one repeated fit, without repeating the audit.
            _, membership = dataset_metadata(store, prepared['dataset']['snapshot_id'])
            rows = [i | {'feature': store.payload('feature', i['feature_version_id']),
                         'label': store.payload('label', i['label_version_id'])} for i in membership]
            fold = result['input_check']['splits']['folds'][0]
            x, y, _, excluded = training_rows(rows, fold['partitions']['train'], config, fold['training_asof'])
            if excluded: raise ValueError('unexpected_post_split_training_exclusions')
            summary['partition_counts'] = fold['counts']
            summary['split_excluded_count'] = len(fold['excluded'])
            summary['split_exclusion_counts'] = fold['exclusion_counts']
            summary['dataset_exclusion_counts'] = prepared['dataset']['reason_counts']
            summary['decision_dates'] = {p: {'start': min(r['feature']['decision_at'][:10] for r in rows if r['sample_id'] in {i['sample_id'] for i in fold['partitions'][p]}),
                'end': max(r['feature']['decision_at'][:10] for r in rows if r['sample_id'] in {i['sample_id'] for i in fold['partitions'][p]})} for p in fold['partitions']}
            summary['test_entry_exit_range'] = {k: fn(r['label'][k] for r in rows if r['sample_id'] in {i['sample_id'] for i in fold['partitions']['test']})
                for k, fn in [('intended_entry_at', min), ('intended_exit_at', max)]}
            common_ids = None
            for model in result['folds'][0]['models']:
                model_id = model['artifact']['model_id']
                artifact = load_model(model['artifact']['path'], root / task / 'models', model_id, 'research', PROJECT)
                # One repeated fit of fixed settings is enough to check determinism.
                repeated = fit_pipeline(x, y, FEATURES, task, model['model_name'], config['seed'], minimum=60)
                test_inputs = feature_inputs(rows, fold['partitions']['test'])
                projected = [project_features(r['feature'], FEATURES) for r in test_inputs]
                np.testing.assert_allclose(predict(repeated, projected), predict(artifact['content']['pipeline'], projected), rtol=1e-12, atol=1e-12)
                ids = {p['sample_id'] for p in json.loads(Path(model['partitions']['test']['predictions_path']).read_text())}
                if common_ids is not None and ids != common_ids: raise ValueError('model_evaluation_membership_differs')
                common_ids = ids
                summary['reproducibility']['models'].append({'task': task, 'model': model['model_name'],
                    'reload': model['reload_predictions'], 'repeated_fit': 'PASS',
                    'pipeline_hash_equal': sha256(canonical(repeated)) == artifact['content']['pipeline_sha256']})
                metric = model['partitions']['test']['metrics']
                if task == 'regression':
                    regression_ics[model['model_name']] = {d: v['rank_ic']['value'] for d, v in metric['daily'].items()
                                                          if v['rank_ic']['value'] is not None}
                if metric['count'] != fold['counts']['test'] or metric['exclusion_counts']:
                    raise ValueError('incomplete_common_test_evaluation')
                summary['models'].append({'task': task, 'model': model['model_name'], 'model_id': model_id,
                    'test_count': metric['count'], 'evaluation_exclusion_counts': metric['exclusion_counts'],
                    'metrics': metric['metrics_row_weighted'], 'rank_ic': metric['rank_ic_equal_date_mean'],
                    'rank_ic_defined_dates': sum(v['rank_ic']['value'] is not None for v in metric['daily'].values()),
                    'validation_count': model['partitions']['validation']['metrics']['count']})
                print(task, model['model_name'], metric['count'], metric['metrics_row_weighted'], flush=True)
        summary['strict_selection'] = training_selection(
            store, pit, prepared['dataset']['snapshot_id'], training_asof=prepared['evaluation_asof'])['selected_count']
        common_ic_dates = sorted(set(regression_ics['ridge']) & set(regression_ics['tree']))
        summary['rank_ic_comparison'] = {'common_dates': len(common_ic_dates),
            'equal_date_mean': {name: fmean(regression_ics[name][d] for d in common_ic_dates) if common_ic_dates else None
                                for name in ('ridge', 'tree')}, 'constant_baselines': 'undefined'}
    write_json(PROJECT / 'reports' / 'real_data_baseline.json', summary)
    print('Completed: reports/real_data_baseline.json', flush=True)


if __name__ == '__main__':
    main()
