import copy
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from market_research.storage import canonical, sha256
from market_research.training.pipeline import fit_pipeline, fit_preprocessor, transform, predict, project_features, check_features
from market_research.training.artifacts import save_model, load_model
from market_research.training.runner import training_rows, infer, check_input, run_training, feature_inputs, validate_config
from market_research.pit import Database
from market_research.datasets.store import DatasetStore, iter_dataset_rows, training_selection, replay_dataset
from market_research.stage4.inputs import key
from stage5_fixture_builder import story

ROOT = Path(__file__).resolve().parents[1]
F = ['close_price_change_5']


def records(values): return [{F[0]: v} for v in values]


class PipelineTests(unittest.TestCase):
    def test_ridge_independent_closed_form(self):
        x = records(range(10)); y = [2*v+3 for v in range(10)]
        p = fit_pipeline(x, y, F, 'regression', 'ridge', 7)
        # Standardized population variance=1, intercept is mean(y), alpha=1.
        expected = [12 + (10/11)*2*(v-4.5) for v in (0, 4, 8)]
        np.testing.assert_allclose(predict(p, records([0,4,8])), expected, atol=1e-12)

    def test_mean_zero_frequency_hand_values(self):
        x = records(range(8))
        self.assertEqual(predict(fit_pipeline(x, [-1,1,2,3,4,5,6,8], F, 'regression','mean',1),x),[3.5]*8)
        self.assertEqual(predict(fit_pipeline(x,[3]*8,F,'regression','zero',1),x),[0]*8)
        self.assertEqual(predict(fit_pipeline(x,[0,0,0,0,0,0,1,1],F,'classification','frequency',1),x),[.3]*8)

    def test_holdout_transform_does_not_fit_statistics_or_model(self):
        p = fit_pipeline(records(range(8)),list(range(8)),F,'regression','ridge',1)
        before = canonical(p)
        predict(p,records([100000,None,-5000]))
        self.assertEqual(before,canonical(p))
        self.assertEqual(p['preprocessing']['medians'],[3.5])

    def test_missing_constant_all_missing_and_large_values(self):
        names = ['close_price_change_5','open_gap','close_to_ma_20']
        rows = [dict(zip(names,[v, None, 7])) for v in (1,2,None,4,5,6,7,8)]
        s=fit_preprocessor(rows,names,True)
        self.assertEqual(s['all_missing_columns'],['open_gap'])
        self.assertEqual(s['constant_columns'],['close_to_ma_20'])
        self.assertEqual(s['medians'][0],5)
        self.assertEqual(s['active_indices'],[0])
        self.assertTrue(np.isfinite(transform([dict(zip(names,[1e6,50,99]))],s)).all())

    def test_infinity_and_nan_are_rejected(self):
        for v in (float('inf'),float('-inf'),float('nan'),True,'2'):
            with self.assertRaises(ValueError): fit_preprocessor(records([1,v]),F,True)

    def test_exact_columns_align_order_and_reject_missing_extra(self):
        names=[F[0],'open_gap']; rows=[{F[0]:v,'open_gap':v*v} for v in range(8)]
        p=fit_pipeline(rows,list(range(8)),names,'regression','ridge',1)
        self.assertEqual(predict(p,rows),predict(p,[dict(reversed(list(r.items()))) for r in rows]))
        for r in ({F[0]:3},{F[0]:3,'open_gap':4,'price_return':.2}):
            with self.assertRaisesRegex(ValueError,'missing_or_extra'): predict(p,[r])

    def test_required_structural_fields_cannot_be_imputed(self):
        for f in ({'features':{}},{'features':{'open_gap':{'status':'unavailable','value':None,'reason':'missing_input_field'}}}):
            with self.assertRaises(ValueError): project_features(f,['open_gap'])
        self.assertEqual(project_features({'features':{'open_gap':{'status':'unavailable','value':None,'reason':'temporary_missing'}}},['open_gap']),{'open_gap':None})

    def test_allowlist_rejects_outcomes_metadata_identity_date(self):
        for n in ('price_return','up_5','label_available_at','intended_entry_at','sample_id','snapshot_id','row_number','version','security_id','decision_at','eligibility_reason'):
            with self.assertRaises(ValueError): check_features([n])

    def test_one_class_error_and_explicit_fallback(self):
        for name in ('logistic','tree'):
            with self.assertRaisesRegex(ValueError,'one_class'): fit_pipeline(records(range(8)),[1]*8,F,'classification',name,1)
            p=fit_pipeline(records(range(8)),[1]*8,F,'classification',name,1,one_class='constant')
            self.assertEqual(predict(p,records([4])),[.9]);self.assertTrue(p['notes'])

    def test_empty_small_and_no_informative_columns(self):
        for vals in ([],[1,2,3]):
            with self.assertRaisesRegex(ValueError,'insufficient'): fit_pipeline(records(vals),vals,F,'regression','ridge',1)
        with self.assertRaisesRegex(ValueError,'no_informative'): fit_pipeline(records([1]*8),[2]*8,F,'regression','ridge',1)

    def test_small_tree_and_logistic_json_predict_export(self):
        x=records(range(30)); y=[int(v>=15) for v in range(30)]
        for name in ('logistic','tree'):
            p=fit_pipeline(x,y,F,'classification',name,1)
            self.assertEqual(p['export_validation']['status'],'PASS')
            ps=predict(json.loads(canonical(p)),x)
            self.assertTrue(all(0<=v<=1 for v in ps))
            self.assertGreater(ps[-1],ps[0])

    def test_artifact_reload_mode_hash_environment_and_origin(self):
        p=fit_pipeline(records(range(8)),list(range(8)),F,'regression','ridge',1)
        with tempfile.TemporaryDirectory() as td:
            saved=save_model(td,p,{'mode':'synthetic'},ROOT,'2026-10-03T00:00:00Z')
            a=load_model(saved['path'],td,saved['model_id'],'synthetic',ROOT)
            self.assertEqual(predict(p,records([9])),predict(a['content']['pipeline'],records([9])))
            with self.assertRaisesRegex(ValueError,'promotion'):load_model(saved['path'],td,saved['model_id'],'strict',ROOT)
            with self.assertRaisesRegex(ValueError,'origin'):load_model(saved['path'],Path(td)/'else',saved['model_id'],'synthetic',ROOT)
            a['content']['pipeline']['model']['intercept']=500
            Path(saved['path']).write_text(json.dumps(a))
            with self.assertRaisesRegex(ValueError,'hash'):load_model(saved['path'],td,saved['model_id'],'synthetic',ROOT)

    def test_fit_and_inference_interfaces_cannot_read_test_labels(self):
        params=set(inspect.signature(fit_pipeline).parameters)
        self.assertNotIn('test',params);self.assertNotIn('validation',params)
        self.assertEqual(list(inspect.signature(infer).parameters),['artifact','inputs','dataset_id','dataset_hash','mode'])
        with self.assertRaises(TypeError): fit_pipeline(records(range(8)),list(range(8)),F,'regression','ridge',1,test=[1])


class TrainingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.pit=Database(cls.root/'pit.sqlite',initialize=True);cls.store=DatasetStore(cls.root/'datasets.sqlite')
        cls.base,cls.generator=story(cls.pit,cls.store,cls.root/'source',small=True)
        cls.c=cls.base|{'task':'regression','models':['zero','mean','ridge','tree']}
        cls.fixed,cls.rows,cls.checked=check_input(cls.pit,cls.store,cls.c)

    @classmethod
    def tearDownClass(cls): cls.store.close();cls.pit.close();cls.temp.cleanup()

    def test_future_corrected_versions_do_not_change_frozen_training(self):
        did=self.c['dataset_snapshot_id'];asof=self.checked['splits']['folds'][0]['training_asof']
        before=training_selection(self.store,self.pit,did,training_asof=asof,allow_research=True)
        row=next(r for r in self.rows if r['label']['status']=='ready')
        l=copy.deepcopy(row['label']);l.update(price_return=999,label_available_at='2025-12-31T23:00:00Z')
        self.store.add_label(l)
        f=copy.deepcopy(row['feature']);f['features'][self.c['features'][0]]['value']=1000;self.store.add_feature(f)
        self.assertEqual(before,training_selection(self.store,self.pit,did,training_asof=asof,allow_research=True))
        self.assertEqual(replay_dataset(self.store,self.pit,did)['content_sha256'],did)

    def test_immature_label_and_total_return_no_fallback(self):
        fold=self.checked['splits']['folds'][0]; rows=copy.deepcopy(self.rows)
        keys={key(m) for m in fold['partitions']['train']}
        for r in rows:
            if key(r) in keys:r['label']['label_available_at']='2025-12-31T00:00:00Z'
        x,y,_,ex=training_rows(rows,fold['partitions']['train'],self.c,fold['training_asof'])
        self.assertEqual(y,[]);self.assertTrue(ex)
        for r in rows:r['label']['cash_total_return_status']='blocked';r['label']['label_available_at']='2024-01-01T00:00:00Z'
        _,y,_,ex=training_rows(rows,fold['partitions']['train'],self.c|{'target_id':'cash_total_return'},fold['training_asof'])
        self.assertFalse(y);self.assertTrue(all('no_price_fallback' in e['reasons'][0] for e in ex))

    def test_changing_holdout_and_outcomes_leaves_fit_unchanged(self):
        fold=self.checked['splits']['folds'][0]
        x,y,_,_=training_rows(self.rows,fold['partitions']['train'],self.c,fold['training_asof'])
        before=fit_pipeline(x,y,self.c['features'],'regression','ridge',self.c['seed'])
        rows=copy.deepcopy(self.rows);train={key(i) for i in fold['partitions']['train']}
        for r in rows:
            if key(r) not in train:
                r['label']['price_return']=1e6
                for item in r['feature']['features'].values():item['value']=1e6
        xx,yy,_,_=training_rows(rows,fold['partitions']['train'],self.c,fold['training_asof'])
        self.assertEqual(canonical(before),canonical(fit_pipeline(xx,yy,self.c['features'],'regression','ridge',self.c['seed'])))
        self.assertTrue(all('label' not in i for i in feature_inputs(rows,fold['partitions']['test'])))

    def test_end_to_end_and_mathematical_label_path_consistency(self):
        r=run_training(self.pit,self.store,self.c,self.root/'run',ROOT)
        self.assertEqual(r['status'],'OK')
        for m in r['folds'][0]['models']:
            self.assertEqual(m['reload_predictions'],'PASS');self.assertEqual(m['stage4_replay'],'PASS')
            ps=json.loads(Path(m['partitions']['test']['predictions_path']).read_text())
            self.assertTrue(all(p['probability_up'] is None and p['dataset_snapshot_id']==self.c['dataset_snapshot_id'] for p in ps))
            self.assertTrue(all(p['model_training_completed_at']>p['decision_at'] for p in ps))
        zero=r['folds'][0]['models'][0]
        self.assertEqual(zero['simulation_summary']['fill_count'],0)
        # Frozen label endpoint values were generated by Stage3 from the same price tape.
        from market_research.stage4.tape import frozen_tape
        tape,_=frozen_tape(self.pit,self.fixed,self.rows)
        bars = {(b['symbol'], b['date']): b for b in tape['bars']}
        for row in self.rows:
            l = row['label']
            if l['status'] != 'ready': continue
            symbol = row['feature']['security_record_ids'][0]
            e = bars[symbol, l['intended_entry_at'][:10]]['open']
            exit_price = bars[symbol, l['intended_exit_at'][:10]]['open']
            self.assertAlmostEqual(l['price_return'], exit_price/e-1, places=12)

    def test_classification_stage4_and_no_regression_probability_conversion(self):
        c=self.c|{'task':'classification','models':['frequency','logistic','tree']}
        r=run_training(self.pit,self.store,c,self.root/'classification',ROOT)
        self.assertEqual(r['status'],'OK')
        for m in r['folds'][0]['models']:
            ps=json.loads(Path(m['partitions']['test']['predictions_path']).read_text())
            self.assertTrue(all(p['predicted_return'] is None and 0<=p['probability_up']<=1 for p in ps))
            metrics=m['partitions']['test']['metrics'];self.assertIsNone(metrics['metrics_row_weighted']['mse']['value'])
            self.assertEqual(sum(b['count'] for b in metrics['calibration']),metrics['count'])

    def test_strict_synthetic_refuses_and_no_candidate_selection(self):
        _,_,r=check_input(self.pit,self.store,self.c|{'mode':'strict'})
        self.assertEqual(r['status'],'BLOCKED')
        with self.assertRaisesRegex(ValueError,'model_selection'): validate_config(self.c|{'model_selection':'choose_by_test'})
