"""Synthetic, isolated checks for opt-in label 3.1.0; no market assertions."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from market_research.datasets.builder import build_dataset
from market_research.datasets.contracts import label_contract
from market_research.datasets.labels import compute_labels
from market_research.datasets.store import DatasetStore, iter_dataset_rows, replay_dataset
from market_research.pit import Database
from market_research.storage import write_json, sha256
from stage3_fixture_builder import market_story


class LabelVersionTests(unittest.TestCase):
    def setUp(self):
        at = '2026-10-13T20:01:00.000000Z'
        self.prices = {'strictness':'SYNTHETIC_TEST_ONLY', 'conflict_count':0, 'data':[
            {'trading_date':d, 'open':v, 'currency':'USD', 'source':'synthetic', 'requested_symbol':'SYN',
             'security_record_id':'security', 'price_kind':'ohlcv', 'price_semantics':'as_traded',
             'session_scope':'synthetic_regular_session', 'version_id':d, 'availability':{'available_at':at}}
            for d,v in [('2026-10-06',100), ('2026-10-13',55)]]}
        self.coverage = {'source':'synthetic', 'symbol':'SYN', 'received_at':at, 'usable_at':at,
            'evidence':{'path':'SYNTHETIC_TEST_ONLY', 'sha256':'fixture'}, 'status':'verified_complete',
            'start_date':'2026-10-06', 'end_date':'2026-10-13', 'same_day_order':'split_then_dividend_postsplit',
            'action_classifications':{'split':'share_split', 'dividend':'ordinary_cash_dividend'},
            'price_action_version_ids':['split'], 'cash_action_version_ids':['dividend']}
        self.state = {'source':'synthetic', 'symbol':'SYN', 'received_at':at, 'usable_at':at,
            'evidence':{'path':'SYNTHETIC_TEST_ONLY', 'sha256':'fixture'}, 'listed_from':'2020-01-01',
            'verified_through':'2026-10-13', 'delisted_date':None, 'halted_dates':[], 'halt_intervals':[],
            'halt_coverage':{'start_at':'2026-10-06T00:00:00Z', 'end_at':'2026-10-14T00:00:00Z',
                             'status':'verified_complete', 'includes_prior_unresolved_halts':True}}
        self.plan = {'decision_at':'2026-10-05T21:00:00.000000Z',
            'intended_entry_at':'2026-10-06T13:30:00.000000Z', 'entry_date':'2026-10-06',
            'exits':{5:{'trading_date':'2026-10-13', 'open_utc':'2026-10-13T13:30:00.000000Z',
                       'close_utc':'2026-10-13T20:00:00.000000Z'}}}
        self.actions = {'conflict_count':0, 'data':[
            {'version_id':'split', 'event_type':'splits', 'event_date':'2026-10-07',
             'effective_date':'2026-10-07', 'numerator':2, 'denominator':1, 'source':'synthetic',
             'security_record_id':'security', 'availability':{'available_at':at}},
            {'version_id':'dividend', 'event_type':'dividends', 'event_date':'2026-10-08',
             'ex_date':'2026-10-08', 'amount':1, 'amount_semantics':'as_declared_cash_per_share',
             'payment_date':'2026-10-09', 'currency':'USD', 'source':'synthetic',
             'security_record_id':'security', 'availability':{'available_at':at}}]}

    def label(self, version='3.1.0', **overrides):
        args = dict(calendar=None, plan=self.plan, evaluation_at='2026-10-14T00:00:00.000000Z',
            simulation_asof='2026-10-14T00:00:00.000000Z', price_kind='ohlcv', coverage=self.coverage,
            trading_state=self.state, policy='observed', expected_currency='USD', label_definition_version=version)
        args.update(overrides)
        return compute_labels(self.prices, self.actions, **args)[0]

    def test_ordinary_cash_missing_or_late_cannot_block_price(self):
        original = self.label()
        self.assertAlmostEqual(original['price_return'], .1)
        self.assertAlmostEqual(original['cash_total_return'], .12)
        self.assertEqual(original['used_action_version_ids'], ['split'])
        for field in ('ex_date', 'amount', 'payment_date', 'currency'):
            with self.subTest(missing=field):
                saved = self.actions['data'][1].pop(field)
                current = self.label()
                self.assertEqual(current['status'], 'ready')
                self.assertEqual(current['price_return'], original['price_return'])
                self.assertEqual(current['label_available_at'], original['label_available_at'])
                self.assertEqual(current['cash_total_return_status'], 'blocked')
                self.actions['data'][1][field] = saved
        self.actions['data'][1]['availability']['available_at'] = '2026-10-15T00:00:00.000000Z'
        late = self.label()
        self.assertEqual(late['status'], 'ready')
        self.assertEqual(late['label_available_at'], original['label_available_at'])
        self.assertEqual(late['cash_total_return_status'], 'pending')
        self.assertIsNone(late['cash_total_return'])
        self.assertEqual(late['cash_total_return_available_at'], '2026-10-15T00:00:00.000000Z')
        self.actions['data'].pop()  # Receipt excluded by the real PIT query: cash is still required.
        missing = self.label()
        self.assertEqual(missing['status'], 'ready')
        self.assertEqual(missing['cash_total_return_reason'], 'cash_action_required_version_unavailable')

    def test_cash_order_and_processing_are_separate(self):
        self.coverage.pop('same_day_order')
        current = self.label()
        self.assertEqual(current['status'], 'ready')
        self.assertEqual(current['cash_total_return_reason'], 'corporate_action_order_unverified')
        self.assertEqual(self.label('3.0.1')['reason'], 'corporate_action_order_unverified')
        self.coverage['same_day_order'] = 'split_then_dividend_postsplit'
        pending = self.label(processing_delay_seconds=3600, evaluation_at='2026-10-13T20:02:00.000000Z')
        self.assertEqual(pending['status'], 'pending')
        self.assertIsNone(pending['holdings'])
        self.assertIsNone(pending['cash_holdings'])

    def test_split_hand_calculation_and_required_evidence(self):
        self.assertAlmostEqual(self.label()['price_return'], .1)  # 2*55/100 - 1
        self.actions['data'][0].update(numerator=1, denominator=2)
        self.assertAlmostEqual(self.label()['price_return'], -.725)  # reverse split .5*55/100 - 1
        effective = self.actions['data'][0].pop('effective_date')
        self.assertEqual(self.label()['reason'], 'corporate_action_effective_or_ex_date_missing')
        self.actions['data'][0]['effective_date'] = effective
        self.actions['data'][0]['numerator'] = None
        self.assertEqual(self.label()['reason'], 'split_ratio_unverified')
        self.actions['data'].pop(0)
        self.assertEqual(self.label()['reason'], 'price_action_required_version_unavailable')
        self.assertEqual(self.label(coverage=None)['status'], 'blocked')

    def test_unclassified_and_special_actions_never_become_ordinary_cash(self):
        for classification in (None, 'special_dividend', 'spinoff', 'merger', 'rights'):
            with self.subTest(classification=classification):
                self.coverage['action_classifications']['dividend'] = classification
                self.coverage['price_action_version_ids'] = ['split', 'dividend']
                self.assertEqual(self.label()['reason'], 'corporate_action_type_unverified_or_unsupported')
        self.coverage['action_classifications']['dividend'] = 'ordinary_cash_dividend'
        self.actions['data'][1]['event_type'] = 'merger'  # A classification cannot rewrite event type.
        self.assertEqual(self.label()['status'], 'blocked')

    def test_halt_endpoint_overlap_timezone_and_resumption_boundaries(self):
        for start,end,expected in [
            ('2026-10-13T10:00:00-04:00', None, 'ready'),  # After 09:30 open
            ('2026-10-13T09:30:00-04:00', '2026-10-13T10:00:00-04:00', 'blocked'),
            ('2026-10-06T09:00:00-04:00', '2026-10-06T10:00:00-04:00', 'blocked'),  # Entry
            ('2026-10-12T15:00:00-04:00', None, 'blocked'),
            ('2026-10-05T15:00:00-04:00', None, 'blocked'),  # Carry-in before coverage start
            ('2026-10-13T09:00:00-04:00', '2026-10-13T09:30:00-04:00', 'blocked'),
            ('2026-10-13T09:00:00-04:00', '2026-10-13T09:29:59-04:00', 'ready')]:
            with self.subTest(start=start,end=end):
                self.state['halt_intervals'] = [{'start_at':start, 'resumed_at':end}]
                row = self.label()
                self.assertEqual(row['status'], expected)
                self.assertFalse(row['order_execution_verified'])

    def test_halt_evidence_is_not_inferred_from_empty_list_or_dates(self):
        for change in ({'halted_dates':['2026-10-13']}, {'halt_intervals':[{'start_at':'2026-10-12'}]},
                       {'halt_intervals':[{'start_at':'2026-10-12T12:00:00'}]},
                       {'halt_coverage':None}, {'halt_coverage':{'status':'verified_complete'}}):
            state = copy.deepcopy(self.state); state.update(change)
            self.assertEqual(self.label(trading_state=state)['status'], 'blocked')

    def test_other_return_queries_do_not_change_price_dependencies(self):
        baseline = self.label()
        self.prices['data'].append(dict(self.prices['data'][-1], trading_date='2026-10-20', currency='EUR'))
        self.prices.update(conflict_count=1, conflicts=[{'kind':'unordered_same_source_versions',
                           'key':['synthetic','security','2026-10-20'], 'version_ids':['later1','later2']}])
        self.actions.update(conflict_count=1, conflicts=[{'kind':'unordered_same_source_versions',
                           'version_ids':['cash1','cash2']}])
        self.coverage['action_classifications'].update(cash1='ordinary_cash_dividend', cash2='ordinary_cash_dividend')
        current = self.label()
        self.assertEqual(current['status'], 'ready')
        self.assertEqual(current['label_available_at'], baseline['label_available_at'])
        self.assertEqual(current['cash_total_return_reason'], 'unresolved_cash_action_query_conflict')
        self.prices['conflicts'][0]['key'][2] = '2026-10-13'
        self.assertEqual(self.label()['status'], 'blocked')
        self.prices['conflicts'][0]['key'][2] = '2026-10-20'
        self.actions['conflicts'][0]['version_ids'] = ['later_split1', 'later_split2']
        self.coverage['action_classifications'].update(later_split1='share_split', later_split2='share_split')
        self.coverage['action_effective_dates'] = {'later_split1':'2026-10-20', 'later_split2':'2026-10-20'}
        self.coverage['price_action_version_ids'].append('later_split1')
        scoped = self.label()
        self.assertEqual(scoped['status'], 'ready')
        self.assertEqual(scoped['cash_total_return_status'], 'ready')
        self.assertEqual(scoped['label_available_at'], baseline['label_available_at'])
        self.coverage.pop('action_effective_dates')
        self.assertEqual(self.label()['status'], 'blocked')  # Cannot infer effective date from query key.

    def test_identity_listing_price_and_version_guards_remain(self):
        for field,value in [('open',0), ('currency','EUR'), ('price_semantics','split_adjusted'),
                            ('security_record_id','foreign_security')]:
            with self.subTest(field=field):
                row = self.prices['data'][-1]; old = row[field]; row[field] = value
                self.assertIn(self.label()['status'], ('blocked','invalid')); row[field] = old
        state = copy.deepcopy(self.state); state['delisted_date'] = '2026-10-13'
        self.assertEqual(self.label(trading_state=state)['status'], 'blocked')
        state = copy.deepcopy(self.state); state['listed_from'] = '2026-10-07'
        self.assertEqual(self.label(trading_state=state)['status'], 'blocked')
        with self.assertRaisesRegex(ValueError, 'unsupported_label_definition_version'):
            self.label('unknown')
        self.assertEqual(label_contract()['version'], '3.0.1')
        self.assertEqual(label_contract('3.1.0')['version'], '3.1.0')

    def test_builder_version_and_immutable_legacy_snapshot(self):
        with tempfile.TemporaryDirectory() as t, Database(Path(t)/'pit.sqlite',initialize=True) as db, DatasetStore(Path(t)/'datasets.sqlite') as store:
            root = Path(t)/'synthetic'; cal,cfg,_ = market_story(db,root)
            old = build_dataset(db,store,cfg)
            frozen = list(iter_dataset_rows(store,db,old['snapshot_id']))
            new = copy.deepcopy(cfg); new['label_definition_version'] = '3.1.0'
            for key,extra in [('action_coverage',{'action_classifications':{},'price_action_version_ids':[], 'cash_action_version_ids':[]}),
                              ('trading_state',{'halt_intervals':[], 'halt_coverage':{'status':'verified_complete',
                                'start_at':'2024-01-01T00:00:00Z','end_at':'2024-12-31T23:59:59Z','includes_prior_unresolved_halts':True}})]:
                content = json.loads(Path(cfg[key]['path']).read_text()); content.update(extra)
                p = Path(t)/(key+'-3.1.0.json'); write_json(p,content)
                new[key] = {'path':str(p), 'sha256':sha256(p.read_bytes())}
            current = build_dataset(db,store,new)
            rows = list(iter_dataset_rows(store,db,current['snapshot_id']))
            self.assertTrue(all(r['label']['definition_version']=='3.1.0' and r['label']['status']=='ready' for r in rows))
            self.assertEqual(frozen,list(iter_dataset_rows(store,db,old['snapshot_id'])))
            self.assertEqual(replay_dataset(store,db,old['snapshot_id'])['status'], 'PASS')
            self.assertNotEqual(old['snapshot_id'],current['snapshot_id'])
            self.assertEqual(build_dataset(db,store,cfg)['snapshot_id'],old['snapshot_id'])
            self.assertTrue(all(r['label']['definition_version']=='3.0.1' for r in frozen))
