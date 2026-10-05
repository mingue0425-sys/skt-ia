"""Small forward-path fixtures in temporary roots; no HTTP, fitting or real forecasts."""
import argparse
from collections import Counter
import copy
import importlib.util
import io
import json
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

from market_research.http import Response
from market_research.storage import canonical, sha256, write_json
from market_research.pit import Database
from market_research.datasets.calendar import SessionCalendar
from market_research.datasets.store import DatasetStore
from market_research.training.artifacts import save_model

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('forward', ROOT/'scripts/run_forward_evaluation.py')
forward = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forward)


class FictionalClient:
    def __init__(self, config, at, receipt_at=None, fail_symbol=None, correction=False, missing_latest=False):
        self.config, self.at = config, at
        self.receipt_at = receipt_at or at
        self.fail_symbol = fail_symbol
        self.correction = correction
        self.missing_latest = missing_latest
        self.calls = 0
        self.calls_by_kind = Counter()

    def get(self, url, **kwargs):
        self.calls += 1
        u = urlsplit(url); kind, security = u.path.split('/')[-2:]
        self.calls_by_kind[kind] += 1
        symbol = security.removesuffix('.US')
        if symbol == self.fail_symbol:
            return Response(503, b'', fetched_at_utc=self.receipt_at)
        if kind == 'fundamentals':
            data = {'Code': symbol, 'ISIN': self.config['identities'][symbol]['external_security_id'][5:],
                    'Type': 'Common Stock', 'CurrencyCode': 'USD'}
        elif kind == 'eod':
            end = parse_qs(u.query)['to'][0]
            sessions = json.loads(Path(self.config['calendar_path']).read_text())
            data = [{'date': r['trading_date'], 'open': 100+i, 'high': 103+i, 'low': 99+i,
                     'close': 101+i, 'adjusted_close': 101+i, 'volume': 10000+i}
                    for i,r in enumerate(sessions) if self.config['lookback_start'] <= r['trading_date'] <= end]
            if self.missing_latest: data = [r for r in data if r['date'] != end]
            if self.correction:
                next(r for r in data if r['date']=='2026-10-13')['open'] += 2
        else: data = []  # Fictional complete no-action interval is asserted separately.
        return Response(200, canonical(data), fetched_at_utc=self.receipt_at)


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.c = json.loads((ROOT/'configs/forward_evaluation.json').read_text())
        # Hand-authored SYNTHETIC_TEST_ONLY calendar and JSON models. No fitting,
        # vendor files, original data/ paths, or operating-model copies are used.
        sessions = []
        day = date(2026, 7, 1)
        while day <= date(2026, 10, 20):
            if day.weekday() < 5 and day.isoformat() not in ('2026-07-03', '2026-09-07'):
                sessions.append({'trading_date':day.isoformat(), 'open_utc':day.isoformat()+'T13:30:00Z',
                    'close_utc':day.isoformat()+'T20:00:00Z', 'exchange':'XNYS','timezone':'America/New_York',
                    'source':'SYNTHETIC_TEST_ONLY','publication_time_utc':None})
            day += timedelta(days=1)
        calendar = self.root/'calendar.json'; write_json(calendar, sessions)
        (self.root/'pyproject.toml').write_text('[project]\nname="synthetic-forward-fixture"\nversion="0"\n')
        membership = [{'sample_id':'synthetic-'+str(i)} for i in range(64)]
        self.c.update(training_count=64, training_membership_sha256=sha256(canonical(membership)),
                      baseline_probability=.5, dataset_snapshot_id='SYNTHETIC_TEST_ONLY')
        self.c.update(output_root=str(self.root/'forward'), seed_collection=str(self.root/'seed'),
                      calendar_path=str(calendar), calendar_sha256=sha256(calendar.read_bytes()),
                      label_evidence_index=str(self.root/'label_evidence.json'),
                      limitation='FICTIONAL_INPUT_UNIT_TEST_ONLY; temporary root excluded from real forward evaluation')
        self.c['identities'] = {s:{'external_security_id':'ISIN:FICTIONAL-'+s,'share_class':'Common Stock',
            'verification':'verified','evidence':{'method':'SYNTHETIC_TEST_ONLY'}} for s in self.c['symbols']}
        state = {'features':self.c['features'], 'fit_count':64, 'active_indices':list(range(5)),
                 'medians':[0.]*5, 'means':[0.]*5, 'scales':[1.]*5, 'scaled':False, 'clipping':None}
        self.provenance = {k:self.c[k] for k in ('target_id','horizon','target_contract','training_asof',
            'seed','training_count','training_membership_sha256','dataset_snapshot_id')}
        self.provenance.update(mode='research', input_domain='synthetic', task='classification',
            fixture_kind='SYNTHETIC_TEST_ONLY', training_membership=membership)
        # research matches the wrapper's serialization contract. This fixture's
        # config pin is substituted only inside tests, never in the production CLI.
        self.c['models'] = []
        for name, model, settings in [
            ('frequency', {'kind':'constant','value':.5}, {'alpha':1.,'formula':'(n_up+alpha)/(n+2*alpha)'}),
            ('tree', {'kind':'tree','left':[1,-1,-1],'right':[2,-1,-1],'feature':[0,-2,-2],
                      'threshold':[.01,-2.,-2.],'values':[.5,.4,.6]}, {'max_depth':3,'min_samples_leaf':32})]:
            pipeline = {'task':'classification','model_name':name,'settings':settings,
                        'seed':self.c['seed'],'notes':['SYNTHETIC_TEST_ONLY'],
                        'preprocessing':copy.deepcopy(state),'model':model}
            self.c['models'].append(self.save_fixture_model(pipeline, self.provenance))
        self.fixed = copy.deepcopy(self.c)
        write_json(self.root/'config.json',self.c)
        for patcher in (patch.object(forward,'PROJECT',self.root),
                        patch.object(forward,'CONFIG_SHA256',sha256(canonical(self.c)))):
            patcher.start(); self.addCleanup(patcher.stop)
        forward.load_frozen(self.c)  # genuine model/config/code/environment/calendar verification
        self.args = argparse.Namespace(decision_date=None, dry_run=False, cache_only=False, refresh=False, update_only=False)

    def tearDown(self): self.temp.cleanup()

    def save_fixture_model(self, pipeline, provenance):
        saved=save_model(self.root/'models',pipeline,provenance,self.root,'2026-10-03T00:00:00Z')
        path=Path(saved['path'])
        return {'path':str(path),'model_id':saved['model_id'],'artifact_sha256':sha256(path.read_bytes()),
                'pipeline_sha256':saved['pipeline_sha256'],'settings':pipeline['settings'],
                'preprocessing_sha256':sha256(canonical(pipeline['preprocessing']))}

    def run_fixture(self, at, receipt_at=None, fail_symbol=None, correction=False, missing_latest=False):
        client = FictionalClient(self.c, at, receipt_at, fail_symbol, correction, missing_latest)
        self.client = client
        with patch.object(forward, 'utc_now', return_value=at), \
             patch.object(forward, 'HTTPClient', return_value=client), \
             patch('market_research.collect.utc_now', return_value=at):
            return forward.run(self.c, self.args)

    def evidence(self):
        index = {}
        for symbol in self.c['symbols']:
            index[symbol] = {}
            for key, kind, extra in [
                ('action_coverage', 'corporate_action_coverage', {'start_date':'2026-07-01','end_date':'2026-12-31',
                 'status':'verified_complete','same_day_order':'split_then_dividend_postsplit'}),
                ('trading_state', 'security_trading_state', {'listed_from':'2020-01-01','verified_through':'2026-12-31',
                 'halted_dates':[],'delisted_date':None})]:
                p = self.root/(symbol+'-'+key+'.json')
                write_json(p, {'kind':kind,'input_domain':'market','source':'eodhd','symbol':symbol,
                              'verified':True,'verification_method':'FICTIONAL_FIXTURE_ONLY_NO_MARKET_EVIDENCE',
                              'received_at':'2026-10-05T19:00:00Z','usable_at':'2026-10-05T19:00:00Z',**extra})
                index[symbol][key] = {'path':str(p),'sha256':sha256(p.read_bytes())}
        write_json(self.c['label_evidence_index'], index)

    def test_pair_replay_conflict_maturity_and_correction_versions(self):
        at = '2026-10-05T20:20:00Z'
        first = self.run_fixture(at)
        self.assertEqual(first['run_status_counts'], {'pending':5})
        paths = list((self.root/'forward/predictions').glob('*.json'))
        before = {p:sha256(p.read_bytes()) for p in paths}
        self.assertEqual(len(paths),5)
        again = self.run_fixture(at)
        self.assertTrue(all(r['idempotent_replay'] for r in again['results']))
        self.assertEqual(before,{p:sha256(p.read_bytes()) for p in paths})
        record = forward.read_sealed(paths[0]); conflicting = copy.deepcopy(record)
        conflicting['input_sha256'] = 'different'
        with self.assertRaisesRegex(ValueError,'PREDICTION_INPUT_CONFLICT'):
            forward.save_pair(self.root/'forward',conflicting)
        self.assertEqual(before,{p:sha256(p.read_bytes()) for p in paths})
        self.evidence(); self.args.update_only=True
        immature = self.run_fixture('2026-10-09T20:20:00Z')
        self.assertEqual({l['status'] for l in immature['label_updates']},{'pending'})
        mature = self.run_fixture('2026-10-13T20:20:00Z')
        self.assertEqual(mature['evaluation']['mature_samples'],5)
        self.assertEqual(mature['evaluation']['unique_decision_dates'],1)
        self.assertTrue(all(m['count']==5 and not m['excluded'] for m in mature['evaluation']['models']))
        self.assertEqual(before,{p:sha256(p.read_bytes()) for p in paths})
        with DatasetStore(self.root/'forward/datasets.sqlite') as store:
            self.assertEqual(store.payload('label',record['pending_label_version_id'])['status'],'pending')
            update = next(l for l in mature['label_updates'] if l['pair_id']==record['pair_id'])
            label = store.payload('label',update['label_version_id'])
        revised = self.run_fixture('2026-10-14T20:20:00Z',correction=True)
        new = next(l for l in revised['label_updates'] if l['pair_id']==record['pair_id'])
        with DatasetStore(self.root/'forward/datasets.sqlite') as store:
            self.assertNotEqual(new['label_version_id'],update['label_version_id'])
            self.assertNotEqual(store.payload('label',new['label_version_id'])['price_return'],label['price_return'])
            self.assertEqual(store.payload('label',update['label_version_id']),label)
        self.assertEqual(before,{p:sha256(p.read_bytes()) for p in paths})

    def test_late_receipts_missed_weekend_holiday_and_partial_failure(self):
        late = self.run_fixture('2026-10-05T21:02:00Z', receipt_at='2026-10-05T21:01:00Z')
        self.assertEqual(late['run_status_counts'], {'missed':5})
        self.assertFalse((self.root/'forward/predictions').exists())
        self.args.cache_only=True
        self.args.decision_date='2026-10-05'
        missed = self.run_fixture('2026-10-06T14:00:00Z')
        self.assertEqual(missed['run_status_counts'], {'missed':5})
        with Database(self.root/'forward/pit.sqlite') as pit:
            version=pit.conn.execute('SELECT calendar_version FROM trading_session_versions LIMIT 1').fetchone()[0]
            cal=SessionCalendar(pit,version)
            cfg=forward.label_config(self.c,'AAPL',cal,'2026-10-05T20:20:00.000000Z')
            result=forward.query(pit,cutoff='2026-10-05T20:20:00Z',start='2026-07-01',end='2026-10-05',
                                 **forward.request_contract(cfg,'feature_policy'))
            self.assertFalse(result['data'])
            self.assertIn('received_after_cutoff',result['exclusion_counts'])
            for day,now in [('2026-10-03','2026-10-03T20:20:00Z'),('2026-09-07','2026-09-07T20:20:00Z')]:
                self.assertEqual(forward.timing(cal,day,now),('dry_run','NON_TRADING_DAY'))
        self.args.decision_date=None; self.args.cache_only=False; self.args.refresh=True
        partial=self.run_fixture('2026-10-12T20:20:00Z',fail_symbol='TSLA')
        self.assertEqual(partial['run_status_counts'],{'pending':4,'blocked':1})
        self.assertEqual(len(list((self.root/'forward/predictions').glob('*.json'))),4)

    def test_config_pin_and_after_cutoff_input_guard(self):
        changed=copy.deepcopy(self.fixed);changed['baseline_probability']=.7
        with self.assertRaisesRegex(ValueError,'frozen_config_hash_mismatch'): forward.load_frozen(changed)
        feature={'required_features':self.c['features']}
        rows=[{'availability':{'available_at':'2026-10-05T21:01:00Z'}}]
        self.assertEqual(forward.input_gate(rows,feature,'2026-10-05T21:00:00.000000Z'),'INPUT_AFTER_CUTOFF')

    def test_frozen_file_content_and_feature_contract_integrity(self):
        path=Path(self.c['models'][0]['path']); original=path.read_bytes()
        path.write_bytes(original+b' ')
        with self.assertRaisesRegex(ValueError,'frozen_artifact_file_hash_mismatch'): forward.load_frozen(self.c)
        path.write_bytes(original)
        artifact=json.loads(original); artifact['content']['pipeline']['model']['value']=.9
        write_json(path,artifact)
        altered=copy.deepcopy(self.c); altered['models'][0]['artifact_sha256']=sha256(path.read_bytes())
        with patch.object(forward,'CONFIG_SHA256',sha256(canonical(altered))):
            with self.assertRaisesRegex(ValueError,'model_artifact_hash_mismatch'): forward.load_frozen(altered)
        path.write_bytes(original)
        pipeline=copy.deepcopy(artifact['content']['pipeline'])
        pipeline['model']['value']=.5
        pipeline['preprocessing']['features']=list(reversed(self.c['features']))
        altered=copy.deepcopy(self.c)
        altered['models'][0]=self.save_fixture_model(pipeline,self.provenance)
        with patch.object(forward,'CONFIG_SHA256',sha256(canonical(altered))):
            with self.assertRaisesRegex(ValueError,'frozen_model_contract_mismatch'): forward.load_frozen(altered)
        calendar=Path(self.c['calendar_path']); calendar.write_bytes(calendar.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'frozen_calendar_hash_mismatch'): forward.load_frozen(self.c)

    def test_cli_label_update_and_prediction_exit_codes(self):
        for prediction_status,label_status,expected in [
            (None,'blocked',3),(None,'invalid',3),(None,'pending',0),
            ('blocked','pending',3),('missed','pending',3),('pending','blocked',3)]:
            with self.subTest(prediction=prediction_status,label=label_status):
                report={'execution_kind':'dry_run','decision_date':'2026-10-13','evaluation':{},
                    'results':[{'symbol':'AAPL','status':prediction_status,'reason':'fixture_prediction_failure'}] if prediction_status else [],
                    'run_status_counts':{prediction_status:1} if prediction_status else {},'collection_failures':{},
                    'label_updates':[{'pair_id':'fixture','status':label_status,'reason':'fixture_label_reason'}]}
                output=io.StringIO()
                with patch.object(forward,'run',return_value=report),patch.object(sys,'argv',['forward','--config',str(self.root/'config.json'),'--update-only']),redirect_stdout(output):
                    code=forward.main()
                console=json.loads(output.getvalue())
                self.assertEqual(code,expected)
                self.assertEqual(console['label_update_status_counts'],{label_status:1})
                if label_status in ('blocked','invalid'):
                    self.assertEqual(console['label_update_failures'][0]['reason'],'fixture_label_reason')
                else: self.assertFalse(console['label_update_failures'])
                self.assertEqual(len(console['prediction_failures']),int(prediction_status in ('blocked','missed')))

    def test_missing_success_cache_requires_explicit_refresh_before_deadline(self):
        first=self.run_fixture('2026-10-05T20:20:00Z',missing_latest=True)
        self.assertEqual(first['run_status_counts'],{'blocked':5})
        self.assertTrue(all(not r['data_freshness']['cache_used'] for r in first['results']))
        cached=self.run_fixture('2026-10-05T20:25:00Z')
        self.assertEqual(self.client.calls_by_kind['eod'],0)
        self.assertEqual(cached['run_status_counts'],{'blocked':5})
        for r in forward.console_summary(cached)['prediction_failures']:
            info=r['data_freshness']
            self.assertEqual(info['required_trading_date'],'2026-10-05')
            self.assertEqual(info['last_trading_date'],'2026-10-02')
            self.assertTrue(info['cache_used'])
            self.assertIn('--refresh',info['recovery_command'])
            self.assertEqual(info['deadline_at'],'2026-10-05T21:00:00.000000Z')
        self.args.cache_only=True
        self.run_fixture('2026-10-05T20:26:00Z')
        self.assertEqual(self.client.calls,0)
        self.assertFalse((self.root/'forward/predictions').exists())
        old_raw={p:sha256(p.read_bytes()) for p in (self.root/'forward/collection/raw').rglob('*.bin')}
        self.args.cache_only=False; self.args.refresh=True
        refreshed=self.run_fixture('2026-10-05T20:30:00Z')
        self.assertEqual(self.client.calls,20)
        self.assertEqual(refreshed['run_status_counts'],{'pending':5})
        self.assertTrue(all(not info['cache_used'] for info in refreshed['collection_info'].values()))
        self.assertEqual(old_raw,{p:sha256(p.read_bytes()) for p in old_raw})

    def test_refresh_after_deadline_cannot_create_forward_predictions(self):
        self.run_fixture('2026-10-05T20:20:00Z',missing_latest=True)
        self.args.refresh=True
        late=self.run_fixture('2026-10-05T21:01:00Z')
        self.assertEqual(self.client.calls,20)
        self.assertEqual(late['run_status_counts'],{'missed':5})
        self.assertFalse((self.root/'forward/predictions').exists())


if __name__=='__main__': unittest.main()
