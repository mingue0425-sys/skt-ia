"""Independent cash/interval/metric expectations, plus frozen Stage3 -> Stage4 replay."""
import copy
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from market_research.pit import Database
from market_research.pit.time import timestamp
from market_research.datasets.store import DatasetStore, iter_dataset_rows, replay_dataset
from market_research.stage4.inputs import key, select_at, load_fixed, eligibility_report
from market_research.stage4.splits import make_splits, plans
from market_research.stage4.predictions import validate_predictions
from market_research.stage4.metrics import evaluate, rank_ic
from market_research.stage4.simulation import simulate
from market_research.stage4.runs import execute, save_run, read_run, replay_run
from market_research.stage4.tape import frozen_tape
from market_research.pit import replay_snapshot
from unittest.mock import patch
from market_research.stage4.cli import exit_code
from stage4_fixture_builder import story


def day(i): return (date(2024, 1, 1)+timedelta(days=i)).isoformat()
def clock(i, hour): return day(i)+f"T{hour:02}:00:00Z"


def account_fixture(horizon=1, initial="1000", end=3):
    tape = {"input_domain": "synthetic", "calendar": [{"trading_date": day(i), "open_utc": clock(i, 9), "close_utc": clock(i, 16)} for i in range(26)],
            "bars": [{"symbol": "S", "date": day(i), "open": 100, "close": 100, "volume": 1000,
                      "available_at": clock(i, 17), "currency": "USD", "price_semantics": "as_traded", "session_scope": "synthetic_regular"} for i in range(26)],
            "actions": [], "coverage": {"status": "verified_complete", "same_day_order": "split_then_dividend_postsplit", "start_date": day(0), "end_date": day(25)},
            "trading_state": {"listed_from": day(0), "verified_through": day(25), "halted_dates": [], "delisted_date": None}}
    signal = {"prediction_id": "p", "security_id": "S", "horizon": horizon, "predicted_return": 0.02,
              "generation_kind": "replay", "generated_at": "2026-10-02T12:00:00Z", "simulated_generated_at": clock(0, 17),
              "decision_at": clock(0, 17), "intended_entry_at": clock(1, 9), "tradable": True}
    config = {"policy_version": "4.0.0", "horizon": horizon, "currency": "USD", "initial_cash": initial,
              "start_at": clock(0, 8), "end_at": clock(end, 16), "costs": {}, "insufficient_cash_policy": "partial_cancel"}
    return tape, [signal], config


