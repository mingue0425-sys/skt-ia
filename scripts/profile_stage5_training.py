"""Measure fresh train-only fitting; compare with saved pipelines without resimulating."""
import json
from pathlib import Path
import numpy as np
from market_research.pit import Database
from market_research.datasets.store import DatasetStore
from market_research.storage import canonical, sha256, write_json
from market_research.http import utc_now
from market_research.training.runner import check_input, training_rows
from market_research.training.pipeline import fit_pipeline, predict, project_features
from market_research.training.artifacts import load_model, environment
from market_research.stage4.inputs import key

ROOT=Path(__file__).resolve().parents[1]


def main():
    checks=[]
    for name in ('signal','null'):
        root=ROOT/'tests/generated/stage5'/name
        before={str(p):sha256(p.read_bytes()) for p in (root/'pit.sqlite',root/'datasets.sqlite')}
        with Database(root/'pit.sqlite') as pit, DatasetStore(root/'datasets.sqlite') as store:
            base=json.loads((root/'regression.json').read_text())
            _,rows,checked=check_input(pit,store,base)
            for task in ('regression','classification'):
                c=json.loads((root/(task+'.json')).read_text())
                old=json.loads((root/(task+'-release')/'result.json').read_text())
                for fold,old_fold in zip(checked['splits']['folds'],old['folds'],strict=True):
                    x,y,members,excluded=training_rows(rows,fold['partitions']['train'],c,fold['training_asof'])
                    if excluded or members!=old_fold['training_membership']:raise AssertionError('retraining_selection_mismatch')
                    eval_keys={key(i) for i in fold['partitions']['test']}
                    holdout=[project_features(r['feature'],c['features']) for r in rows if key(r) in eval_keys]
                    for model in old_fold['models']:
                        saved=model['artifact']
                        a=load_model(saved['path'],Path(saved['path']).parent,saved['model_id'],'synthetic',ROOT)
                        pipeline=fit_pipeline(x,y,c['features'],task,model['model_name'],c['seed'],one_class=c['one_class_policy'],minimum=max(8,c['split']['min_samples']['train']))
                        predicted=predict(pipeline,holdout);fixed=predict(a['content']['pipeline'],holdout)
                        if not np.allclose(predicted,fixed,rtol=1e-12,atol=1e-12):raise AssertionError('retraining_numeric_prediction_mismatch')
                        checks.append({'dataset':name,'task':task,'fold_id':fold['fold_id'],'model':model['model_name'],
                                       'model_id':saved['model_id'],'pipeline_byte_hash_equal':sha256(canonical(pipeline))==saved['pipeline_sha256'],
                                       'prediction_tolerance':'rtol=atol=1e-12','max_absolute_error':float(np.max(np.abs(np.array(predicted)-fixed)))})
        if any(sha256(Path(p).read_bytes())!=h for p,h in before.items()):raise AssertionError('profile_changed_fixed_database')
    report={'status':'PASS','executed_at':utc_now(),'environment':environment(),'count':len(checks),'checks':checks,
            'pipeline_hash_equal_count':sum(c['pipeline_byte_hash_equal'] for c in checks),
            'scope':'Fresh train-only synthetic fits in current pinned Linux environment; fixed-model holdout prediction tolerance checked; cross-platform byte-identical retraining is not promised; no new model artifact or actual-market model'}
    write_json(ROOT/'data/stage5/release/retraining-profile.json',report)
    print(json.dumps({'status':'PASS','models':len(checks),'pipeline_hash_equal_count':report['pipeline_hash_equal_count']}))


if __name__=='__main__':main()
