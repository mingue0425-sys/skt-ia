"""Separate immutable run artifacts. No Stage2/3 writes or migrations."""
import json
import platform
import sys
from pathlib import Path
from importlib.metadata import version
from ..storage import canonical, sha256, code_version, write_json
from ..http import utc_now
from ..datasets.store import DatasetStore
from ..pit import Database
from .inputs import load_fixed, eligibility_report, contract_reasons, key
from .splits import make_splits
from .predictions import validate_predictions, PREDICTION_VERSION
from .metrics import evaluate
from .tape import frozen_tape
from .simulation import simulate, POLICY_VERSION
from . import VERSION


def execute(pit, store, config, predictions, project_root="."):
    mode, did, digest = config["mode"], config["dataset_snapshot_id"], config["dataset_content_sha256"]
    fixed, rows = load_fixed(store, pit, did, digest, mode)
    eligibility = eligibility_report(store, pit, did, rows, config["evaluation_asof"], mode)
    split = make_splits(store, pit, did, rows, config["split"], mode)
    content = {"version": VERSION, "mode": mode, "dataset_snapshot_id": did, "dataset_content_sha256": digest,
               "feature_definition_versions": sorted({r["feature"]["definition_version"] for r in rows}),
               "label_definition_versions": sorted({r["label"]["definition_version"] for r in rows}),
               "eligibility": eligibility, "splits": split, "metrics": None, "simulation": None, "comparisons": None,
               "warnings": [], "errors": [], "historical_performance": "BLOCKED"}
    if mode == "strict" and eligibility["selected_count"] == 0:
        content.update(status="BLOCKED", reason="strict_dataset_has_no_eligible_samples")
        return content
    if mode == "strict": content["historical_performance"] = "CONDITIONAL"
    fold_index = int(config.get("fold_index", 0))
    if not 0 <= fold_index < len(split["folds"]) or split["folds"][fold_index]["status"] != "OK":
        content.update(status="INSUFFICIENT_DATA", reason="selected_fold_empty_or_below_minimum")
        return content
    fold = split["folds"][fold_index]
    eval_keys = {key(r) for r in fold["partitions"]["test"]}
    eval_rows = [r for r in rows if key(r) in eval_keys]
    checked = validate_predictions(predictions, rows, did, digest, mode)
    content["prediction_validation"] = checked
    if checked["status"] == "INVALID":
        content.update(status="INVALID", reason="invalid_fixed_prediction_records"); return content
    eval_preds = [p for p in checked["accepted"] if (p["sample_id"], p["horizon"]) in eval_keys]
    if any(p["training_asof"] != fold["training_asof"] for p in eval_preds):
        content.update(status="INVALID", reason="prediction_training_asof_does_not_match_fold"); return content
    if len(eval_preds) != len(eval_rows):
        content.update(status="INSUFFICIENT_DATA", reason="test_predictions_missing"); return content
    content["metrics"] = evaluate(eval_rows, eval_preds, evaluation_asof=config["evaluation_asof"], mode=mode, horizon=config["split"]["horizon"])
    # Feature-time suitability gates execution; outcome readiness does not censor simulated trades.
    # Strict eligibility is mandatory before any actual-input simulation.
    signals, rejected = [], []
    strict_eligible_keys = {key(r) for r in eligibility["eligible_membership"]}
    for p in eval_preds:
        row = next(r for r in eval_rows if key(r) == (p["sample_id"], p["horizon"]))
        reasons = [r for r in contract_reasons(row, mode) if not r.startswith("label_")]
        if mode == "strict" and key(row) not in strict_eligible_keys:
            reasons.append("strict_execution_ineligible")
        if len(row["feature"].get("security_record_ids", [])) != 1: reasons.append("single_security_identity_required")
        if reasons: rejected.append({"prediction_id": p["prediction_id"], "reasons": reasons})
        else: signals.append(p | {"security_id": row["feature"]["security_record_ids"][0]})
    content["execution_rejected_predictions"] = rejected
    if rejected:
        content.update(status="BLOCKED", reason="test_feature_or_identity_ineligible"); return content
    tape, tape_hash = frozen_tape(pit, fixed, rows)
    simulation_config = config["simulation"]
    if simulation_config["horizon"] != config["split"]["horizon"]: raise ValueError("split_simulation_horizon_mismatch")
    content["execution_tape_sha256"] = tape_hash
    content["simulation"] = simulate(tape, signals, simulation_config, mode)
    comparisons = {"cash": simulate(tape, [], simulation_config, mode)}
    for name, costs in config.get("cost_scenarios", {}).items():
        comparisons[name] = simulate(tape, signals, simulation_config | {"costs": costs}, mode)
    content["comparisons"] = comparisons
    content["cost_scenario_differences"] = {name: {"fill_count_delta": r.get("performance", {}).get("fill_count", 0)-content["simulation"].get("performance", {}).get("fill_count", 0),
                                                  "terminal_cash": r.get("terminal", {}).get("cash"),
                                                  "fill_quantities": [f["quantity"] for f in r.get("fills", [])]}
                                            for name, r in comparisons.items()}
    content["status"] = "BLOCKED" if content["simulation"]["status"] == "BLOCKED" else "PARTIAL" if content["simulation"]["status"] == "PARTIAL" or content["metrics"]["status"] != "OK" else "OK"
    return content


