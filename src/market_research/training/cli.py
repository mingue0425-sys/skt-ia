"""Stage5 flat commands; Stage4 prediction-evaluate/simulation-run remain reusable."""
import json
import sqlite3
from pathlib import Path
from ..pit import Database
from ..datasets.store import DatasetStore
from ..storage import write_json, sha256, canonical
from ..stage4.inputs import load_fixed
from ..stage4.splits import make_splits
from ..stage4.cli import exit_code
from ..stage4.predictions import validate_predictions
from .runner import check_input, run_training, infer, feature_inputs
from .artifacts import load_model

COMMANDS = ('training-config-check', 'training-run', 'model-predict', 'training-replay', 'stage5-report')


def options(parser):
    parser.add_argument('--training-root', default='data/stage5/run')
    parser.add_argument('--model-root')
    parser.add_argument('--model-file')
    parser.add_argument('--model-id')
    parser.add_argument('--training-result')
    parser.add_argument('--partition', choices=('validation', 'test'), default='test')
    parser.add_argument('--training-fold', type=int, default=0)


def run(args):
    try:
        if not Path(args.db).is_file() or not Path(args.dataset_db).is_file(): raise ValueError('fixed_input_database_missing')
        with Database(args.db) as pit, DatasetStore(args.dataset_db) as store:
            if args.command in ('training-config-check', 'training-run'):
                c = json.loads(Path(args.config).read_text())
                if not args.mode or c['mode'] != args.mode: raise ValueError('explicit_cli_config_mode_match_required')
                result = check_input(pit, store, c)[2] if args.command == 'training-config-check' else run_training(pit, store, c, args.training_root, args.project_root)
            elif args.command == 'model-predict':
                if not all((args.model_file, args.model_root, args.model_id, args.dataset_id, args.dataset_hash, args.mode)): raise ValueError('model_origin_id_dataset_hash_mode_required')
                a = load_model(args.model_file, args.model_root, args.model_id, args.mode, args.project_root)
                _, rows = load_fixed(store, pit, args.dataset_id, args.dataset_hash, args.mode)
                prov = a['content']['provenance']
                splits = make_splits(store, pit, args.dataset_id, rows, prov['split_contract'], args.mode)
                f = splits['folds'][int(prov['fold_id'])]
                preds, rejects = infer(a, feature_inputs(rows, f['partitions'][args.partition]), args.dataset_id, args.dataset_hash, args.mode)
                checked = validate_predictions(preds, rows, args.dataset_id, args.dataset_hash, args.mode)
                result = {'status': 'PARTIAL' if rejects else 'OK', 'predictions': preds, 'excluded': rejects, 'validation': checked['status']}
                if checked['status'] == 'INVALID': result['status'] = 'INVALID'
                if args.output: write_json(args.output, preds)
            else:
                if not args.training_result: raise ValueError('training_result_required')
                original = json.loads(Path(args.training_result).read_text())
                digest = original.pop('result_content_sha256')
                if sha256(canonical(original)) != digest: raise ValueError('training_result_hash_mismatch')
                c = original['config']
                if args.mode != c['mode']: raise ValueError('explicit_cli_result_mode_match_required')
                fixed, rows, checked = check_input(pit, store, c)
                if args.command == 'stage5-report':
                    result = {'status': original['status'], 'mode': c['mode'], 'dataset_snapshot_id': fixed['snapshot_id'],
                              'folds': [{'fold_id': f['fold_id'], 'status': f['status'], 'training_count': f.get('training_count', 0),
                                         'models': [{'name': m['model_name'], 'status': m['status'], 'model_id': m.get('artifact', {}).get('model_id'),
                                                     'reload': m.get('reload_predictions'), 'stage4_replay': m.get('stage4_replay')} for m in f['models']]} for f in original['folds']],
                              'real_predictive_power': 'NOT_EVALUATED'}
                else:
                    counts = 0
                    from ..stage4.runs import replay_run
                    for f in original['folds']:
                        fold = checked['splits']['folds'][int(f['fold_id'])]
                        for m in f['models']:
                            if 'artifact' not in m: continue
                            saved = m['artifact']
                            a = load_model(saved['path'], Path(saved['path']).parent, saved['model_id'], c['mode'], args.project_root)
                            if a['content']['provenance']['run_config'] != c: raise ValueError('model_run_config_binding_mismatch')
                            for part, info in m['partitions'].items():
                                old = json.loads(Path(info['predictions_path']).read_text())
                                if sha256(canonical(old)) != info['predictions_sha256']: raise ValueError('stored_prediction_hash_mismatch')
                                new, rejects = infer(a, feature_inputs(rows, fold['partitions'][part]), c['dataset_snapshot_id'], c['dataset_content_sha256'], c['mode'])
                                def values(ps): return [{k: v for k, v in p.items() if k != 'generated_at'} for p in ps]
                                if rejects or values(old) != values(new): raise ValueError('fixed_model_prediction_replay_mismatch')
                            if replay_run(m['stage4']['path'], pit, store, args.project_root)['status'] != 'PASS': raise ValueError('stage4_replay_failed')
                            counts += 1
                    result = {'status': 'PASS' if counts else 'BLOCKED', 'models_verified': counts, 'scope': 'fixed saved models and stored predictions; not byte-identical retraining'}
        if args.output and args.command != 'model-predict': write_json(args.output, result)
        print(json.dumps({'status': result['status'], 'mode': args.mode, 'models_verified': result.get('models_verified'), 'reason': result.get('reason'), 'output': args.output}, ensure_ascii=False))
        return exit_code(result)
    except (ValueError, KeyError, IndexError, TypeError, OSError, sqlite3.Error) as exc:
        result = {'status': 'ERROR', 'mode': args.mode, 'reason': str(exc)}
        if args.output: write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False)); return 1
