"""Fictional HTTP bodies/evidence in temporary roots; no market access or models."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from market_research.storage import canonical,sha256,write_json
from market_research.http import Response
from market_research.pit import Database,query,replay_snapshot
from market_research.datasets.store import DatasetStore,training_selection,iter_dataset_rows
from market_research.datasets.calendar import SessionCalendar
from market_research.observed.operation import cycle,check_config,MINIMUM
from market_research.observed.diagnostics import pending_assessment
from market_research.observed.scope import learning_reasons,validate_scope
from market_research.validation.quality import calendar_rows


class Client:
    def __init__(self,body,at,status=200):self.body=body;self.at=at;self.status=status;self.calls=0
    def get(self,*args,**kwargs):
        self.calls+=1
        return Response(self.status,self.body,fetched_at_utc=self.at,attempts=[{'status':self.status}])


class ObservedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.c=json.loads(Path('configs/observed_ibm_demo.json').read_text())
        self.at='2026-10-02T20:20:00Z'
        self.body=self.prices('2026-10-02')
    def tearDown(self):self.temp.cleanup()
    def prices(self,end):
        rows=[r for r in calendar_rows('2026-01-01',end)][-100:]
        return canonical({'Meta Data':{'2. Symbol':'IBM','3. Last Refreshed':end,'5. Time Zone':'US/Eastern'},
            'Time Series (Daily)':{r['trading_date']:{'1. open':str(100+i),'2. high':str(102+i),'3. low':str(99+i),'4. close':str(101+i),'5. volume':'10000'} for i,r in enumerate(rows)}})
    def run_cycle(self,c=None,client=None,at=None,**kw):
        at=at or self.at
        with patch('market_research.collect.utc_now',return_value=at),patch('market_research.pit.importer.utc_now',return_value=at),patch('market_research.observed.review.utc_now',return_value=at):
            return cycle(c or self.c,self.root,'.',clock=lambda:at,client=client or Client(self.body,at),**kw)
    def ref(self,kind,**kw):
        value={'kind':kind,'input_domain':'market','verified':True,'verification_method':'FICTIONAL_UNIT_TEST_ONLY_NO_ACTUAL_MARKET_EVIDENCE','source':'alpha_vantage','symbol':'IBM','received_at':'2026-10-02T20:00:00Z','usable_at':'2026-10-02T20:01:00Z'}|kw
        path=self.root/(kind+'-'+sha256(canonical(value))+'.json');write_json(path,value)
        return {'path':str(path),'sha256':sha256(path.read_bytes())}
    def reviewed(self,body=None):
        c=copy.deepcopy(self.c)
        c['price_contract_evidence']=self.ref('price_contract_review',price_semantics='as_traded',currency='USD',session_scope='verified_regular_session',volume_unit='shares',volume_adjustment_basis='as_traded',start_date='2026-01-01',end_date='2027-12-31',version_raw_sha256s=[sha256(body or self.body)],security_identity={'verification':'verified','external_security_id':'TEST_ONLY_SHARE_ID','evidence':'FICTIONAL_TEST_ONLY'})
        c['learning_rights']=self.ref('learning_rights',model_training_allowed=True)
        c['trading_state']=self.ref('security_trading_state',listed_from='2000-01-01',verified_through='2027-12-31',halted_dates=[],delisted_date=None)
        c['action_coverage']=self.ref('corporate_action_coverage',start_date='2026-01-01',end_date='2027-12-31',status='verified_complete',same_day_order='split_then_dividend_postsplit')
        return c
    def test_current_receipt_is_current_lookback_not_backdated(self):
        r=self.run_cycle();self.assertEqual(r['status'],'SAMPLE_PREPARED');self.assertEqual(r['sample']['label_status_counts'],{'pending':3})
        self.assertFalse(r['sample']['scoped_input_eligible']);self.assertEqual(r['training_readiness']['scoped_eligible_pairs'],0)
        with Database(self.root/'pit.sqlite') as pit:
            cal=r['calendar']['version']
            old=query(pit,cutoff='2026-09-01T21:00:00Z',policy='observed',symbols=['IBM'],sources=['alpha_vantage'],identifier_requirement='temporary_allowed',symbol_mode='provider_label',calendar_version=cal)
            self.assertEqual(old['selected_count'],0);self.assertIn('received_after_cutoff',old['exclusion_counts'])
        self.assertEqual(r['lookback_diagnostic']['input_rows'],61)
        self.assertEqual(r['prediction_status'],'prediction_not_available')
    def test_cutoff_after_receipt_but_before_processing_excludes(self):
        c=copy.deepcopy(self.c);c.update(cutoff_policy='explicit',cutoff='2026-10-02T20:15:00Z')
        r=self.run_cycle(c,Client(self.body,'2026-10-02T20:10:00Z'))
        self.assertEqual(r['status'],'NO_ELIGIBLE_INPUTS');self.assertIn('processing_after_cutoff',r['lookback_diagnostic']['query_exclusion_counts'])
    def test_receipt_after_cutoff_excluded(self):
        c=copy.deepcopy(self.c);c.update(cutoff_policy='explicit',cutoff='2026-10-02T20:10:00Z')
        r=self.run_cycle(c);self.assertEqual(r['status'],'NO_ELIGIBLE_INPUTS');self.assertEqual(r['sample'],None)
        self.assertIn('received_after_cutoff',r['lookback_diagnostic']['query_exclusion_counts'])
    def test_missed_decision_does_not_create_sample(self):
        c=copy.deepcopy(self.c);c['decision_date']='2026-10-02'
        r=self.run_cycle(c,at='2026-10-03T05:00:00Z');self.assertEqual(r['status'],'MISSED_DECISION');self.assertIsNone(r['sample'])
    def test_weekend_and_holiday_no_fictional_session(self):
        for at in ['2026-10-03T20:20:00Z','2026-12-25T20:20:00Z']:
            r=self.run_cycle(at=at);self.assertEqual(r['status'],'NON_TRADING_DAY');self.assertIsNone(r['sample'])
    def test_same_cutoff_idempotency_and_hash_integrity(self):
        client=Client(self.body,self.at);first=self.run_cycle(client=client)
        before={str(p):sha256(p.read_bytes()) for p in self.root.rglob('*') if p.is_file()}
        again=self.run_cycle(client=client,refresh=True)
        after={str(p):sha256(p.read_bytes()) for p in self.root.rglob('*') if p.is_file()}
        self.assertTrue(again['idempotent_replay']);self.assertEqual(client.calls,1);self.assertEqual(before,after)
        self.assertEqual(first['sample'],again['sample'])
    def test_failure_after_collection_resumes_without_second_request(self):
        client=Client(self.body,self.at)
        with self.assertRaisesRegex(RuntimeError,'injected_failure'):self.run_cycle(client=client,fail_after='collection')
        raw={str(p):sha256(p.read_bytes()) for p in (self.root/'collection/raw').rglob('*') if p.is_file()}
        import sqlite3
        with patch('market_research.observed.operation.import_artifacts',side_effect=sqlite3.OperationalError('TEST_ONLY failure')):
            with self.assertRaises(sqlite3.OperationalError):self.run_cycle(client=client,refresh=True)
        journals=[json.loads(p.read_text()) for p in (self.root/'executions').glob('*.json')]
        self.assertEqual(journals[0]['last_failure']['category'],'OperationalError')
        r=self.run_cycle(client=client,refresh=True);self.assertEqual(client.calls,1);self.assertEqual(r['status'],'SAMPLE_PREPARED')
        self.assertTrue(all(sha256(Path(p).read_bytes())==h for p,h in raw.items()))
    def test_failed_http_can_retry_without_erasing_failure(self):
        bad=Client(b'not found',self.at,404);r=self.run_cycle(client=bad)
        self.assertEqual(r['status'],'COLLECTION_FAILED')
        r=self.run_cycle();self.assertEqual(r['status'],'SAMPLE_PREPARED')
        statuses=[json.loads(p.read_text())['status'] for p in (self.root/'collection/manifest').glob('*.json')]
        self.assertIn('not_found',statuses);self.assertIn('success',statuses)
    def test_pending_vs_missing_past_at_two_evaluation_clocks(self):
        r=self.run_cycle()
        with Database(self.root/'pit.sqlite') as pit:
            cal=SessionCalendar(pit,r['calendar']['version'])
            l={'intended_exit_at':cal.by_date['2026-10-06']['open_utc']}
            self.assertEqual(pending_assessment(l,cal,self.at),'future_exit_session')
            self.assertEqual(pending_assessment(l,cal,'2026-10-07T12:00:00Z'),'within_normal_delivery_delay')
            self.assertEqual(pending_assessment(l,cal,'2026-10-12T12:00:00Z'),'past_exit_requires_refresh_or_missing_outcome_assessment')
    def test_maturity_appends_label_keeps_feature_and_old_snapshot(self):
        c=self.reviewed();r=self.run_cycle(c);sid=r['sample']['dataset_snapshot_id'];fid=r['sample']['feature_version_id']
        with Database(self.root/'pit.sqlite') as pit,DatasetStore(self.root/'datasets.sqlite') as store:
            original=list(iter_dataset_rows(store,pit,sid));old20=next(x for x in original if x['horizon_sessions']==20)['label_version_id']
            self.assertTrue(r['sample']['scoped_input_eligible'])
        later=self.prices('2026-10-13');c2=self.reviewed(later)

        r2=self.run_cycle(c2,client=Client(later,'2026-10-13T20:20:00Z'),at='2026-10-13T20:20:00Z',refresh=True)
        update=next(x for x in r2['mature_label_updates'] if x['sample_id']==r['sample']['sample_id'])
        with Database(self.root/'pit.sqlite') as pit,DatasetStore(self.root/'datasets.sqlite') as store:
            current=list(iter_dataset_rows(store,pit,update['snapshot_id']))
            self.assertEqual(original,list(iter_dataset_rows(store,pit,sid)))
            self.assertEqual({x['feature_version_id'] for x in current},{fid})
            self.assertEqual(next(x for x in current if x['horizon_sessions']==20)['label_version_id'],old20)
            h5=next(x for x in current if x['horizon_sessions']==5)
            self.assertEqual(h5['label']['status'],'ready')
            self.assertEqual(training_selection(store,pit,update['snapshot_id'],training_asof='2026-10-13T20:20:00Z')['selected_count'],0)
            scoped=training_selection(store,pit,update['snapshot_id'],training_asof='2026-10-13T20:20:00Z',scope='forward_observed')
            self.assertEqual(scoped['selected_count'],2)
            # Open e=Oct5, exit=e+5 Oct12; validate directly from received raw.
            prices=json.loads(later)['Time Series (Daily)']
            expected=float(prices['2026-10-12']['1. open'])/float(prices['2026-10-05']['1. open'])-1
            self.assertAlmostEqual(h5['label']['price_return'],expected,places=12)
        self.assertEqual(r2['training_readiness']['status'],'BLOCKED');self.assertEqual(r2['models_trained'],0)
    def test_scope_limits_not_market_universe_and_rights_stay_required(self):
        c=self.reviewed();r=self.run_cycle(c)
        with Database(self.root/'pit.sqlite') as pit,DatasetStore(self.root/'datasets.sqlite') as store:
            row=next(iter_dataset_rows(store,pit,r['sample']['dataset_snapshot_id']))
            f=copy.deepcopy(row['feature']);l=copy.deepcopy(row['label'])
            l.update(status='ready',label_available_at='2026-10-13T00:00:00Z')
            self.assertFalse(learning_reasons(f,l,'forward_observed','2026-10-13T20:00:00Z'))
            from market_research.training.runner import training_rows
            training_row=copy.deepcopy(row)
            training_row['label'].update(status='ready',price_return=0.02,label_available_at='2026-10-13T00:00:00Z')
            run={'mode':'research','data_scope':'forward_observed','task':'regression','target_id':'price_return','features':list(f['required_features'])}
            membership={k:row[k] for k in ('sample_id','horizon_sessions','feature_version_id','label_version_id')}
            _,target,_,excluded=training_rows([training_row],[membership],run,'2026-10-13T20:00:00Z')
            self.assertEqual(target,[0.02]);self.assertEqual(excluded,[])
            run['features']=['close_price_change_5']
            _,target,_,excluded=training_rows([training_row],[membership],run,'2026-10-13T20:00:00Z')
            self.assertEqual(target,[]);self.assertIn('scoped_model_feature_list_mismatch',excluded[0]['reasons'])
            self.assertIn('legacy_strict_pit_or_rights_unmet',learning_reasons(f,l,'strict_history','2026-10-13T20:00:00Z'))
            f['scope_contract']['scope']='fixed_security_research'
            l['scope_contract']=copy.deepcopy(f['scope_contract'])
            self.assertFalse(learning_reasons(f,l,'fixed_security_research','2026-10-13T20:00:00Z'))
            l['evidence_dependencies']=[]
            self.assertIn('learning_rights_unconfirmed',learning_reasons(f,l,'fixed_security_research','2026-10-13T20:00:00Z'))
            f['input_domain']='synthetic'
            self.assertIn('synthetic_not_market_training',learning_reasons(f,l,'forward_observed','2026-10-13T20:00:00Z'))
    def test_permission_structural_field_and_review_not_automatic(self):
        c=copy.deepcopy(self.c);c.update(access_scope='account_operation');c['request']['auth_mode']='environment'
        with self.assertRaisesRegex(ValueError,'collection_and_storage'):check_config(c,self.at)
        c=copy.deepcopy(self.c);c['required_fields']=['close']
        with self.assertRaisesRegex(ValueError,'fixed_fields'):check_config(c,self.at)
        c=self.reviewed(b'wrong vintage')
        with self.assertRaisesRegex(ValueError,'review_downloaded_version'):self.run_cycle(c)
        c=self.reviewed();value=json.loads(Path(c['price_contract_evidence']['path']).read_text())
        value['security_identity']['external_security_id']='temporary:alpha_vantage:IBM'
        path=self.root/'bad-identity-review.json';write_json(path,value)
        c['price_contract_evidence']={'path':str(path),'sha256':sha256(path.read_bytes())}
        with self.assertRaisesRegex(ValueError,'temporary_or_issuer_id'):self.run_cycle(c)

    def test_minimum_is_fixed_and_synthetic_model_not_activated(self):
        c=copy.deepcopy(self.c);c['minimum_training']['samples']=1
        with self.assertRaisesRegex(ValueError,'minimum'):check_config(c,self.at)
        r=self.run_cycle();self.assertEqual(r['models_trained'],0);self.assertEqual(r['prediction_status'],'prediction_not_available')
        self.assertFalse(r['orders_enabled']);self.assertFalse(r['schedules_enabled'])
        from market_research.training.runner import validate_config
        from market_research.training import VERSION as TRAINING_VERSION
        run={'version':TRAINING_VERSION,'mode':'research','data_scope':'forward_observed','task':'regression','target_id':'price_return','horizon':5,'split':{'horizon':5,'min_samples':{'train':8,'validation':1,'test':1}}}
        with self.assertRaisesRegex(ValueError,'scoped_training_partition_minimum'):validate_config(run)
    def test_snapshot_preservation_on_later_source_correction(self):
        r=self.run_cycle();sid=r['lookback_diagnostic']['snapshot_id']
        with Database(self.root/'pit.sqlite') as pit:before=replay_snapshot(pit,sid)
        c=copy.deepcopy(self.c);c['cutoff_policy']='explicit';c['cutoff']='2026-10-02T20:21:00Z'
        body=json.loads(self.body);body['Time Series (Daily)']['2026-10-02']['4. close']='202'
        # coherent OHLC correction
        body['Time Series (Daily)']['2026-10-02']['2. high']='205'
        self.run_cycle(c,client=Client(canonical(body),'2026-10-02T20:21:00Z'),at='2026-10-02T20:21:00Z',refresh=True)
        with Database(self.root/'pit.sqlite') as pit:self.assertEqual(before,replay_snapshot(pit,sid))

    def test_diagnostics_count_samples_and_versions_separately(self):
        from market_research.observed.diagnostics import diagnose
        r=self.run_cycle()
        with Database(self.root/'pit.sqlite') as pit, DatasetStore(self.root/'datasets.sqlite') as store:
            report=diagnose(pit,store,'2026-10-03T12:00:00Z')
        self.assertEqual(report['sample_count'],1)
        self.assertEqual(report['snapshot_count'],1)
        self.assertEqual(report['distinct_sample_horizon_count'],3)
        self.assertEqual(report['referenced_label_version_count'],3)
        self.assertEqual(report['labels_by_horizon'],{'1':1,'5':1,'20':1})
        self.assertEqual(report['strict_eligible_distinct_sample_horizon_count'],0)
        self.assertTrue(all(len(row['original_endpoint_evidence'])==2 for row in report['rows']))

    def test_missing_raw_open_is_not_median_imputed(self):
        body=json.loads(self.body)
        body['Time Series (Daily)']['2026-10-02'].pop('1. open')
        r=self.run_cycle(client=Client(canonical(body),self.at))
        self.assertEqual(r['status'],'COLLECTION_FAILED')
        self.assertTrue(list((self.root/'collection/raw').rglob('*.bin')))
        self.assertFalse((self.root/'samples').exists())
