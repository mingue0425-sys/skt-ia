import copy
import json
from pathlib import Path
import tempfile
import unittest
import sqlite3
from unittest.mock import patch
from market_research.pit import Database,query,create_snapshot,import_artifacts
from market_research.datasets import DatasetStore,build_dataset,generate_feature,generate_labels,replay_dataset,training_selection
from market_research.datasets.calendar import plus_seconds
from market_research.datasets.features import compute_features
from market_research.datasets.actions import holdings_outcome
from market_research.datasets.evidence import load_assertion
from market_research.datasets.store import iter_dataset_rows
from market_research.storage import write_json
from stage3_fixture_builder import market_story,assertion
from fixture_builder import append,RULE


class DatasetTests(unittest.TestCase):
    def test_assumed_action_coverage_requires_explicit_research_option(self):
        entry, exit_row, actions, coverage = self.ledger_inputs()
        coverage.update(status='research_assumed_complete', research_assumption='current feed assumed complete')
        self.assertEqual(holdings_outcome(entry, exit_row, actions, coverage=coverage, include_receivables=True)['status'], 'blocked')
        result = holdings_outcome(entry, exit_row, actions, coverage=coverage, include_receivables=True, allow_assumed_coverage=True)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['coverage_state'], 'research_assumed_complete')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)/'synthetic'
        self.db=Database(Path(self.tmp.name)/'pit.sqlite',initialize=True)
        self.store=DatasetStore(Path(self.tmp.name)/'datasets.sqlite')
        self.calendar,self.config,self.sessions=market_story(self.db,self.root)
        self.oracle=json.loads((Path(__file__).parent/'fixtures/stage3_expected.json').read_text())

    def tearDown(self):
        self.db.close(); self.store.close(); self.tmp.cleanup()

    def feature(self,config=None,sample=None):
        cfg=config or self.config
        return generate_feature(self.db,self.calendar,sample or cfg['samples'][0],cfg)

    def labels(self,config=None):
        cfg=config or self.config
        return generate_labels(self.db,self.calendar,cfg['samples'][0],cfg)

    def correction(self,*,date=None,close=120,published=None,received=None):
        date=date or self.config['samples'][0]['decision_date']
        row=self.calendar.by_date[date]; original=plus_seconds(row['close_utc'],60)
        append(self.root,label='correction-'+str(close),date=date,close=close,published=original,
               revised=published or '2024-05-31T21:00:00Z',received=received or '2024-05-31T21:01:00Z',
               usable=received or '2024-05-31T21:02:00Z',extra={'observation_end_utc':row['close_utc'],'session_scope':'synthetic_regular_session'})
        import_artifacts(self.db,self.root,domain='synthetic')

    def test_01_future_price_does_not_change_past_features(self):
        before=self.feature()['features']
        self.correction(date=self.sessions[80]['trading_date'])
        self.assertEqual(self.feature()['features'],before)

    def test_02_future_published_correction_cannot_enter_features(self):
        before=self.feature()['features']; self.correction()
        self.assertEqual(self.feature()['features'],before)

    def test_03_old_dates_received_late_do_not_enter_observed_features(self):
        cfg=copy.deepcopy(self.config); cfg['feature_policy']='observed'
        with tempfile.TemporaryDirectory() as t, Database(Path(t)/'late.sqlite',initialize=True) as db:
            cal,cfg0,_=market_story(db,Path(t)/'late',late_received='2026-10-02T12:00:00Z')
            cfg0['feature_policy']='observed'
            f=generate_feature(db,cal,cfg0['samples'][0],cfg0)
            self.assertFalse(f['research_calculable']); self.assertEqual(f['sample_status'],'blocked')
            self.assertIn('received_after_cutoff',f['query_exclusion_counts'])

    def test_04_one_five_twenty_exit_indices_handwritten(self):
        p=self.calendar.plan('2024-01-02'); expected=self.oracle['decision_2024_01_02']
        self.assertEqual(p['decision_at'],expected['decision_at']); self.assertEqual(p['intended_entry_at'],expected['entry_at'])
        self.assertEqual({str(h):r['trading_date'] for h,r in p['exits'].items()},expected['exit_dates'])

    def test_05_dst_holiday_and_early_close_decisions(self):
        self.assertEqual(self.calendar.plan('2024-03-08')['intended_entry_at'],'2024-03-11T13:30:00.000000Z')
        self.assertEqual(self.calendar.plan('2024-03-11')['decision_at'],'2024-03-11T21:00:00.000000Z')
        self.assertEqual(self.calendar.plan('2024-11-29')['decision_at'],'2024-11-29T19:00:00.000000Z')
        self.assertEqual(self.calendar.plan('2024-07-03')['intended_entry_at'],'2024-07-05T13:30:00.000000Z')

    def test_06_exit_open_and_label_availability_are_different(self):
        cfg=copy.deepcopy(self.config); target=self.calendar.plan(cfg['samples'][0]['decision_date'])['exits'][1]
        cfg['label_asof']=cfg['simulation_asof']=target['open_utc']
        row=self.labels(cfg)[0]; self.assertEqual(row['status'],'pending')
        self.assertEqual(row['reason'],'waiting_for_received_usable_open_price_within_delay')
        mature=self.labels()[0]; self.assertEqual(mature['status'],'ready')
        self.assertGreater(mature['label_available_at'],mature['intended_exit_at'])

    def test_07_training_asof_blocks_late_available_labels(self):
        meta=build_dataset(self.db,self.store,self.config)
        labels=self.labels(); first=labels[0]
        before=training_selection(self.store,self.db,meta['snapshot_id'],training_asof=first['intended_exit_at'],allow_research=True)
        self.assertEqual(before['selected_count'],0)
        after=training_selection(self.store,self.db,meta['snapshot_id'],training_asof='2024-06-01T00:00:00Z',allow_research=True)
        self.assertEqual(after['selected_count'],3)
        strict=training_selection(self.store,self.db,meta['snapshot_id'],training_asof='2024-06-01T00:00:00Z')
        self.assertEqual(strict['selected_count'],0)

    def test_08_close_only_cannot_produce_opening_label_or_ohlcv_features(self):
        with tempfile.TemporaryDirectory() as t, Database(Path(t)/'close.sqlite',initialize=True) as db:
            cal,cfg,_=market_story(db,Path(t)/'close',meaning='total_return_adjusted',close_only=True)
            f=generate_feature(db,cal,cfg['samples'][0],cfg)
            self.assertEqual(f['features']['close_return_1']['value'],0)
            for key in ('open_gap','intraday_high_low_ratio','volume_to_prior20_mean'):
                self.assertEqual(f['features'][key]['status'],'unavailable')
            self.assertEqual({r['reason'] for r in generate_labels(db,cal,cfg['samples'][0],cfg)},{'opening_prices_unavailable_close_only'})

    def test_09_mixed_currency_and_price_meaning_are_blocked(self):
        f=self.feature(); result=json.loads(self.db.conn.execute('SELECT payload_json FROM query_snapshots WHERE snapshot_id=?',(f['feature_snapshot_id'],)).fetchone()[0])
        for field,value in (('currency','EUR'),('price_semantics','split_adjusted')):
            mixed=copy.deepcopy(result); mixed['data'][0][field]=value
            computed=compute_features(mixed,self.calendar,anchor_date=self.config['samples'][0]['decision_date'],price_kind='ohlcv',as_traded_mode='price_change_only')
            self.assertEqual(computed['status'],'blocked'); self.assertIn('mixed_series_meaning_currency_session_or_identity',computed['reasons'])

    def ledger_inputs(self,dividend=False):
        entry={'trading_date':'2024-01-03','open':100,'currency':'USD'}; exit={'trading_date':'2024-01-10','open':55,'currency':'USD'}
        acts=[{'version_id':'synthetic-split','event_type':'split','event_date':'2024-01-05','effective_date':'2024-01-05','numerator':2,'denominator':1}]
        if dividend: acts.append({'version_id':'synthetic-dividend','event_type':'dividend','event_date':'2024-01-08','ex_date':'2024-01-08','amount':1,'currency':'USD','amount_semantics':'as_declared_cash_per_share','payment_date':'2024-01-20'})
        cov=load_assertion(self.config['action_coverage'],kind='corporate_action_coverage',domain='synthetic')
        return entry,exit,acts,cov

    def test_10_split_quantity_and_return_match_hand_calculation(self):
        a,b,actions,cov=self.ledger_inputs(); r=holdings_outcome(a,b,actions,coverage=cov,include_receivables=True)
        self.assertEqual(r['exit_quantity'],2); self.assertAlmostEqual(r['price_return'],0.1)
        self.assertNotAlmostEqual(b['open']/a['open']-1,r['price_return'])

    def test_11_dividend_right_payment_and_receivable_hand_calculation(self):
        a,b,actions,cov=self.ledger_inputs(True)
        r=holdings_outcome(a,b,actions,coverage=cov,include_receivables=True)
        self.assertEqual(r['receivable_cash'],2); self.assertEqual(r['paid_cash'],0); self.assertAlmostEqual(r['cash_total_return'],0.12)
        no=holdings_outcome(a,b,actions,coverage=cov,include_receivables=False); self.assertAlmostEqual(no['cash_total_return'],0.1)
        actions[1]['payment_date']='2024-01-09'
        paid=holdings_outcome(a,b,actions,coverage=cov,include_receivables=False); self.assertEqual(paid['paid_cash'],2); self.assertAlmostEqual(paid['cash_total_return'],0.12)
        a['trading_date']='2024-01-08'; a['open']=50
        buy_ex=holdings_outcome(a,b,actions,coverage=cov,include_receivables=True); self.assertEqual(buy_ex['receivable_cash'],0)

    def test_12_missing_corporate_action_coverage_blocks_not_zero_actions(self):
        cfg=copy.deepcopy(self.config); cfg.pop('action_coverage')
        r=self.labels(cfg)[0]; self.assertEqual(r['status'],'blocked'); self.assertIsNone(r['price_return']); self.assertEqual(r['cash_total_return_status'],'blocked')
        complete=self.labels()[0]; self.assertEqual(complete['holdings']['coverage_state'],'verified_no_actions_in_interval')

    def test_13_halt_and_delisting_are_recorded_without_price_substitution(self):
        for kind in ('halt','delist'):
            cfg=copy.deepcopy(self.config); p=self.calendar.plan(cfg['samples'][0]['decision_date']); exit=p['exits'][1]['trading_date']
            cfg['trading_state']=assertion(self.root,'security_trading_state',**({'halted_dates':[exit]} if kind=='halt' else {'delisted_date':exit}))
            r=self.labels(cfg)[0]; self.assertEqual(r['status'],'blocked')
            self.assertIn('halted' if kind=='halt' else 'delisting',r['reason'])
        a,b,acts,cov=self.ledger_inputs()
        acts.append({'version_id':'synthetic-merger','event_type':'merger','event_date':'2024-01-01','effective_date':'2024-01-08'})
        r=holdings_outcome(a,b,acts,coverage=cov,include_receivables=True)
        self.assertEqual(r['status'],'blocked'); self.assertEqual(r['reason'],'merger_delisting_or_action_type_unsupported')

    def test_14_short_lookback_and_actual_missing_session_are_distinct(self):
        early=self.feature(sample={'decision_date':self.sessions[4]['trading_date']})
        self.assertEqual(early['features']['close_price_change_20']['reason'],'lookback_outside_calendar')
        with tempfile.TemporaryDirectory() as t, Database(Path(t)/'missing.sqlite',initialize=True) as db:
            cal,cfg,_=market_story(db,Path(t)/'missing',missing=(60,))
            f=generate_feature(db,cal,cfg['samples'][0],cfg)
            self.assertEqual(f['features']['close_price_change_20']['reason'],'missing_session_or_collection_failure')

    def test_15_explicit_assumed_results_never_claim_strict(self):
        cfg=copy.deepcopy(self.config); cfg.update(feature_policy='research_assumed',label_policy='research_assumed',assumption_rule=RULE)
        prior=self.sessions[64]
        append(self.root,label='date-only-prior',date=prior['trading_date'],close=95,published=None,
               publication_date=prior['trading_date'],proof=False,received='2024-05-31T21:00:00Z',usable='2024-05-31T21:01:00Z',
               extra={'observation_end_utc':prior['close_utc'],'session_scope':'synthetic_regular_session'})
        import_artifacts(self.db,self.root,domain='synthetic')
        f=self.feature(cfg); self.assertEqual(f['strictness'],'NOT_STRICT_PIT'); self.assertFalse(f['historical_pit_eligible'])
        self.assertAlmostEqual(f['features']['close_price_change_1']['value'],1/19)
        payload=json.loads(self.db.conn.execute('SELECT payload_json FROM query_snapshots WHERE snapshot_id=?',(f['feature_snapshot_id'],)).fetchone()[0])
        assumed=next(r for r in payload['data'] if r['trading_date']==prior['trading_date'])
        self.assertIsNone(assumed['temporal']['published_at'])
        self.assertEqual(assumed['availability']['available_at'],'2024-04-05T05:00:00.000000Z')
        meta=build_dataset(self.db,self.store,cfg); replay=replay_dataset(self.store,self.db,meta['snapshot_id'])
        self.assertEqual(replay['metadata']['strictness'],'NOT_STRICT_PIT')
        self.assertTrue(all(not r['label']['learning_ready'] for r in iter_dataset_rows(self.store,self.db,meta['snapshot_id'])))

    def test_16_corrected_label_creates_new_version_preserves_frozen_dataset(self):
        old=build_dataset(self.db,self.store,self.config)
        before=list(iter_dataset_rows(self.store,self.db,old['snapshot_id']))
        date=self.calendar.plan(self.config['samples'][0]['decision_date'])['exits'][5]['trading_date']
        self.correction(date=date,close=120)
        new=build_dataset(self.db,self.store,self.config)
        self.assertNotEqual(old['snapshot_id'],new['snapshot_id'])
        self.assertEqual(list(iter_dataset_rows(self.store,self.db,old['snapshot_id'])),before)
        newrows=list(iter_dataset_rows(self.store,self.db,new['snapshot_id']))
        self.assertAlmostEqual([r['label']['price_return'] for r in newrows if r['horizon_sessions']==5][0],0.2)

    def test_17_same_inputs_config_versions_produce_identical_hash(self):
        a=build_dataset(self.db,self.store,self.config); b=build_dataset(self.db,self.store,self.config)
        self.assertEqual(a['content_sha256'],b['content_sha256']); self.assertTrue(b['reused']); self.assertEqual(self.store.audit()['status'],'PASS')

    def test_18_past_missing_endpoint_is_blocked_not_indefinitely_pending(self):
        with tempfile.TemporaryDirectory() as t, Database(Path(t)/'short.sqlite',initialize=True) as db:
            cal,cfg,_=market_story(db,Path(t)/'short',n=68)
            labels=generate_labels(db,cal,cfg['samples'][0],cfg)
            self.assertEqual(labels[-1]['status'],'blocked'); self.assertEqual(labels[-1]['reason'],'past_endpoint_price_missing_or_collection_failure')
            cfg['label_asof']=cfg['simulation_asof']=cal.plan(cfg['samples'][0]['decision_date'])['intended_entry_at']
            self.assertTrue(all(r['status']=='pending' for r in generate_labels(db,cal,cfg['samples'][0],cfg)))

    def test_19_feature_ready_after_entry_blocks_sample_execution(self):
        spec=copy.deepcopy(self.config['samples'][0]); p=self.calendar.plan(spec['decision_date'])
        spec.update(feature_ready_at=plus_seconds(p['intended_entry_at'],60),feature_ready_basis='actual_operational_record')
        f=self.feature(sample=spec); self.assertEqual(f['sample_status'],'blocked'); self.assertEqual(f['execution_status'],'blocked')
        self.assertIn('feature_ready_after_intended_entry',f['sample_reasons'])

    def test_20_label_query_cannot_flow_into_feature_module(self):
        with patch('market_research.datasets.builder.generate_labels',side_effect=AssertionError('future path touched')):
            f=self.feature(); self.assertEqual(f['features']['close_price_change_1']['value'],0)
        with patch('market_research.datasets.builder.compute_features',side_effect=AssertionError('feature path touched')):
            self.assertEqual(self.labels()[0]['status'],'ready')
        self.assertEqual([r['trading_date'] for r in json.loads(self.db.conn.execute('SELECT payload_json FROM query_snapshots WHERE snapshot_id=?',(f['feature_snapshot_id'],)).fetchone()[0])['data']][-1],self.config['samples'][0]['decision_date'])

    def test_21_handwritten_numeric_feature_table(self):
        feature=self.feature()
        for name,expected in self.oracle['constant_100'].items():
            self.assertAlmostEqual(feature['features'][name]['value'],expected)

    def test_22_unverified_cash_does_not_silently_become_total_return(self):
        a,b,acts,cov=self.ledger_inputs(True); acts[1]['amount_semantics']='provider_reported_split_basis_unverified'
        price=holdings_outcome(a,b,acts,coverage=cov,include_receivables=True,require_cash=False)
        total=holdings_outcome(a,b,acts,coverage=cov,include_receivables=True)
        self.assertEqual(price['status'],'ready'); self.assertEqual(total['status'],'blocked')

    def test_23_assertion_hash_corruption_and_malformed_cutoff_are_detected(self):
        cfg=copy.deepcopy(self.config); Path(cfg['action_coverage']['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError,'assertion_file_missing_or_hash_mismatch'): self.labels(cfg)
        with self.assertRaisesRegex(ValueError,'feature_cutoff_or_decision_contract_invalid'):
            self.feature(sample={'decision_date':self.config['samples'][0]['decision_date'],'feature_cutoff':'2026-10-02T12:00:00Z'})

    def test_24_invalid_order_and_zero_price_do_not_produce_ready_labels(self):
        cfg=copy.deepcopy(self.config); cfg['simulation_asof']='2024-01-01T00:00:00Z'
        self.assertEqual({r['status'] for r in self.labels(cfg)},{'invalid'})
        a,b,acts,cov=self.ledger_inputs(); a['open']=0
        self.assertEqual(holdings_outcome(a,b,acts,coverage=cov,include_receivables=True)['status'],'invalid')
        self.assertEqual(holdings_outcome(a,b,acts,coverage=cov,include_receivables='false')['status'],'invalid')

    def test_25_nonconstant_volatility_ddof_and_volume_exclusion(self):
        for i,close in ((64,110),(65,121)):
            row=self.sessions[i]
            self.correction(date=row['trading_date'],close=close,published=plus_seconds(row['close_utc'],600),received=plus_seconds(row['close_utc'],660))
        f=self.feature()['features']
        self.assertAlmostEqual(f['close_price_change_1']['value'],0.1)
        self.assertAlmostEqual(f['close_price_change_5']['value'],0.21)
        self.assertAlmostEqual(f['close_to_ma_20']['value'],389/2031)
        # Five log changes: [0,0,0,log(1.1),log(1.1)]. Manual variance = .3*log(1.1)^2.
        import math
        self.assertAlmostEqual(f['realized_log_price_change_volatility_5']['value'],0.5477225575051661*0.09531017980432493)
        self.assertAlmostEqual(f['open_gap']['value'],0.1)
        original=self.feature(); result=json.loads(self.db.conn.execute('SELECT payload_json FROM query_snapshots WHERE snapshot_id=?',(original['feature_snapshot_id'],)).fetchone()[0])
        result['data'][-1]['volume']=2000
        result['data'][-2]['open']=None  # Not a required field for today's gap.
        out=compute_features(result,self.calendar,anchor_date=self.config['samples'][0]['decision_date'],price_kind='ohlcv',as_traded_mode='price_change_only')
        self.assertEqual(out['features']['volume_to_prior20_mean']['value'],2)
        self.assertAlmostEqual(out['features']['open_gap']['value'],0.1)

    def test_26_pinned_calendar_corruption_is_detected_by_dataset_replay(self):
        snap=build_dataset(self.db,self.store,self.config)
        (self.root/'calendar.json').write_text('[]')
        with self.assertRaisesRegex(ValueError,'dataset_assertion_file_missing_or_corrupt'):
            replay_dataset(self.store,self.db,snap['snapshot_id'])

    def test_27_actual_zero_volume_and_null_are_distinct(self):
        original=self.feature(); result=json.loads(self.db.conn.execute('SELECT payload_json FROM query_snapshots WHERE snapshot_id=?',(original['feature_snapshot_id'],)).fetchone()[0])
        for value,expected in ((0,'ready'),(None,'unavailable')):
            result['data'][-1]['volume']=value
            out=compute_features(result,self.calendar,anchor_date=self.config['samples'][0]['decision_date'],price_kind='ohlcv',as_traded_mode='price_change_only')
            self.assertEqual(out['features']['volume_to_prior20_mean']['status'],expected)
            self.assertEqual(out['observation_counts']['reported_zero_volume'],1 if value==0 else 0)

    def test_28_invalid_sample_is_preserved_and_valid_neighbor_finishes(self):
        cfg=copy.deepcopy(self.config)
        cfg['samples'].append({'decision_date':'2024-07-04'})
        result=build_dataset(self.db,self.store,cfg)
        self.assertEqual(result['feature_status_counts'],{'ready':1,'invalid':1})
        self.assertEqual(result['label_status_counts'],{'ready':3,'invalid':3})
        self.assertEqual(replay_dataset(self.store,self.db,result['snapshot_id'])['items'],6)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.conn.execute('UPDATE dataset_snapshots SET metadata_json=?',('{}',))

    def test_29_declared_currency_must_match_even_when_series_is_consistent(self):
        cfg=copy.deepcopy(self.config); cfg['series']['currency']='EUR'
        self.assertEqual(self.feature(cfg)['sample_status'],'blocked')
        self.assertEqual({r['reason'] for r in self.labels(cfg)},{'label_currency_unknown_or_contract_mismatch'})

    def test_30_maturity_update_appends_version_and_preserves_pending_snapshot(self):
        cfg=copy.deepcopy(self.config)
        target=self.calendar.plan(cfg['samples'][0]['decision_date'])['exits'][1]
        cfg['label_asof']=cfg['simulation_asof']=target['open_utc']
        pending=build_dataset(self.db,self.store,cfg)
        self.assertEqual(pending['label_status_counts'],{'pending':3})
        frozen=list(iter_dataset_rows(self.store,self.db,pending['snapshot_id']))
        matured=build_dataset(self.db,self.store,self.config)
        self.assertEqual(matured['label_status_counts'],{'ready':3})
        self.assertNotEqual(pending['snapshot_id'],matured['snapshot_id'])
        self.assertEqual(list(iter_dataset_rows(self.store,self.db,pending['snapshot_id'])),frozen)

    def test_31_announced_earlier_split_and_dividend_are_applied_on_effective_dates(self):
        for i,close,actions in (
            (67,50,[{'event_type':'splits','event_date':'2024-01-02','declaration_date':'2024-01-02',
                     'effective_date':self.sessions[67]['trading_date'],'numerator':2,'denominator':1,'amount_semantics':'split_ratio'}]),
            (68,50,[{'event_type':'dividends','event_date':'2024-01-03','declaration_date':'2024-01-03',
                     'ex_date':self.sessions[68]['trading_date'],'record_date':self.sessions[69]['trading_date'],
                     'payment_date':'2024-04-30','amount':1,'currency':'USD','amount_semantics':'as_declared_cash_per_share'}]),
            (71,55,None)):
            session=self.sessions[i]
            append(self.root,label='action-ledger-'+str(i),date=session['trading_date'],close=close,
                   published=plus_seconds(session['close_utc'],60),revised=plus_seconds(session['close_utc'],600),
                   received=plus_seconds(session['close_utc'],660),usable=plus_seconds(session['close_utc'],720),actions=actions,
                   extra={'observation_end_utc':session['close_utc'],'session_scope':'synthetic_regular_session'})
        result=import_artifacts(self.db,self.root,domain='synthetic'); self.assertEqual(result['quarantined'],0)
        label=next(r for r in self.labels() if r['horizon_sessions']==5)
        self.assertEqual(label['status'],'ready'); self.assertEqual(label['holdings']['exit_quantity'],2)
        self.assertAlmostEqual(label['price_return'],0.1); self.assertAlmostEqual(label['cash_total_return'],0.12)
        self.assertEqual(label['cash_holdings']['receivable_cash'],2)
        self.assertEqual(len(label['used_action_version_ids']),2)
        self.assertEqual(self.feature()['features']['close_price_change_1']['value'],0)

    def test_32_early_declaration_without_effective_date_cannot_mean_no_action(self):
        for kind,field in (('split','effective_date'),('dividend','ex_date')):
            a,b,actions,cov=self.ledger_inputs(True)
            action=next(v for v in actions if v['event_type']==kind)
            action['event_date']='2024-01-02'  # Before entry; effective date remains unknown.
            action.pop(field)
            outcome=holdings_outcome(a,b,actions,coverage=cov,include_receivables=True)
            self.assertEqual(outcome['status'],'blocked')
            self.assertEqual(outcome['reason'],'corporate_action_effective_or_ex_date_missing')

    def test_33_past_label_evaluation_uses_its_own_maturity_clock(self):
        cfg=copy.deepcopy(self.config)
        target=self.calendar.plan(cfg['samples'][0]['decision_date'])['exits'][1]
        cfg['label_asof']=target['open_utc']  # simulation_asof stays months later.
        rows=self.labels(cfg)
        self.assertEqual(rows[0]['status'],'pending')
        self.assertEqual(rows[0]['reason'],'waiting_for_received_usable_open_price_within_delay')
        self.assertEqual({r['reason'] for r in rows[1:]},{'target_open_after_label_evaluation_asof'})
        self.assertTrue(all(r['price_return'] is None for r in rows))

    def test_34_zero_price_is_invalid_even_when_volume_feature_is_computable(self):
        session=self.sessions[65]
        append(self.root,label='zero-price',date=session['trading_date'],close=0,
               published=plus_seconds(session['close_utc'],60),revised=plus_seconds(session['close_utc'],600),
               received=plus_seconds(session['close_utc'],660),usable=plus_seconds(session['close_utc'],720),
               extra={'open':0,'high':0,'low':0,'observation_end_utc':session['close_utc'],
                      'session_scope':'synthetic_regular_session'})
        imported=import_artifacts(self.db,self.root,domain='synthetic')
        self.assertEqual(imported['quarantined'],0)
        feature=self.feature()
        self.assertEqual(feature['features']['volume_to_prior20_mean']['value'],1)
        self.assertEqual(feature['features']['close_price_change_1']['status'],'invalid')
        self.assertEqual(feature['sample_status'],'invalid')
        self.assertEqual(feature['execution_status'],'invalid')
        self.assertFalse(feature['research_calculable'])

    def test_35_finite_inputs_overflow_does_not_create_ready_holdings(self):
        a,b,actions,cov=self.ledger_inputs()
        for change in ('quantity','split'):
            current=copy.deepcopy(actions)
            if change=='split': current[0].update(numerator=1e308,denominator=1e-308)
            result=holdings_outcome(a,b,current,coverage=cov,include_receivables=True,
                                   initial_quantity=1e308 if change=='quantity' else 1)
            self.assertEqual(result['status'],'invalid')
            self.assertEqual(result['reason'],'nonfinite_or_underflow_holdings_result')

    def test_36_pending_processing_does_not_export_computed_holdings_outcomes(self):
        cfg=copy.deepcopy(self.config)
        target=self.calendar.plan(cfg['samples'][0]['decision_date'])['exits'][1]
        cfg['label_asof']=cfg['simulation_asof']=plus_seconds(target['close_utc'],120)
        cfg['label_processing_delay_seconds']=3600
        row=self.labels(cfg)[0]
        self.assertEqual(row['status'],'pending')
        self.assertEqual(row['reason'],'label_processing_delay_not_completed')
        self.assertEqual(row['label_available_at'],plus_seconds(target['close_utc'],3660))
        self.assertIsNone(row['price_return'])
        self.assertIsNone(row['holdings'])
        self.assertIsNone(row['cash_holdings'])


if __name__=='__main__': unittest.main()
