"""Small forward-path fixtures in temporary roots; no HTTP, fitting or real forecasts."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

from market_research.http import Response
from market_research.storage import canonical, sha256, write_json
from market_research.pit import Database
from market_research.datasets.calendar import SessionCalendar
from market_research.datasets.store import DatasetStore

spec = importlib.util.spec_from_file_location('forward', Path('scripts/run_forward_evaluation.py'))
forward = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forward)


class FictionalClient:
    def __init__(self, config, at, receipt_at=None, fail_symbol=None, correction=False):
        self.config, self.at = config, at
        self.receipt_at = receipt_at or at
        self.fail_symbol = fail_symbol
        self.correction = correction

    def get(self, url, **kwargs):
        u = urlsplit(url); kind, security = u.path.split('/')[-2:]
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
            if self.correction:
                next(r for r in data if r['date']=='2026-10-13')['open'] += 2
        else: data = []  # Fictional complete no-action interval is asserted separately.
        return Response(200, canonical(data), fetched_at_utc=self.receipt_at)


class ForwardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixed = json.loads(Path('configs/forward_evaluation.json').read_text())
        cls.pipelines = forward.load_frozen(cls.fixed)  # actual frozen artifacts, no fit

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.c = copy.deepcopy(self.fixed)
        self.c.update(output_root=str(self.root/'forward'), seed_collection=str(self.root/'seed'),
                      calendar_path=str(Path(self.c['calendar_path']).resolve()),
                      label_evidence_index=str(self.root/'label_evidence.json'),
                      limitation='FICTIONAL_INPUT_UNIT_TEST_ONLY; temporary root excluded from real forward evaluation')
        self.args = argparse.Namespace(decision_date=None, dry_run=False, cache_only=False, refresh=False, update_only=False)

    def tearDown(self): self.temp.cleanup()

    def run_fixture(self, at, receipt_at=None, fail_symbol=None, correction=False):
        client = FictionalClient(self.c, at, receipt_at, fail_symbol, correction)
        with patch.object(forward, 'load_frozen', return_value=self.pipelines), \
             patch.object(forward, 'utc_now', return_value=at), \
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
        changed=copy.deepcopy(self.fixed);changed['baseline_probability']=.5
        with self.assertRaisesRegex(ValueError,'frozen_config_hash_mismatch'): forward.load_frozen(changed)
        feature={'required_features':self.c['features']}
        rows=[{'availability':{'available_at':'2026-10-05T21:01:00Z'}}]
        self.assertEqual(forward.input_gate(rows,feature,'2026-10-05T21:00:00.000000Z'),'INPUT_AFTER_CUTOFF')


if __name__=='__main__': unittest.main()
