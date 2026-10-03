"""Fictional fixed dataset and deterministic signals independent of label values."""
from pathlib import Path
from market_research.datasets import build_dataset
from market_research.datasets.store import iter_dataset_rows
from market_research.stage4.inputs import key
from market_research.stage4.splits import make_splits
from market_research.stage4.predictions import PREDICTION_VERSION
from market_research.stage4.simulation import POLICY_VERSION
from market_research.datasets.calendar import plus_seconds
from market_research.pit import import_artifacts
from fixture_builder import append
from stage3_fixture_builder import market_story


def story(pit, store, root, generated_at):
    # Declaration is before the first test entry; effective split/ex-dividend is at its exit.
    calendar, cfg, sessions = market_story(pit, root, n=100)
    actions = [{"event_type": "split", "event_date": sessions[70]["trading_date"],
                "effective_date": sessions[73]["trading_date"], "numerator": 2, "denominator": 1},
               {"event_type": "dividend", "event_date": sessions[70]["trading_date"],
                "ex_date": sessions[73]["trading_date"], "payment_date": sessions[74]["trading_date"],
                "amount": 2, "currency": "USD", "amount_semantics": "as_declared_cash_per_share"}]
    for i in range(70, 100):
        s = sessions[i]
        # Explicit corrected bar versions, all available before that session's decision.
        append(root, label="stage4-"+s["trading_date"], date=s["trading_date"], close=50 if i >= 73 else 100,
               published=plus_seconds(s["close_utc"], 60), revised=plus_seconds(s["close_utc"], 240),
               received=plus_seconds(s["close_utc"], 300), usable=plus_seconds(s["close_utc"], 360),
               extra={"observation_end_utc": s["close_utc"], "session_scope": "synthetic_regular_session"},
               actions=actions if i == 70 else None)
    imported = import_artifacts(pit, root, domain="synthetic")
    if imported["quarantined"]: raise AssertionError(imported)
    cfg["samples"] = [{"decision_date": s["trading_date"]} for s in sessions[61:75]]
    fixed = build_dataset(pit, store, cfg)
    rows = list(iter_dataset_rows(store, pit, fixed["snapshot_id"]))
    spec = {"kind": "expanding", "train_dates": 6, "validation_dates": 2, "test_dates": 4,
            "step_dates": 4, "gap_dates": 1, "horizon": 1, "min_samples": {"train": 2, "validation": 1, "test": 1}}
    split = make_splits(store, pit, fixed["snapshot_id"], rows, spec, "synthetic")
    fold = split["folds"][0]
    testing = {key(r) for r in fold["partitions"]["test"]}
    records = []
    for row in rows:
        if key(row) not in testing: continue
        f = row["feature"]
        records.append({"prediction_id": "manual-"+row["sample_id"]+"-1", "sample_id": row["sample_id"],
                        "model_or_signal_id": "constant_positive_fixture", "model_version": "1", "training_asof": fold["training_asof"],
                        "generated_at": generated_at, "simulated_generated_at": f["decision_at"], "generation_kind": "replay",
                        "decision_at": f["decision_at"], "intended_entry_at": f["intended_entry_at"], "horizon": 1,
                        "predicted_return": 0.02, "probability_up": 0.6, "predicted_quantiles": {"0.1": -0.05, "0.9": 0.05},
                        "dataset_snapshot_id": fixed["snapshot_id"], "dataset_content_sha256": fixed["content_sha256"],
                        "feature_version_id": row["feature_version_id"], "feature_snapshot_id": f["feature_snapshot_id"],
                        "prediction_version": PREDICTION_VERSION, "mode": "synthetic", "oracle": False})
    test_rows = [r for r in rows if key(r) in testing]
    simulation = {"policy_version": POLICY_VERSION, "horizon": 1, "currency": "USD", "initial_cash": "1000.00",
                  "start_at": min(r["feature"]["decision_at"] for r in test_rows),
                  "end_at": max(r["label"]["intended_exit_at"] for r in test_rows)[:10]+"T23:59:00Z",
                  "allocation_fraction": "1", "max_prior_volume_participation": "0.1",
                  "insufficient_cash_policy": "partial_cancel", "repeat_signal_policy": "defer",
                  "costs": {"commission_fixed": "1", "half_spread_bps": "5", "slippage_bps": "5"}}
    config = {"mode": "synthetic", "dataset_snapshot_id": fixed["snapshot_id"], "dataset_content_sha256": fixed["content_sha256"],
              "evaluation_asof": cfg["label_asof"], "split": spec, "fold_index": 0, "simulation": simulation,
              "cost_scenarios": {"adverse_assumption": {"commission_fixed": "2", "half_spread_bps": "50", "slippage_bps": "50"}}}
    return config, records