class AccountTests(unittest.TestCase):
    def run_account(self, tape, signals, cfg): return simulate(tape, signals, cfg, "synthetic")

    def test_constant_price_no_wealth_created(self):
        r = self.run_account(*account_fixture())
        self.assertEqual(r["terminal"]["cash"], "1000.00")
        self.assertEqual(r["performance"]["total_return"], 0)
        self.assertEqual(r["performance"]["fill_count"], 2)

    def test_buy_sell_commission_hand_cash(self):
        tape, signals, cfg = account_fixture(initial="1001")
        cfg["costs"] = {"commission_fixed": "1"}
        tape["bars"][2]["open"] = 110
        r = self.run_account(tape, signals, cfg)
        self.assertEqual([f["cash_after"] for f in r["fills"]], ["0.00", "1099.00"])
        self.assertEqual(r["performance"]["costs"]["commission"], "2.00")
        self.assertAlmostEqual(r["performance"]["total_return"], 98/1001)

    def test_rising_falling_nav_and_drawdown(self):
        tape, signals, cfg = account_fixture()
        tape["bars"][1]["close"] = 110
        tape["bars"][2]["open"] = 90
        r = self.run_account(tape, signals, cfg)
        self.assertEqual([float(x["nav"]) for x in r["daily_nav"]], [1000, 1100, 900, 900])
        self.assertAlmostEqual(r["performance"]["max_drawdown"], 200/1100)
        self.assertAlmostEqual(r["performance"]["total_return"], -0.1)

    def test_split_wealth_continuity_and_pending_order_adjustment(self):
        tape, signals, cfg = account_fixture()
        tape["actions"] = [{"action_id": "split", "symbol": "S", "type": "split", "date": day(1), "numerator": 2, "denominator": 1}]
        for b in tape["bars"][1:]: b.update(open=50, close=50)
        r = self.run_account(tape, signals, cfg)
        self.assertEqual(r["orders"][0]["quantity"], "20")
        self.assertEqual(r["orders"][0]["sizing_price"], "50")
        self.assertEqual(r["terminal"]["cash"], "1000.00")
        # A held split is independently checked, too.
        tape["actions"][0]["date"] = day(2); tape["bars"][1].update(open=100, close=100)
        r = self.run_account(tape, signals, cfg)
        self.assertEqual([f["quantity"] for f in r["fills"]], ["10", "20"])
        self.assertTrue(all(x["nav"] == "1000.00" for x in r["daily_nav"]))

    def test_fractional_split_quantity_preserved(self):
        tape, signals, cfg = account_fixture(initial="300", horizon=5, end=2)
        tape["actions"] = [{"action_id": "reverse", "symbol": "S", "type": "split", "date": day(2), "numerator": 1, "denominator": 2}]
        tape["bars"][2].update(open=200, close=200)
        r = self.run_account(tape, signals, cfg)
        self.assertEqual(r["terminal"]["positions"][0]["quantity"], "1.5")
        self.assertEqual(float(r["terminal"]["nav"]), 300)
        self.assertEqual(len(r["fills"]), 1)

    def test_dividend_right_receivable_payment_no_duplicates(self):
        tape, signals, cfg = account_fixture(horizon=5, end=6)
        a = {"action_id": "div", "symbol": "S", "type": "dividend", "date": day(2), "amount": 2,
             "payment_date": day(3), "currency": "USD", "amount_semantics": "as_declared_cash_per_share"}
        tape["actions"] = [a, copy.deepcopy(a)]
        r = self.run_account(tape, signals, cfg)
        self.assertEqual(float(r["daily_nav"][2]["dividend_receivables"]), 20)
        self.assertEqual(float(r["daily_nav"][3]["dividend_receivables"]), 20)  # Same date payment remains receivable.
        self.assertEqual(float(r["daily_nav"][4]["cash"]), 20)
        self.assertEqual(r["terminal"]["cash"], "1020.00")
        self.assertEqual([e["type"] for e in r["corporate_action_ledger"]], ["dividend_right", "dividend_payment"])

    def test_ex_date_entry_has_no_dividend_right(self):
        tape, signals, cfg = account_fixture()
        tape["actions"] = [{"action_id": "div", "symbol": "S", "type": "dividend", "date": day(1), "amount": 2,
                            "payment_date": day(1), "currency": "USD", "amount_semantics": "as_declared_cash_per_share"}]
        r = self.run_account(tape, signals, cfg)
        self.assertEqual(r["terminal"]["cash"], "1000.00")

    def test_insufficient_cash_partial_and_reject(self):
        tape, signals, cfg = account_fixture()
        tape["bars"][1]["open"] = 150
        r = self.run_account(tape, signals, cfg)
        self.assertEqual(r["fills"][0]["quantity"], "6")
        self.assertEqual(r["fills"][0]["cash_after"], "100.00")
        self.assertEqual(r["orders"][0]["cancelled_quantity"], "4")
        self.assertEqual(r["orders"][0]["history"], ["created", "pending", "partially_filled", "cancelled"])
        cfg["insufficient_cash_policy"] = "reject"
        r = self.run_account(tape, signals, cfg)
        self.assertEqual(r["orders"][0]["status"], "rejected")
        self.assertEqual(r["fills"], [])
        self.assertEqual(float(r["terminal"]["nav"]), 1000)

    def test_expired_entry_does_not_affect_holdings_or_nav(self):
        tape, signals, cfg = account_fixture()
        tape["bars"][1]["open"] = None
        r = self.run_account(tape, signals, cfg)
        self.assertEqual(r["orders"][0]["status"], "expired")
        self.assertEqual(r["terminal"]["positions"], [])
        self.assertEqual(float(r["terminal"]["nav"]), 1000)

    def test_repeated_held_signal_deferred(self):
        tape, signals, cfg = account_fixture(horizon=5, end=6)
        signals.append(signals[0] | {"prediction_id": "p2", "simulated_generated_at": clock(1, 17), "intended_entry_at": clock(2, 9)})
        r = self.run_account(tape, signals, cfg)
        self.assertEqual(r["deferred_signals"][0]["reason"], "already_holding_or_pending_entry")
        self.assertEqual(len(r["fills"]), 2)

    def test_halt_blocks_entry_and_exit(self):
        tape, signals, cfg = account_fixture()
        tape["trading_state"]["halted_dates"] = [day(1)]
        r = self.run_account(tape, signals, cfg)
        self.assertEqual(r["orders"][0]["reason"], "security_halted")
        self.assertEqual(r["fills"], [])
        tape["trading_state"]["halted_dates"] = [day(2)]
        r = self.run_account(tape, signals, cfg)
        self.assertEqual(r["terminal"]["positions"][0]["quantity"], "10")
        self.assertIsNone(r["performance"]["total_return"])

    def test_missing_exit_and_delisting_preserve_position(self):
        for cause in ("missing", "delisted"):
            tape, signals, cfg = account_fixture()
            if cause == "missing": tape["bars"][2].update(open=None, close=None)
            else: tape["trading_state"]["delisted_date"] = day(2)
            r = self.run_account(tape, signals, cfg)
            self.assertEqual(len(r["fills"]), 1)
            self.assertEqual(len(r["terminal"]["positions"]), 1)
            self.assertIsNone(r["performance"]["total_return"])
            self.assertEqual(r["performance_grade"], "CONDITIONAL")

    def test_spread_slippage_embedded_only_once(self):
        tape, signals, cfg = account_fixture()
        cfg["costs"] = {"half_spread_bps": 50, "slippage_bps": 50}
        r = self.run_account(tape, signals, cfg)
        self.assertEqual([f["execution_price"] for f in r["fills"]], ["101.000000", "99.000000"])
        self.assertEqual(r["terminal"]["cash"], "982.00")  # 9 shares * 2 cost, no second debit.
        self.assertEqual(float(r["performance"]["costs"]["execution_difference"]), 18)
        self.assertEqual(float(r["performance"]["costs"]["commission"]), 0)

    def test_future_daily_volume_not_used_for_sizing(self):
        tape, signals, cfg = account_fixture()
        tape["bars"][0]["volume"] = 10
        cfg["max_prior_volume_participation"] = "0.1"
        for volume in (1, 999999999):
            tape["bars"][1]["volume"] = volume
            r = self.run_account(tape, signals, cfg)
            self.assertEqual(r["orders"][0]["quantity"], "1")
            self.assertEqual(r["orders"][0]["prior_volume"], "10")

    def test_one_five_twenty_exit_session_indices(self):
        for h in (1, 5, 20):
            r = self.run_account(*account_fixture(horizon=h, end=22))
            self.assertEqual(r["fills"][1]["at"], timestamp(clock(1+h, 9)))

    def test_pending_orders_and_terminal_positions_retained(self):
        tape, signals, cfg = account_fixture()
        cfg["end_at"] = clock(0, 18)
        r = self.run_account(tape, signals, cfg)
        self.assertEqual(len(r["pending_orders"]), 1)
        self.assertEqual(r["terminal"]["positions"], [])
        self.assertEqual(float(r["terminal"]["nav"]), 1000)
        cfg["end_at"] = clock(1, 16)
        r = self.run_account(tape, signals, cfg)
        self.assertEqual(r["terminal"]["positions"][0]["quantity"], "10")
        self.assertEqual(len(r["fills"]), 1)

    def test_late_generated_signal_no_entry(self):
        tape, signals, cfg = account_fixture()
        signals[0].update(simulated_generated_at=clock(1, 10), tradable=False)
        r = self.run_account(tape, signals, cfg)
        self.assertEqual(r["fills"], [])
        self.assertEqual(r["deferred_signals"][0]["reason"], "generated_outside_decision_entry_window")

    def test_cost_scenario_changes_affordable_quantity(self):
        tape, signals, cfg = account_fixture()
        baseline = self.run_account(tape, signals, cfg)
        cfg["costs"] = {"commission_fixed": 1, "impact_bps_at_full_prior_volume": 20, "sell_tax_bps": 10, "other_fixed": 1}
        adverse = self.run_account(tape, signals, cfg)
        self.assertEqual(baseline["fills"][0]["quantity"], "10")
        self.assertEqual(adverse["fills"][0]["quantity"], "9")
        self.assertLess(float(adverse["terminal"]["cash"]), float(baseline["terminal"]["cash"]))
        self.assertGreater(float(adverse["performance"]["costs"]["tax"]), 0)

    def test_adjusted_price_and_mode_promotion_rejected(self):
        tape, signals, cfg = account_fixture()
        with self.assertRaises(ValueError): simulate(tape, signals, cfg, "strict")
        tape["bars"][0]["price_semantics"] = "split_adjusted"
        with self.assertRaises(ValueError): self.run_account(tape, signals, cfg)

    def test_same_open_exits_before_entries_and_no_future_cash_sizing(self):
        tape, signals, cfg = account_fixture()
        cfg["allocation_fraction"] = "0.5"
        tape["bars"] += [b | {"symbol": "T"} for b in tape["bars"]]
        signals.append(signals[0] | {"prediction_id": "t", "security_id": "T", "simulated_generated_at": clock(1, 17), "intended_entry_at": clock(2, 9)})
        r = self.run_account(tape, signals, cfg)
        self.assertEqual([(f["side"], f["symbol"], f["quantity"]) for f in r["fills"]],
                         [("buy", "S", "5"), ("sell", "S", "5"), ("buy", "T", "2"), ("sell", "T", "2")])
        self.assertEqual(float(r["terminal"]["nav"]), 1000)

    def test_unsupported_action_does_not_erase_or_allow_new_entry(self):
        for effective in (0, 2):
            tape, signals, cfg = account_fixture()
            tape["actions"] = [{"action_id": "merger", "symbol": "S", "type": "merger", "date": day(effective)}]
            r = self.run_account(tape, signals, cfg)
            if effective == 2:
                self.assertEqual(len(r["terminal"]["positions"]), 1)
                self.assertEqual(len(r["fills"]), 1)
            else: self.assertEqual(len(r["fills"]), 0)
            self.assertEqual(r["performance_grade"], "CONDITIONAL")

    def test_same_corporate_event_different_versions_cannot_double_apply(self):
        tape, signals, cfg = account_fixture()
        a = {"action_id": "v1", "event_key": "economic-split", "symbol": "S", "type": "split", "date": day(2), "numerator": 2, "denominator": 1}
        tape["actions"] = [a, a | {"action_id": "v2"}]
        with self.assertRaisesRegex(ValueError, "duplicate_corporate_action_event_versions"):
            self.run_account(tape, signals, cfg)


class FrozenValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.pit = Database(Path(cls.tmp.name)/"pit.sqlite", initialize=True)
        cls.store = DatasetStore(Path(cls.tmp.name)/"datasets.sqlite")
        cls.config, cls.predictions = story(cls.pit, cls.store, Path(cls.tmp.name)/"source", "2026-10-02T15:00:00Z")
        cls.did = cls.config["dataset_snapshot_id"]
        cls.rows = list(iter_dataset_rows(cls.store, cls.pit, cls.did))

    @classmethod
    def tearDownClass(cls):
        cls.store.close(); cls.pit.close(); cls.tmp.cleanup()

    def splits(self, rows=None, spec=None, did=None):
        return make_splits(self.store, self.pit, did or self.did, rows or self.rows, spec or self.config["split"], "synthetic")

    def test_same_date_same_partition_and_rolling_expanding(self):
        rows = self.rows + [copy.deepcopy(r) | {"sample_id": "second-"+r["sample_id"]} for r in self.rows]
        for kind in ("expanding", "rolling"):
            spec = self.config["split"] | {"kind": kind, "test_dates": 1, "step_dates": 1}
            p = plans(rows, spec)
            self.assertGreater(len(p), 1)
            self.assertEqual(p[0]["train"][0], p[1]["train"][0]) if kind == "expanding" else self.assertNotEqual(p[0]["train"][0], p[1]["train"][0])
            for plan in p:
                assignments = {}
                for r in rows:
                    d = r["feature"]["decision_at"][:10]
                    for part in ("train", "validation", "test"):
                        if plan[part][0] <= d <= plan[part][1]: assignments.setdefault(d, set()).add(part)
                self.assertTrue(all(len(parts) == 1 for parts in assignments.values()))

    def test_actual_label_boundary_purge(self):
        spec = copy.deepcopy(self.config["split"])
        spec.update(horizon=20, gap_dates=0)
        r = self.splits(spec=spec)
        self.assertEqual(r["status"], "INSUFFICIENT_DATA")
        self.assertGreater(r["folds"][0]["exclusion_counts"]["label_interval_overlaps_validation"], 0)

    def test_training_asof_blocks_future_maturity_and_correction(self):
        asof = self.predictions[0]["training_asof"]
        before, _ = select_at(self.store, self.pit, self.did, self.rows, asof, "synthetic")
        self.assertTrue(before)
        old = before[0]
        corrected = copy.deepcopy(old["label"])
        corrected.update(label_available_at="2024-06-02T00:00:00Z", price_return=0.9)
        lid = self.store.add_label(corrected)
        items = [{k: r[k] for k in ("sample_id", "horizon_sessions", "feature_version_id", "label_version_id")} for r in self.rows]
        for item in items:
            if key(item) == key(old): item["label_version_id"] = lid
        metadata = replay_dataset(self.store, self.pit, self.did)["metadata"]
        new = self.store.freeze(items, metadata)["snapshot_id"]
        newrows = list(iter_dataset_rows(self.store, self.pit, new))
        after, report = select_at(self.store, self.pit, new, newrows, asof, "synthetic")
        self.assertNotIn(key(old), {key(r) for r in after})
        self.assertGreater(report["exclusion_counts"]["label_not_available_at_asof"], 0)
        self.assertEqual(next(r for r in iter_dataset_rows(self.store, self.pit, self.did) if key(r) == key(old))["label"]["price_return"], 0)

    def test_empty_and_insufficient_fold_no_metrics(self):
        cfg = copy.deepcopy(self.config)
        cfg["split"]["train_dates"] = 100
        result = execute(self.pit, self.store, cfg, self.predictions)
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertIsNone(result["metrics"])
        cfg = copy.deepcopy(self.config); cfg["split"]["min_samples"]["train"] = 999
        self.assertEqual(execute(self.pit, self.store, cfg, self.predictions)["status"], "INSUFFICIENT_DATA")

    def test_replay_clock_separate_late_prediction_blocked(self):
        predictions = copy.deepcopy(self.predictions)
        predictions[0]["simulated_generated_at"] = predictions[0]["intended_entry_at"]
        checked = validate_predictions(predictions, self.rows, self.did, self.did, "synthetic")
        self.assertFalse(checked["accepted"][0]["tradable"])
        self.assertNotEqual(checked["accepted"][0]["generated_at"], checked["accepted"][0]["simulated_generated_at"])

    def test_duplicate_conflict_dataset_and_probability_validation(self):
        for field, value in (("probability_up", 1.1), ("dataset_snapshot_id", "wrong"), ("predicted_quantiles", {"0.1": 1, "0.9": 0})):
            p = copy.deepcopy(self.predictions); p[0][field] = value
            self.assertEqual(validate_predictions(p, self.rows, self.did, self.did, "synthetic")["status"], "INVALID")
        p = copy.deepcopy(self.predictions); p.append(p[0] | {"predicted_return": 0.7})
        report = validate_predictions(p, self.rows, self.did, self.did, "synthetic")
        self.assertIn("conflicting_prediction_id", report["exclusion_counts"])
        self.assertFalse(any(x["prediction_id"] == p[0]["prediction_id"] for x in report["accepted"]))

    def test_probability_rank_missing_and_quantiles_hand_values(self):
        checked = validate_predictions(self.predictions, self.rows, self.did, self.did, "synthetic")
        result = evaluate(self.rows, checked["accepted"], evaluation_asof=self.config["evaluation_asof"], mode="synthetic", horizon=1)
        self.assertEqual(result["count"], 4)
        self.assertAlmostEqual(result["metrics_row_weighted"]["mae"]["value"], 0.02)
        self.assertAlmostEqual(result["metrics_row_weighted"]["mse"]["value"], 0.0004)
        self.assertAlmostEqual(result["metrics_row_weighted"]["brier"]["value"], 0.36)
        import math
        self.assertAlmostEqual(result["metrics_row_weighted"]["binary_log_loss"]["value"], -math.log(0.4))
        self.assertEqual(result["metrics_row_weighted"]["direction_accuracy"]["value"], 0)
        self.assertAlmostEqual(result["pinball_loss"]["0.1"], 0.005)
        self.assertEqual(result["interval_coverage"]["0.1:0.9"]["value"], 1)
        self.assertEqual(result["exclusion_counts"]["missing_prediction"], 10)
        self.assertIsNone(result["rank_ic_equal_date_mean"]["value"])
        self.assertAlmostEqual(rank_ic([(1, 1), (2, 2), (2, 2)])["value"], 1)
        self.assertIsNone(rank_ic([(1, 2), (1, 3)])["value"])
        early = evaluate(self.rows, checked["accepted"], evaluation_asof="2024-01-01T00:00:00Z", mode="synthetic", horizon=1)
        self.assertIsNone(early["metrics_row_weighted"]["mae"]["value"])
        self.assertGreater(early["exclusion_counts"]["immature_label"], 0)

    def test_oracle_separate_and_synthetic_not_promoted(self):
        p = [r | {"oracle": True} for r in self.predictions]
        self.assertEqual(validate_predictions(p, self.rows, self.did, self.did, "synthetic")["status"], "INVALID")
        self.assertEqual(validate_predictions(p, self.rows, self.did, self.did, "synthetic", oracle_test=True)["scope"], "ORACLE_ACCURACY_TEST_ONLY")
        cfg = self.config | {"mode": "strict"}
        r = execute(self.pit, self.store, cfg, [])
        self.assertEqual(r["status"], "BLOCKED")
        self.assertIsNone(r["metrics"])
        self.assertEqual(execute(self.pit, self.store, self.config, self.predictions)["historical_performance"], "BLOCKED")

    def test_end_to_end_fixed_run_hash_and_integrity(self):
        replay_dataset(self.store, self.pit, self.did, verify_files=True)
        root = Path(self.tmp.name)/"runs"
        first = save_run(root, self.pit, self.store, self.config, self.predictions)
        second = save_run(root, self.pit, self.store, self.config, self.predictions)
        self.assertEqual(first["result_content_sha256"], second["result_content_sha256"])
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(replay_run(first["path"], self.pit, self.store)["status"], "PASS")
        run = read_run(first["path"])
        self.assertEqual(run["result"]["metrics"]["count"], 4)
        # Four daily signals, h=1: signals during two held periods are deferred.
        self.assertEqual(run["result"]["simulation"]["performance"]["fill_count"], 4)
        self.assertEqual(run["result"]["comparisons"]["cash"]["performance"]["total_return"], 0)
        self.assertEqual(run["result"]["simulation"]["terminal"]["positions"], [])
        # 9 pre-split shares ->18; dividend 36. Two round trips cost 4 fees+3.70 execution.
        self.assertEqual(run["result"]["simulation"]["terminal"]["cash"], "1028.30")
        self.assertEqual([a["type"] for a in run["result"]["simulation"]["corporate_action_ledger"]],
                         ["split", "dividend_right", "dividend_payment"])
        with self.assertRaises(ValueError): load_fixed(self.store, self.pit, self.did, "wrong", "synthetic")

    def test_cli_exit_codes_are_distinct(self):
        self.assertEqual([exit_code({"status": s}) for s in ("OK", "INVALID", "PARTIAL", "BLOCKED", "INSUFFICIENT_DATA")], [0, 1, 2, 3, 3])

    def test_explicit_ranges_boundary_touch_and_feature_readiness(self):
        base = self.splits()["folds"][0]
        spec = {"kind": "explicit", "folds": [base["plan"]], "horizon": 1}
        rows = copy.deepcopy(self.rows)
        candidate = next(r for r in rows if key(r) == key(base["partitions"]["train"][0]))
        boundary = min(r["feature"]["decision_at"] for r in rows if base["plan"]["validation"][0] <= r["feature"]["decision_at"][:10] <= base["plan"]["validation"][1])
        candidate["label"]["intended_exit_at"] = boundary
        result = self.splits(rows=rows, spec=spec)
        self.assertEqual(result["folds"][0]["exclusion_counts"]["label_interval_overlaps_validation"], 1)
        rows = copy.deepcopy(self.rows)
        rows[0]["feature"]["feature_ready_at"] = rows[0]["feature"]["intended_entry_at"]
        _, audit = select_at(self.store, self.pit, self.did, rows, self.config["evaluation_asof"], "synthetic")
        self.assertGreater(audit["exclusion_counts"]["feature_decision_entry_exit_order"], 0)

    def test_equal_date_vs_row_weighted_endpoints_and_missing_states(self):
        source = next(r for r in self.rows if r["sample_id"] == self.predictions[0]["sample_id"] and r["horizon_sessions"] == 1)
        rows, predictions = [], []
        for i, (d, ret, forecast, prob) in enumerate((("2024-04-01", 0, 0, 0), ("2024-04-01", 0, 0, 0), ("2024-04-02", 1, 0, 1))):
            r = copy.deepcopy(source); r["sample_id"] = str(i); r["feature"]["decision_at"] = d+"T22:00:00Z"
            r["feature"]["feature_cutoff"] = d+"T21:00:00Z"; r["feature"]["feature_ready_at"] = d+"T21:01:00Z"
            r["feature"]["security_record_ids"] = [str(i)]; r["label"]["price_return"] = ret
            rows.append(r); predictions.append(self.predictions[0] | {"sample_id": str(i), "predicted_return": forecast, "probability_up": prob})
        result = evaluate(rows, predictions, evaluation_asof=self.config["evaluation_asof"], mode="synthetic", horizon=1)
        self.assertAlmostEqual(result["metrics_row_weighted"]["mae"]["value"], 1/3)
        self.assertAlmostEqual(result["metrics_equal_date_weighted"]["mae"], 0.5)
        self.assertLess(result["metrics_row_weighted"]["binary_log_loss"]["value"], 1e-12)
        rows[0]["label"]["status"] = "pending"; rows[1]["feature"]["sample_status"] = "blocked"
        result = evaluate(rows, predictions, evaluation_asof=self.config["evaluation_asof"], mode="synthetic", horizon=1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["exclusion_counts"]["label_pending"], 1)
        self.assertEqual(result["exclusion_counts"]["feature_blocked"], 1)

    def test_conflicting_action_versions_across_frozen_snapshots_rejected(self):
        fixed = replay_dataset(self.store, self.pit, self.did)
        rows = copy.deepcopy(self.rows)
        extra = copy.deepcopy(rows[0])
        original_sid = extra["label"]["action_snapshot_id"]
        extra["label"]["action_snapshot_id"] = "second-frozen-action-snapshot"
        rows.append(extra)
        def altered(pit, sid, verify_files=True):
            result = copy.deepcopy(replay_snapshot(pit, original_sid if sid == "second-frozen-action-snapshot" else sid, verify_files=verify_files))
            for action in result["result"]["data"]:
                if sid == "second-frozen-action-snapshot" and action.get("event_type") == "split":
                    action["version_id"] = "different-correction"
                    action["numerator"] = 3
            return result
        with patch("market_research.stage4.tape.replay_snapshot", side_effect=altered):
            with self.assertRaisesRegex(ValueError, "conflicting_frozen_corporate_action_versions"):
                frozen_tape(self.pit, fixed, rows)

    def test_finite_extreme_prediction_loss_overflow_is_null(self):
        row = next(r for r in self.rows if r["sample_id"] == self.predictions[0]["sample_id"] and r["horizon_sessions"] == 1)
        predictions = [self.predictions[0] | {"predicted_return": 1e308}]
        result = evaluate([row], predictions, evaluation_asof=self.config["evaluation_asof"], mode="synthetic", horizon=1)
        self.assertEqual(result["metrics_row_weighted"]["mae"]["value"], 1e308)
        self.assertIsNone(result["metrics_row_weighted"]["mse"]["value"])
        self.assertEqual(result["metrics_row_weighted"]["mse"]["reason"], "numeric_overflow")

    def test_final_eligible_membership_preserves_extra_contract_rejection(self):
        rows = copy.deepcopy(self.rows)
        row = rows[0]
        row["label"]["intended_entry_at"] = row["label"]["intended_exit_at"]
        report = eligibility_report(self.store, self.pit, self.did, rows, self.config["evaluation_asof"], "synthetic")
        self.assertIn(key(row), {key(r) for r in report["selector"]["selected"]})
        self.assertNotIn(key(row), {key(r) for r in report["eligible_membership"]})


if __name__ == "__main__": unittest.main()
