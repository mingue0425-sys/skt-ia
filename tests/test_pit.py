import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from market_research.pit import Database, import_artifacts, query, create_snapshot, replay_snapshot
from market_research.pit.importer import import_calendar
from market_research.pit.time import assumed_available
from market_research.storage import write_json, canonical, sha256
from market_research.validation.quality import calendar_rows
from market_research.cli import main as cli
from fixture_builder import append, story, RULE

LATE = "2024-01-10T22:00:00Z"
EARLY = "2024-01-03T22:00:00Z"


class PitTests(unittest.TestCase):
    def test_explicit_unknown_publication_research_keeps_strict_blocked(self):
        from market_research.pit.query import eligible
        row = {'temporal_json': json.dumps({'published_at': None, 'revised_at': None, 'publication_date': None}),
               'evidence_json': '{}', 'observation_end': '2024-01-03T21:00:00Z',
               'observation_end_evidence': 'reference_calendar_assumption'}
        links = [{'raw_sha256': 'original'}]
        rule = RULE | {'allow_observation_date_for_unknown_publication': True, 'bar_close_delay_seconds': 900}
        self.assertEqual(eligible(row, links, '2024-01-03T21:14:59Z', 'research_assumed', rule)[1], 'assumed_publication_after_cutoff')
        selected, reason = eligible(row, links, '2024-01-03T22:00:00Z', 'research_assumed', rule)
        self.assertIsNone(reason)
        self.assertIn('not_historical_publication', selected['assumption']['basis'])
        self.assertEqual(eligible(row, links, '2024-01-03T22:00:00Z', 'historical_verified', None)[1], 'publication_time_unknown')
        self.assertEqual(eligible(row, links, '2024-01-03T22:00:00Z', 'research_assumed', RULE)[1], 'publication_time_unknown_no_assumption_input')
        row['temporal_json'] = json.dumps({'published_at': None, 'revised_at': '2024-01-04T22:00:00Z', 'publication_date': None})
        self.assertEqual(eligible(row, links, '2024-01-03T22:00:00Z', 'research_assumed', rule)[1], 'assumed_publication_after_cutoff')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)/"synthetic_artifacts"
        self.root.mkdir()
        self.db = Database(Path(self.tmp.name)/"test.sqlite",initialize=True)

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def ingest(self, **kwargs):
        return import_artifacts(self.db,self.root,domain="synthetic",now=lambda: "2024-01-10T23:00:00Z",**kwargs)

    def q(self, policy="historical_verified", cutoff=EARLY, **kwargs):
        return query(self.db,cutoff=cutoff,policy=policy,identifier_requirement="temporary_allowed",
                     symbol_mode="provider_label",input_domain="synthetic",**kwargs)

    def closes(self, result):
        return [r["close"] for r in result["data"]]

    def test_01_handwritten_oracle_for_initial_correction_and_late_disclosure(self):
        expected = json.loads((Path(__file__).parent/"fixtures/stage2_expected.json").read_text())
        for case in expected["cases"]:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as t:
                root = Path(t)/"synthetic"; root.mkdir(); story(root,case["case"])
                with Database(Path(t)/"oracle.sqlite",initialize=True) as db:
                    report = import_artifacts(db,root,domain="synthetic")
                    self.assertEqual(report["quarantined"],0)
                    args = {"assumption_rule": expected["assumption_rule"]} if case["policy"]=="research_assumed" else {}
                    result = query(db,cutoff=case["cutoff"],policy=case["policy"],identifier_requirement="temporary_allowed",
                                   symbol_mode="provider_label",input_domain="synthetic",**args)
                    self.assertEqual(self.closes(result),case["expected_close"])
        story(self.root,"initial_and_correction"); self.ingest()
        corrected = self.q(cutoff=LATE)["data"][0]
        self.assertIsNotNone(corrected["previous_version_id"])
        self.assertEqual(corrected["previous_relation"],"version_bound_publication_order")
        self.assertEqual(corrected["temporal"]["revised_at"],"2024-01-05T21:05:00.000000Z")

    def test_02_late_publication_not_backdated_to_event(self):
        story(self.root,"late_event_publication"); self.ingest()
        before = self.q(); after = self.q(cutoff=LATE)
        self.assertEqual(self.closes(before),[]); self.assertEqual(self.closes(after),[100])
        self.assertIn("publication_after_cutoff",before["exclusion_counts"])

    def test_03_publication_receipt_processing_and_ingest_are_distinct(self):
        story(self.root,"early_publication_late_receipt"); self.ingest()
        self.assertEqual(self.closes(self.q()),[100])
        self.assertEqual(self.closes(self.q("observed")),[])
        self.assertEqual(self.closes(self.q("observed",cutoff=LATE)),[100])
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/"synthetic"; root.mkdir()
            append(root,label="processing-late",usable="2024-01-05T21:07:00Z")
            with Database(Path(t)/"test.sqlite",initialize=True) as db:
                import_artifacts(db,root,domain="synthetic",now=lambda:"2024-02-01T00:00:00Z")
                args=dict(policy="observed",identifier_requirement="temporary_allowed",symbol_mode="provider_label",input_domain="synthetic")
                self.assertEqual(query(db,cutoff=EARLY,**args)["exclusion_counts"],{"processing_after_cutoff":1})
                self.assertEqual(query(db,cutoff=LATE,**args)["selected_count"],1)  # ingest in February is irrelevant

    def test_04_null_publication_observed_only_and_no_policy_fallback(self):
        append(self.root,label="null",published=None,proof=False); self.ingest()
        self.assertEqual(self.closes(self.q()),[])
        self.assertEqual(self.closes(self.q("observed")),[100])
        self.assertEqual(self.closes(self.q("research_assumed",assumption_rule=RULE)),[])
        with self.assertRaises(ValueError): query(self.db,cutoff=EARLY,policy="unknown")
        with self.assertRaises(ValueError): self.q("research_assumed")
        with self.assertRaises(TypeError): query(self.db,cutoff=EARLY)

    def test_05_date_assumption_preserves_fields_precision_and_not_strict_label(self):
        append(self.root,label="date",published=None,publication_date="2024-01-03",proof=False); self.ingest()
        strict=self.q(cutoff=LATE); assumed=self.q("research_assumed",cutoff="2024-01-04T06:00:00Z",assumption_rule=RULE)
        self.assertEqual(strict["selected_count"],0); self.assertEqual(assumed["selected_count"],1)
        self.assertEqual(assumed["strictness"],"NOT_STRICT_PIT")
        r=assumed["data"][0]
        self.assertIsNone(r["temporal"]["published_at"])
        self.assertEqual(r["temporal"]["publication_date"],"2024-01-03")
        self.assertEqual(r["temporal"]["publication_timezone"],"America/New_York")
        self.assertFalse(r["availability"]["assumption"]["version_content_verified"])
        self.assertEqual(self.q("research_assumed",cutoff="2024-01-04T05:59:59Z",assumption_rule=RULE)["selected_count"],0)
        self.assertEqual(assumed_available("2024-03-10",RULE),"2024-03-11T05:00:00.000000Z")
        self.assertEqual(assumed_available("2024-11-03",RULE),"2024-11-04T06:00:00.000000Z")

    def test_06_same_raw_content_keeps_two_receipt_events(self):
        a=append(self.root,label="same-a")
        append(self.root,label="same-b",raw_body=a["raw"],received="2024-01-04T21:06:00Z",usable="2024-01-04T21:07:00Z")
        self.ingest(); counts=self.db.counts()
        self.assertEqual(counts["raw_receipts"],2)
        self.assertEqual(len(list((self.root/"raw").rglob("*.bin"))),1)
        result=self.q("observed",cutoff=LATE)
        self.assertEqual(self.closes(result),[100])
        self.assertEqual(result["data"][0]["provenance"]["receipt_id"],"synthetic-receipt-same-b")

    def test_07_conflicting_versions_have_no_arbitrary_winner(self):
        story(self.root,"unordered_conflict"); self.ingest()
        for policy in ("observed","historical_verified"):
            result=self.q(policy,cutoff=LATE)
            self.assertEqual(result["selected_count"],0)
            self.assertEqual(result["conflict_count"],1)
            self.assertEqual(result["status"],"CONFLICT")

    def test_08_symbol_reuse_and_exchange_overlap_conflict(self):
        append(self.root,label="old",local_label="synthetic:share-old",date="2024-01-02",
               mappings=[{"symbol":"REUSE","exchange":"XNAS","effective_from":"2020-01-01","effective_to":"2024-01-03"}])
        append(self.root,label="new",local_label="synthetic:share-new",close=200,date="2024-01-04",
               published="2024-01-04T21:05:00Z",received="2024-01-04T21:06:00Z",usable="2024-01-04T21:07:00Z",
               mappings=[{"symbol":"REUSE","exchange":"XNYS","effective_from":"2024-01-03"}])
        self.ingest()
        result=query(self.db,cutoff=LATE,policy="historical_verified",symbols=["REUSE"],symbol_mode="effective_mapping",
                     identifier_requirement="temporary_allowed",input_domain="synthetic")
        self.assertEqual(sorted(self.closes(result)),[100,200])
        append(self.root,label="overlap",local_label="synthetic:share-third",close=300,
               mappings=[{"symbol":"REUSE","exchange":"XNAS","effective_from":"2020-01-01"}])
        self.ingest()
        conflict=query(self.db,cutoff=LATE,policy="historical_verified",symbols=["REUSE"],identifier_requirement="temporary_allowed",input_domain="synthetic")
        self.assertEqual(conflict["selected_count"],0)
        self.assertGreater(conflict["exclusion_counts"].get("symbol_mapping_conflict",0),0)

    def test_09_future_known_mapping_does_not_change_past_query(self):
        append(self.root,label="original",local_label="synthetic:share")
        self.ingest(); before=query(self.db,cutoff=EARLY,policy="historical_verified",symbols=["ALIAS"],input_domain="synthetic",identifier_requirement="temporary_allowed")
        append(self.root,label="future-map",local_label="synthetic:share",published="2024-01-08T21:05:00Z",
               received="2024-01-08T21:06:00Z",usable="2024-01-08T21:07:00Z",
               mappings=[{"symbol":"ALIAS","exchange":"XNAS","effective_from":"2020-01-01"}])
        self.ingest()
        after=query(self.db,cutoff=EARLY,policy="historical_verified",symbols=["ALIAS"],input_domain="synthetic",identifier_requirement="temporary_allowed")
        self.assertEqual(before["selected_count"],0); self.assertEqual(after["selected_count"],0)
        later=query(self.db,cutoff=LATE,policy="historical_verified",symbols=["ALIAS"],input_domain="synthetic",identifier_requirement="temporary_allowed")
        self.assertEqual(later["selected_count"],1)

    def test_10_raw_split_adjusted_and_adjusted_close_remain_separate(self):
        append(self.root,label="raw",close=100,meaning="as_traded")
        append(self.root,label="split",close=10,meaning="split_adjusted",
               extra={"adjusted_close":9,"adjusted_close_semantics":"split_and_distribution_adjusted_current_vintage"})
        self.ingest()
        result=self.q(price_semantics=("as_traded","split_adjusted","total_return_adjusted"))
        actual={r["price_semantics"]:(r["close"],r["price_kind"]) for r in result["data"]}
        self.assertEqual(actual,{"as_traded":(100,"ohlcv"),"split_adjusted":(10,"ohlcv"),"total_return_adjusted":(9,"close_only")})
        adjusted=next(r for r in result["data"] if r["price_kind"]=="close_only")
        self.assertIsNone(adjusted["volume"]); self.assertIsNone(adjusted["open"])

    def test_11_dst_early_close_and_extraordinary_holiday_bounds(self):
        dates=[("2024-03-08","2024-03-08T21:00:00Z"),("2024-03-11","2024-03-11T20:00:00Z"),
               ("2024-11-29","2024-11-29T18:00:00Z"),("2025-01-09",None)]
        p=self.root/"derived/calendar.json"; write_json(p,calendar_rows("2024-01-01","2025-12-31"))
        cv=import_calendar(self.db,p,domain="synthetic",known_at="2024-03-01T00:00:00Z")["calendar_version"]
        for i,(d,end) in enumerate(dates):
            append(self.root,label="calendar-"+str(i),symbol="CAL"+str(i),date=d,observation_end=False,
                   published=None,proof=False,received=d+"T16:00:00Z",usable=d+"T16:01:00Z")
        self.ingest()
        for i,(d,end) in enumerate(dates):
            result=self.q("observed",cutoff=d+"T22:00:00Z",symbols=["CAL"+str(i)],calendar_version=cv)
            if end:
                self.assertEqual(result["selected_count"],1)
                self.assertEqual(result["data"][0]["observation_bound"]["observation_end"],end.replace("Z",".000000Z"))
                early=d+("T17:59:59Z" if i==2 else "T19:59:59Z")
                self.assertEqual(self.q("observed",cutoff=early,symbols=["CAL"+str(i)],calendar_version=cv)["selected_count"],0)
            else: self.assertEqual(result["exclusion_counts"],{"calendar_date_unavailable":1})

    def test_12_latest_failure_retains_previous_success_and_marks_age(self):
        a=append(self.root,label="success")
        fail=copy.deepcopy(a["manifest"])
        fail.update(receipt_id="synthetic-failure",fetched_at_utc="2024-01-08T21:00:00Z",status="access_restricted",http_status=403,
                    raw_path=None,sha256=None,file_size_bytes=0,normalized_path=None,normalized_sha256=None)
        write_json(self.root/"manifest/failure.json",fail)
        report=self.ingest(); self.assertEqual(report["counts"]["source_failure_receipts"],1)
        result=self.q("observed",cutoff=LATE)
        self.assertEqual(self.closes(result),[100]); self.assertTrue(result["data"][0]["stale_receipt"])
        self.assertEqual(result["latest_access_failures"][0]["status"],"access_restricted")
        self.assertTrue(result["latest_access_failures"][0]["previous_success_retained"])

    def test_13_transaction_interruption_and_idempotent_resume(self):
        append(self.root,label="one"); append(self.root,label="two",symbol="SYN2")
        with self.assertRaises(KeyboardInterrupt): self.ingest(interrupt_after=1)
        self.assertEqual(self.db.counts()["raw_receipts"],1)
        self.assertEqual(self.db.counts()["daily_price_versions"],1)
        self.assertEqual(self.db.conn.execute("SELECT status FROM ingest_runs").fetchone()[0],"INTERRUPTED")
        report=self.ingest(); counts=self.db.counts()
        self.assertEqual(report["status"],"PASS"); self.assertEqual(counts["daily_price_versions"],2)
        self.ingest(); self.assertEqual(self.db.counts(),counts)
        self.assertEqual(self.db.audit()["status"],"PASS")

    def test_14_new_correction_normalizer_and_fixed_snapshot_are_independent(self):
        append(self.root,label="one"); self.ingest()
        initial=self.q(cutoff=LATE); snap=create_snapshot(self.db,initial)
        append(self.root,label="late-correction",close=110,published="2024-01-05T21:05:00Z",
               received="2024-01-05T21:06:00Z",usable="2024-01-05T21:07:00Z",normalizer="synthetic-normalizer-v2")
        self.ingest()
        self.assertEqual(self.closes(self.q(cutoff=LATE)),[110])
        replay=replay_snapshot(self.db,snap["snapshot_id"])
        self.assertEqual(replay["result"],initial)
        self.assertEqual(sha256(canonical(replay["result"])),snap["content_sha256"])
        with self.assertRaises(sqlite3.IntegrityError): self.db.conn.execute("UPDATE daily_price_versions SET close=0")

    def test_15_future_evidence_and_mapping_leave_existing_snapshot_unchanged(self):
        append(self.root,label="one"); self.ingest(); result=self.q()
        snap=create_snapshot(self.db,result)
        append(self.root,label="future",close=200,published="2024-02-01T21:05:00Z",
               received="2024-02-01T21:06:00Z",usable="2024-02-01T21:07:00Z",
               mappings=[{"symbol":"FUT","exchange":"XNYS","effective_from":"2020-01-01"}])
        self.ingest()
        self.assertEqual(self.closes(self.q()),[100])
        self.assertEqual(replay_snapshot(self.db,snap["snapshot_id"])["result"],result)
        self.assertTrue(create_snapshot(self.db,result)["reused"])

    def test_16_assumed_snapshot_export_never_claims_strict_pit(self):
        append(self.root,label="date",published=None,publication_date="2024-01-03",proof=False); self.ingest()
        result=self.q("research_assumed",cutoff=LATE,assumption_rule=RULE)
        snap=create_snapshot(self.db,result); replay=replay_snapshot(self.db,snap["snapshot_id"])
        self.assertEqual(replay["result"]["strictness"],"NOT_STRICT_PIT")
        self.assertEqual(replay["result"]["assumptions"],RULE)
        self.assertIn("NOT_STRICT_PIT",json.dumps(replay))

    def test_17_missing_hash_corruption_and_schema_failure_are_quarantined(self):
        for mode in ("missing_raw","bad_raw","bad_normalized","missing_column"):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as t:
                root=Path(t)/"synthetic"; root.mkdir(); a=append(root,label=mode)
                m=a["manifest"]
                if mode=="missing_raw": (root/m["raw_path"]).unlink()
                if mode=="bad_raw": (root/m["raw_path"]).write_bytes(b"corrupt")
                if mode=="bad_normalized": (root/m["normalized_path"]).write_bytes(b"corrupt")
                if mode=="missing_column":
                    data=a["normalized"]; del data["bars"][0]["close"]
                    write_json(root/m["normalized_path"],data)
                    m["normalized_sha256"]=sha256((root/m["normalized_path"]).read_bytes()); write_json(a["manifest_path"],m)
                with Database(Path(t)/"test.sqlite",initialize=True) as db:
                    report=import_artifacts(db,root,domain="synthetic")
                    self.assertEqual(report["status"],"PARTIAL"); self.assertEqual(report["quarantined"],1)
                    self.assertEqual(db.counts()["daily_price_versions"],0)

    def test_18_legacy_processing_unknown_not_backdated_and_reprocessing_not_receipt(self):
        first=append(self.root,label="old",legacy_hash=True,legacy_processing=True)
        append(self.root,label="reparse",raw_body=first["raw"],network=False,normalizer="new-normalizer",
               usable="2024-01-06T21:00:00Z")
        report=self.ingest(); self.assertEqual(report["quarantined"],0)
        self.assertEqual(self.db.counts()["raw_receipts"],1)
        self.assertEqual(self.db.counts()["normalized_artifacts"],2)
        self.assertEqual(self.q("observed")["selected_count"],0)
        result=self.q("observed",cutoff=LATE)
        self.assertEqual(result["selected_count"],1)
        link=self.db.conn.execute("SELECT * FROM artifact_receipts WHERE parsing_completed_at IS NULL").fetchone()
        self.assertEqual(link["usable_at"],"2024-01-10T23:00:00.000000Z")
        self.assertEqual(link["usable_evidence"],"actual_import_validation_legacy_processing_unknown")

    def test_19_future_prices_and_unknown_price_semantics_are_excluded(self):
        append(self.root,label="future",date="2024-02-01")
        append(self.root,label="unknown",symbol="UNKNOWN",meaning="mystery")
        self.ingest(); result=self.q("observed")
        self.assertEqual(result["selected_count"],0)
        self.assertEqual(result["exclusion_counts"],{"future_trading_date":1,"price_semantics_not_requested":1})

    def test_20_timestamp_without_version_bound_evidence_cannot_pass_historical(self):
        append(self.root,label="no-proof",proof=False); self.ingest()
        self.assertEqual(self.q()["exclusion_counts"],{"publication_version_unproven":1})
        self.assertEqual(self.q("research_assumed",assumption_rule=RULE)["selected_count"],1)

    def test_21_withdrawal_respects_time_and_dividends_do_not_become_cashflows(self):
        action={"event_type":"dividends","event_date":"2024-01-04","amount":0.01,
                "amount_semantics":"provider_reported_split_basis_unverified"}
        append(self.root,label="action",actions=[action]); self.ingest()
        initial=self.q(family="actions"); self.assertEqual(initial["selected_count"],1)
        event=initial["data"][0]
        self.assertEqual(event["ex_date"],"2024-01-04"); self.assertIsNone(event["payment_date"])
        self.assertEqual(event["amount_semantics"],"provider_reported_split_basis_unverified")
        append(self.root,label="withdraw",close=100,published="2024-01-05T21:05:00Z",
               received="2024-01-05T21:06:00Z",usable="2024-01-05T21:07:00Z",extra={"record_state":"withdrawn"})
        self.ingest()
        self.assertEqual(self.q()["selected_count"],1)
        self.assertEqual(self.q(cutoff=LATE)["selected_count"],0)

    def test_22_identity_requirements_and_synthetic_domain_do_not_mix(self):
        append(self.root,label="temporary"); self.ingest()
        strict=query(self.db,cutoff=EARLY,policy="observed",symbol_mode="provider_label",input_domain="synthetic")
        self.assertEqual(strict["exclusion_counts"],{"security_identity_unverified":1})
        self.assertEqual(query(self.db,cutoff=EARLY,policy="observed",identifier_requirement="temporary_allowed",symbol_mode="provider_label")["selected_count"],0)
        self.assertIn("SYNTHETIC_TEST_ONLY",json.dumps(self.q()))

    def test_23_snapshot_evidence_tamper_is_detected(self):
        a=append(self.root,label="one"); self.ingest(); snap=create_snapshot(self.db,self.q())
        (self.root/a["manifest"]["raw_path"]).write_bytes(b"changed")
        self.assertEqual(self.db.audit()["status"],"FAIL")
        with self.assertRaisesRegex(ValueError,"snapshot_evidence_file_missing_or_corrupt"):
            replay_snapshot(self.db,snap["snapshot_id"])

    def test_24_empty_read_is_normal_but_require_data_fails_and_does_not_write(self):
        before=self.db.counts(); self.assertEqual(self.q()["status"],"EMPTY"); self.assertEqual(self.db.counts(),before)
        dbpath=str(self.db.path)
        with patch("builtins.print"):
            code=cli(["db-query","--db",dbpath,"--cutoff",EARLY,"--policy","historical_verified","--require-data"])
        self.assertEqual(code,1)

    def test_25_migration_foreign_keys_and_failure_response_are_real_constraints(self):
        self.db.migrate(); self.assertEqual(self.db.conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0],2)
        self.assertEqual(self.db.audit()["status"],"PASS")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.insert("manifest_links",{"manifest_sha256":"x","manifest_path":"x","receipt_id":"absent",
                           "manifest_json":"{}","run_id":"absent"})
        a=append(self.root,label="http-failure")
        m=a["manifest"]; m.update(status="access_restricted",http_status=403); write_json(a["manifest_path"],m)
        self.ingest(); self.assertEqual(self.db.counts()["daily_price_versions"],0)
        self.assertEqual(self.db.counts()["raw_receipts"],1)

    def test_26_reusing_receipt_id_for_different_content_is_not_deduplication(self):
        a=append(self.root,label="one"); self.ingest()
        b=append(self.root,label="two",close=110)
        m=b["manifest"]; m["receipt_id"]=a["manifest"]["receipt_id"]; write_json(b["manifest_path"],m)
        report=self.ingest()
        self.assertEqual(report["quarantine_reasons"],{"receipt_id_reused_with_different_event":1})
        self.assertEqual(self.db.counts()["raw_receipts"],1)

    def test_27_wrong_content_version_proof_cannot_make_current_values_historical(self):
        a=append(self.root,label="wrong-version")
        proof=a["row"]["publication_evidence"]
        evidence=json.loads((self.root/proof["path"]).read_text())
        evidence["version_raw_sha256"]="0"*64
        write_json(self.root/proof["path"],evidence)
        data=a["normalized"]; data["bars"][0]["publication_evidence"]["sha256"]=sha256((self.root/proof["path"]).read_bytes())
        m=a["manifest"]; write_json(self.root/m["normalized_path"],data)
        m["normalized_sha256"]=sha256((self.root/m["normalized_path"]).read_bytes()); write_json(a["manifest_path"],m)
        report=self.ingest()
        self.assertEqual(report["quarantine_reasons"],{"publication_evidence_wrong_version":1})
        self.assertEqual(self.db.counts()["daily_price_versions"],0)

    def test_28_normalizer_fork_same_receipt_time_is_conflict_not_new_provider_revision(self):
        a=append(self.root,label="one"); self.ingest(); initial=self.q(); snap=create_snapshot(self.db,initial)
        append(self.root,label="new-parser",close=150,raw_body=a["raw"],normalizer="synthetic-normalizer-v2")
        self.ingest()
        self.assertEqual(self.q()["selected_count"],0); self.assertEqual(self.q()["conflict_count"],1)
        self.assertEqual(replay_snapshot(self.db,snap["snapshot_id"])["result"],initial)

    def test_29_providers_remain_separate_with_visible_disagreement(self):
        append(self.root,label="provider-a",source="synthetic_a",close=100)
        append(self.root,label="provider-b",source="synthetic_b",close=110)
        self.ingest(); result=self.q()
        self.assertEqual(sorted(self.closes(result)),[100,110])
        self.assertEqual(result["conflict_count"],1)
        self.assertEqual(result["conflicts"][0]["kind"],"cross_provider_disagreement")

    def test_30_domain_mismatch_and_secret_parameters_are_not_stored(self):
        a=append(self.root,label="domain")
        report=import_artifacts(self.db,self.root,domain="market")
        self.assertEqual(report["quarantine_reasons"],{"input_domain_mismatch":1})
        m=a["manifest"]; m["request_parameters"]["apikey"]="SYNTHETIC-SECRET-CANARY"
        write_json(a["manifest_path"],m)
        report=self.ingest(); self.assertEqual(report["quarantine_reasons"],{"unsafe_manifest_parameters":1})
        self.assertEqual(self.db.counts()["raw_receipts"],0)
        self.assertNotIn("SYNTHETIC-SECRET-CANARY",self.db.path.read_bytes().decode(errors="ignore"))

    def test_31_duplicate_and_wrong_type_corporate_actions_do_not_enter_store(self):
        action={"event_type":"dividends","event_date":"2024-01-04","amount":"cash"}
        append(self.root,label="bad-action",actions=[action])
        report=self.ingest(); self.assertEqual(report["quarantine_reasons"],{"invalid_corporate_action_type":1})
        self.assertEqual(self.db.counts()["corporate_action_versions"],0)

    def test_32_initial_version_arriving_after_correction_has_append_only_lineage(self):
        append(self.root,label="correction-first",close=110,published="2024-01-05T21:05:00Z",
               received="2024-01-05T21:06:00Z",usable="2024-01-05T21:07:00Z")
        self.ingest()
        append(self.root,label="initial-late",close=100,published="2024-01-02T21:05:00Z",
               received="2024-01-09T21:06:00Z",usable="2024-01-09T21:07:00Z")
        self.ingest(); result=self.q(cutoff=LATE)
        self.assertEqual(self.closes(result),[110])
        self.assertEqual(len(result["data"][0]["version_lineage"]),1)
        self.assertEqual(self.closes(self.q()),[100])
        self.assertEqual(self.closes(self.q("observed",cutoff=LATE)),[110])

    def test_33_missing_immutability_guard_and_changed_value_hash_are_detected(self):
        append(self.root,label="one"); self.ingest(); snap=create_snapshot(self.db,self.q())
        self.db.conn.execute("DROP TRIGGER daily_price_versions_no_update")
        self.db.conn.execute("UPDATE daily_price_versions SET close=101")
        codes={r["reason"] for r in self.db.audit()["failures"]}
        self.assertTrue({"missing_immutable_guard","version_value_hash_mismatch"}.issubset(codes))
        with self.assertRaisesRegex(ValueError,"snapshot_version_mismatch"): replay_snapshot(self.db,snap["snapshot_id"])

    def test_34_empty_snapshot_require_data_returns_failure(self):
        snap=create_snapshot(self.db,self.q())
        with patch("builtins.print"):
            code=cli(["snapshot-replay","--db",str(self.db.path),"--snapshot-id",snap["snapshot_id"],"--require-data"])
        self.assertEqual(code,1)

    def test_35_real_migration_upgrade_preserves_existing_receipt(self):
        from market_research.pit.migrations import MIGRATIONS
        from market_research.pit.database import IMPORTER_VERSION
        self.assertEqual(IMPORTER_VERSION,"2.0.0")
        # Verify that a version-1 database is genuinely upgraded by the migration runner.
        old_path=Path(self.tmp.name)/"old.sqlite"
        c=sqlite3.connect(old_path)
        sql=MIGRATIONS[0][1]; c.executescript(sql)
        c.execute("INSERT INTO schema_migrations VALUES(1,?,?)",(sha256(sql.encode()),"2024-01-01T00:00:00Z"))
        c.execute("""INSERT INTO raw_receipts(receipt_id,source,request_id,request_json,receipt_evidence,status,input_domain,ingested_at)
                  VALUES('old-receipt','synthetic','old-request','{}','synthetic','http_error','synthetic','2024-01-01T00:00:00Z')""")
        c.commit(); c.close()
        with self.assertRaises(ValueError): Database(old_path)
        with Database(old_path,initialize=True) as old:
            self.assertEqual(old.conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0],2)
            self.assertIsNotNone(old.conn.execute("SELECT name FROM sqlite_master WHERE name='version_edges'").fetchone())
            self.assertEqual(old.conn.execute("SELECT receipt_id FROM raw_receipts").fetchone()[0],"old-receipt")

    def test_36_reprocess_uses_explicit_origin_instead_of_arbitrary_equal_receipt(self):
        a=append(self.root,label="receipt-a")
        b=append(self.root,label="receipt-b",raw_body=a["raw"])
        c=append(self.root,label="reprocess",raw_body=a["raw"],network=False,normalizer="synthetic-normalizer-v2")
        m=c["manifest"]; m["origin_receipt_id"]=b["manifest"]["receipt_id"]; write_json(c["manifest_path"],m)
        report=self.ingest(); self.assertEqual(report["quarantined"],0)
        link=self.db.conn.execute("""SELECT l.receipt_id FROM artifact_receipts l JOIN normalized_artifacts a USING(artifact_id)
                                    WHERE a.normalizer_code_sha256='synthetic-normalizer-v2'""").fetchone()
        self.assertEqual(link[0],"synthetic-receipt-receipt-b")
        self.assertEqual(self.db.counts()["raw_receipts"],2)

    def test_37_missing_reprocess_raw_reference_is_quarantined(self):
        a=append(self.root,label="one"); self.ingest()
        c=append(self.root,label="reprocess",raw_body=a["raw"],network=False,normalizer="synthetic-normalizer-v2")
        m=c["manifest"]; m["raw_path"]="raw/nonexistent.bin"; write_json(c["manifest_path"],m)
        report=self.ingest(); self.assertEqual(report["quarantine_reasons"],{"missing_file":1})
        self.assertEqual(self.db.counts()["normalized_artifacts"],1)

    def test_38_same_publication_conflict_is_not_ordered_by_later_download(self):
        append(self.root,label="first",close=100)
        append(self.root,label="late",close=110,received="2024-01-05T21:06:00Z",usable="2024-01-05T21:07:00Z")
        self.ingest()
        for policy in ("observed","historical_verified"):
            result=self.q(policy,cutoff=LATE)
            self.assertEqual(result["selected_count"],0); self.assertEqual(result["conflict_count"],1)

    def test_39_unknown_publication_timezone_stays_null_without_losing_observed_data(self):
        append(self.root,label="unknown-zone",published=None,publication_date="2024-01-03",proof=False,
               extra={"publication_timezone":None})
        self.assertEqual(self.ingest()["quarantined"],0)
        observed=self.q("observed",cutoff=LATE)
        self.assertEqual(self.closes(observed),[100]); self.assertIsNone(observed["data"][0]["temporal"]["publication_timezone"])
        self.assertEqual(self.q(cutoff=LATE)["selected_count"],0)
        assumed=self.q("research_assumed",cutoff=LATE,assumption_rule=RULE)
        self.assertEqual(assumed["exclusion_counts"],{"assumption_source_timezone_unknown":1})

    def test_40_integer_price_dividend_and_split_values_survive_sqlite_real_affinity(self):
        append(self.root,label="integer-numbers",extra={"open":100,"high":101,"low":99,"close":100},
               actions=[{"event_type":"splits","event_date":"2024-01-03","numerator":2,"denominator":1},
                        {"event_type":"dividends","event_date":"2024-01-04","amount":1}])
        self.assertEqual(self.ingest()["quarantined"],0)
        self.assertEqual(self.db.audit()["status"],"PASS")
        prices=self.q(); self.assertEqual(self.closes(prices),[100])
        actions=self.q(family="actions"); self.assertEqual(actions["selected_count"],2)
        for result in (prices,actions):
            snap=create_snapshot(self.db,result)
            self.assertEqual(replay_snapshot(self.db,snap["snapshot_id"])["result"],result)

    def test_41_integer_overflow_is_quarantined_without_stopping_valid_artifact(self):
        append(self.root,label="good")
        append(self.root,label="overflow",symbol="BIG",extra={"volume":2**80})
        report=self.ingest()
        self.assertEqual(report["status"],"PARTIAL"); self.assertEqual(report["quarantined"],1)
        self.assertEqual(self.closes(self.q()),[100])
        self.assertEqual(self.db.counts()["daily_price_versions"],1)


if __name__ == "__main__": unittest.main()