def manifest(config, predictions, result, project_root):
    return {"stage4_version": VERSION, "feature_versions": result["feature_definition_versions"], "label_versions": result["label_definition_versions"],
            "prediction_version": PREDICTION_VERSION, "trading_cost_action_policy_version": POLICY_VERSION,
            "mode": config["mode"], "dataset_snapshot_id": config["dataset_snapshot_id"], "dataset_content_sha256": config["dataset_content_sha256"],
            "config": config, "config_sha256": sha256(canonical(config)), "predictions_sha256": sha256(canonical(predictions)),
            "code_sha256": code_version(project_root), "environment": {"python": sys.version, "platform": platform.platform(),
            "machine": platform.machine(), "dependencies": {name: version(name) for name in ("exchange_calendars", "numpy", "pandas")}},
            "seed": None, "randomness": "none", "result_content_sha256": sha256(canonical(result))}


def save_run(root, pit, store, config, predictions, project_root="."):
    result = execute(pit, store, config, predictions, project_root)
    meta = manifest(config, predictions, result, project_root)
    run_id = sha256(canonical(meta))
    envelope = {"run_id": run_id, "manifest": meta, "result": result, "fixed_predictions": predictions,
                "execution_metadata": {"executed_at": utc_now(), "not_a_historical_generation_record": True}}
    path = Path(root)/f"{run_id}.json"
    if path.exists():
        previous = read_run(path)
        if previous["manifest"] != meta: raise ValueError("run_id_collision")
    else: write_json(path, envelope)
    return {"status": result["status"], "mode": config["mode"], "run_id": run_id, "path": str(path),
            "result_content_sha256": meta["result_content_sha256"], "historical_performance": result["historical_performance"]}


def read_run(path):
    envelope = json.loads(Path(path).read_text())
    meta = envelope["manifest"]
    if sha256(canonical(meta)) != envelope["run_id"] or sha256(canonical(envelope["result"])) != meta["result_content_sha256"] or sha256(canonical(envelope["fixed_predictions"])) != meta["predictions_sha256"] or sha256(canonical(meta["config"])) != meta["config_sha256"]:
        raise ValueError("run_artifact_hash_mismatch")
    return envelope


def replay_run(path, pit, store, project_root="."):
    old = read_run(path)
    if code_version(project_root) != old["manifest"]["code_sha256"]:
        raise ValueError("run_replay_code_changed_use_original_source")
    result = execute(pit, store, old["manifest"]["config"], old["fixed_predictions"], project_root)
    digest = sha256(canonical(result))
    return {"status": "PASS" if digest == old["manifest"]["result_content_sha256"] else "FAIL", "mode": result["mode"],
            "run_id": old["run_id"], "result_content_sha256": digest, "historical_performance": result["historical_performance"]}


def aggregate_run(envelope):
    r = envelope["result"]
    return {"status": r["status"], "mode": r["mode"], "run_id": envelope["run_id"], "manifest": envelope["manifest"],
            "result_content_sha256": envelope["manifest"]["result_content_sha256"],
            "counts": {"eligible": r["eligibility"]["selected_count"], "folds": len(r["splits"]["folds"]),
                       "evaluated": (r.get("metrics") or {}).get("count", 0),
                       "fills": (r.get("simulation") or {}).get("performance", {}).get("fill_count", 0)},
            "historical_performance": r["historical_performance"], "scope": "aggregate_fixed_run; no source market rows"}
