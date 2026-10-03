"""Offline aggregate evidence, preserving all original DBs and frozen versions."""
import io
import json
import platform
import sqlite3
import subprocess
import sys
import time
import unittest
import warnings
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import zipfile
from market_research.http import utc_now
from market_research.storage import write_json, sha256, canonical, code_version
from market_research.pit import Database, replay_snapshot
from market_research.datasets.store import DatasetStore, replay_dataset, iter_dataset_rows
from market_research.stage4.inputs import eligibility_report
from market_research.stage4.runs import execute, read_run

ROOT = Path(__file__).resolve().parents[1]


def command(argv, expected=0):
    result = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != expected:
        raise RuntimeError(f"command failed: {argv}: {result.returncode}: {result.stderr[-1500:]} {result.stdout[-1000:]}")
    return {"command": argv, "expected_returncode": expected, "returncode": result.returncode, "status": "PASS"}, result.stdout


def verify_datasets(pit_path, dataset_path):
    if not pit_path.is_file() or not dataset_path.is_file():
        return {"status": "BLOCKED", "reason": "local_fixed_input_database_missing", "count": 0}
    before = {str(p): sha256(p.read_bytes()) for p in (pit_path, dataset_path)}
    verified = []
    with Database(pit_path) as pit, DatasetStore(dataset_path) as store:
        for r in store.conn.execute("SELECT snapshot_id FROM dataset_snapshots ORDER BY snapshot_id"):
            fixed = replay_dataset(store, pit, r[0], verify_files=True)
            verified.append({"dataset_snapshot_id": r[0], "content_sha256": fixed["content_sha256"], "items": fixed["items"]})
    unchanged = all(sha256(Path(p).read_bytes()) == h for p, h in before.items())
    if not unchanged: raise AssertionError("original_database_bytes_changed")
    return {"status": "PASS", "count": len(verified), "datasets": verified, "database_bytes_unchanged": unchanged}


def actual_report():
    pit_path, dataset_path = ROOT/"data/stage3/pit_market.sqlite", ROOT/"data/stage3/datasets.sqlite"
    preserved = verify_datasets(pit_path, dataset_path)
    if preserved["status"] != "PASS": return preserved | {"historical_performance": "BLOCKED", "training_selected_count": 0}
    report = {"status": "BLOCKED", "historical_performance": "BLOCKED", "fixed_dataset_preservation": preserved,
              "evaluation_asof": "2026-10-02T14:31:42.659568Z", "datasets": [], "training_selected_count": 0}
    # Independently verify the four fixed input IDs reported by Stage3; do not rebuild queries.
    previous = json.loads((ROOT/"reports/stage3_validation.json").read_text())
    feature_counts, label_counts, reasons, exclusions = Counter(), Counter(), Counter(), Counter()
    with Database(pit_path) as pit, DatasetStore(dataset_path) as store:
        for reference in previous["real"].get("datasets", []):
            did = reference["snapshot_id"]
            fixed = replay_dataset(store, pit, did, verify_files=True)
            if fixed["content_sha256"] != reference["content_sha256"]: raise AssertionError("stage3_fixed_reference_hash_mismatch")
            rows = list(iter_dataset_rows(store, pit, did, verify_files=True))
            eligibility = eligibility_report(store, pit, did, rows, report["evaluation_asof"], "strict")
            features = {r["feature_version_id"]: r["feature"] for r in rows}
            label_queries = {r["label"].get("label_snapshot_id"): r["label"] for r in rows if r["label"].get("label_snapshot_id")}
            details = {"feature_query_exclusion_counts": dict(sum((Counter(f.get("query_exclusion_counts", {})) for f in features.values()), Counter())),
                       "label_query_exclusion_counts": dict(sum((Counter(l.get("query_exclusion_counts", {})) for l in label_queries.values()), Counter())),
                       "identifier_counts": dict(Counter(level for f in features.values() for level in (f.get("identifier_verification") or ["missing"]))),
                       "learning_rights_status_counts": dict(Counter(r["label"].get("learning_rights_status", "missing") for r in rows)),
                       "execution_status_counts": dict(Counter(f.get("execution_status", "missing") for f in features.values())),
                       "feature_ready_basis_counts": dict(Counter(f.get("feature_ready_basis", "missing") for f in features.values())),
                       "query_policy": fixed["metadata"]["config"]["feature_policy"]}
            feature_counts.update(eligibility["feature_status_counts"]); label_counts.update(eligibility["label_status_counts"])
            reasons.update(eligibility["source_reason_counts"]); exclusions.update(eligibility["exclusion_counts"])
            dates = sorted({r["feature"]["decision_at"][:10] for r in rows})
            val = (date.fromisoformat(dates[-1])+timedelta(days=1)).isoformat()
            test = (date.fromisoformat(val)+timedelta(days=1)).isoformat()
            cfg = {"mode": "strict", "dataset_snapshot_id": did, "dataset_content_sha256": did,
                   "evaluation_asof": report["evaluation_asof"], "split": {"kind": "explicit", "horizon": 1,
                   "folds": [{"train": [dates[0], dates[-1]], "validation": [val, val], "test": [test, test], "training_asof": val+"T00:00:00Z"}]}}
            result = execute(pit, store, cfg, [])
            if result["status"] != "BLOCKED" or result["simulation"] is not None or result["metrics"] is not None:
                raise AssertionError("strict_actual_input_did_not_refuse")
            write_json(ROOT/"data/stage4"/(did+"-strict.json"), cfg)
            report["datasets"].append({"dataset_snapshot_id": did, "content_sha256": did, "integrity": "PASS",
                                       "training_selected": eligibility["selected_count"], "strict_run_status": result["status"],
                                       "feature_status_counts": eligibility["feature_status_counts"], "label_status_counts": eligibility["label_status_counts"],
                                       "exclusion_counts": eligibility["exclusion_counts"], "reason_counts": eligibility["source_reason_counts"], **details})
            report["training_selected_count"] += eligibility["selected_count"]
    report.update(feature_status_counts=dict(feature_counts), label_status_counts=dict(label_counts),
                  source_reason_counts=dict(reasons), eligibility_exclusion_counts=dict(exclusions),
                  exclusion_count_semantics="multi-reason membership counts; selector aggregate and extra contract counts are distinct names",
                  current_price_diagnostics="not repeated; not an eligible past decision sample")
    source = ROOT/"data/stage2-validation.sqlite"
    if source.exists():
        digest = sha256(source.read_bytes())
        with Database(source) as pit:
            stored = previous["real"].get("stage2_snapshot_reverified", {})
            fixed = replay_snapshot(pit, stored["content_sha256"], verify_files=True)
            report["original_stage2_snapshot"] = {"status": fixed["status"], "content_sha256": fixed["content_sha256"], "items": fixed["result"]["selected_count"]}
        report["stage2_database_bytes_unchanged"] = sha256(source.read_bytes()) == digest
    return report


def main():
    started, timer = utc_now(), time.perf_counter()
    python = str(ROOT/".venv/bin/python") if (ROOT/".venv/bin/python").exists() else sys.executable
    commands = []
    stream = io.StringIO()
    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always", ResourceWarning)
        suite = unittest.defaultTestLoader.discover(str(ROOT/"tests"))
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    write_json(ROOT/"data/stage4/unit_tests.json", {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                                                  "skipped": len(result.skipped), "output": stream.getvalue()})
    if not result.wasSuccessful(): print(stream.getvalue()); return 1
    evidence, _ = command([python, "scripts/prepare_synthetic_stage4.py", "--root", "tests/generated/stage4-actions"]); commands.append(evidence)
    root = ROOT/"tests/generated/stage4-actions"
    cfg = json.loads((root/"config.json").read_text()); did = cfg["dataset_snapshot_id"]
    base = [python, "-m", "market_research.cli"]
    dbargs = ["--db", "tests/generated/stage4-actions/pit.sqlite", "--dataset-db", "tests/generated/stage4-actions/datasets.sqlite"]
    inputargs = ["--dataset-id", did, "--dataset-hash", did, "--mode", "synthetic"]
    for name in ("split-plan", "split-check"):
        evidence, _ = command(base+[name]+dbargs+inputargs+["--config", "tests/generated/stage4-actions/split.json", "--output", str(root/(name+".json"))]); commands.append(evidence)
    for name in ("prediction-check", "prediction-evaluate"):
        extra = [] if name == "prediction-check" else ["--evaluation-asof", cfg["evaluation_asof"], "--horizon", "1"]
        evidence, _ = command(base+[name]+dbargs+inputargs+["--predictions", "tests/generated/stage4-actions/predictions.json", "--output", str(root/(name+".json"))]+extra,
                             expected=2 if name == "prediction-check" else 0)
        commands.append(evidence)
    evidence, stdout = command(base+["simulation-run"]+dbargs+["--mode", "synthetic", "--config", "tests/generated/stage4-actions/config.json",
                                        "--predictions", "tests/generated/stage4-actions/predictions.json", "--run-root", "tests/generated/stage4-actions/runs", "--output", str(root/"run-meta.json")]); commands.append(evidence)
    meta = json.loads(stdout)
    for name in ("run-show", "run-replay", "stage4-report"):
        evidence, _ = command(base+[name]+dbargs+["--run-file", meta["path"], "--output", str(root/(name+".json"))]); commands.append(evidence)
    # Explicit error, partial and insufficient cases, alongside strict BLOCKED below.
    invalid = json.loads((root/"predictions.json").read_text()); invalid[0]["probability_up"] = 2
    write_json(root/"invalid-predictions.json", invalid)
    evidence, _ = command(base+["prediction-check"]+dbargs+inputargs+["--predictions", str(root/"invalid-predictions.json")], expected=1); commands.append(evidence)
    tiny = cfg["split"] | {"train_dates": 999}; write_json(root/"empty-split.json", tiny)
    evidence, _ = command(base+["split-check"]+dbargs+inputargs+["--config", str(root/"empty-split.json")], expected=3); commands.append(evidence)
    actual = actual_report()
    for d in actual.get("datasets", []):
        evidence, _ = command(base+["simulation-run", "--db", "data/stage3/pit_market.sqlite", "--dataset-db", "data/stage3/datasets.sqlite",
                                    "--mode", "strict", "--config", "data/stage4/"+d["dataset_snapshot_id"]+"-strict.json"], expected=3)
        commands.append(evidence)
    old_synthetic = verify_datasets(ROOT/"tests/generated/stage3/pit.sqlite", ROOT/"tests/generated/stage3/datasets.sqlite")
    # The runtime venv deliberately has no build backend. Use the installed local system backend.
    evidence, _ = command(["python3", "-c", 'from setuptools.build_meta import build_wheel; build_wheel("dist/stage4")']); commands.append(evidence)
    wheels = sorted((ROOT/"dist/stage4").glob("*.whl"))
    with zipfile.ZipFile(wheels[-1]) as wheel:
        modules = [name for name in wheel.namelist() if name.startswith("market_research/") and name.endswith(".py")]
        if any(wheel.read(name) != (ROOT/"src"/name).read_bytes() for name in modules): raise AssertionError("wheel_source_mismatch")
        if len(modules) != len(list((ROOT/"src/market_research").rglob("*.py"))): raise AssertionError("wheel_module_missing")
    evidence, _ = command(["git", "diff", "--check"]); commands.append(evidence)
    envelope = read_run(meta["path"])
    sim = envelope["result"]["simulation"]
    if sim["terminal"]["cash"] != "1028.30": raise AssertionError("synthetic_e2e_hand_cash_mismatch")
    starting = json.loads((ROOT/"data/stage4/starting_state.json").read_text()) if (ROOT/"data/stage4/starting_state.json").exists() else None
    changed = [p for p, h in starting["files"].items() if not (ROOT/p).is_file() or sha256((ROOT/p).read_bytes()) != h] if starting else []
    build_metadata_changes = [p for p in changed if ".egg-info/" in p]
    changed = [p for p in changed if ".egg-info/" not in p]
    # README was observed/read at start but outside the baseline directory hash inventory.
    if starting and "README.md" not in changed: changed.append("README.md")
    protected = [p for p in changed if p.startswith(("src/market_research/datasets/", "src/market_research/pit/", "data/", "reports/stage3", "reports/stage4_handoff"))]
    if protected: raise AssertionError("protected_stage3_inputs_changed:"+str(protected))
    report = {"stage": 4, "started_at": started, "finished_at": utc_now(), "elapsed_seconds": time.perf_counter()-timer,
              "starting_commit": starting["head"] if starting else "not_captured", "starting_worktree": starting["status"] if starting else "not_captured",
              "existing_changed_files_this_stage": changed, "protected_inputs_unchanged": True,
              "generated_build_metadata_changes": build_metadata_changes,
              "starting_readme_evidence": "read at start; existing uncommitted README preserved and extended; root README was outside initial directory hash inventory",
              "environment": envelope["manifest"]["environment"] | {"sqlite": sqlite3.sqlite_version, "m2_executed": False},
              "baseline_tests": starting["baseline_tests"] if starting else None,
              "offline_tests": {"status": "PASS", "tests_run": result.testsRun, "passed": result.testsRun, "failures": 0, "errors": 0, "skipped": len(result.skipped)},
              "runtime_warnings": [{"category": w.category.__name__, "message": str(w.message)} for w in observed],
              "runtime_warning_scope": "existing test_pit.test_35 rejects Database(old_path) after opening SQLite; constructor does not close on that failure; Stage4 contexts close normally; source left unchanged outside Stage4 blocker scope",
              "commands": commands, "build": {"status": "PASS", "wheel": str(wheels[-1].relative_to(ROOT)), "sha256": sha256(wheels[-1].read_bytes()), "source_identical_modules": len(modules)},
              "synthetic": {"status": "PASS", "mode": "synthetic", "dataset_snapshot_id": did, "dataset_content_sha256": did,
                            "run_id": meta["run_id"], "result_content_sha256": meta["result_content_sha256"], "fixed_replay": "PASS",
                            "feature_version": "3.0.1", "label_version": "3.0.1", "prediction_version": "4.0.0",
                            "counts": {"test_predictions": len(json.loads((root/"predictions.json").read_text())), "evaluated": envelope["result"]["metrics"]["count"],
                                       "fills": sim["performance"]["fill_count"], "corporate_action_events": len(sim["corporate_action_ledger"])},
                            "independent_terminal_cash": "1028.30", "terminal_cash": sim["terminal"]["cash"], "cost_totals": sim["performance"]["costs"],
                            "scope": "engine correctness only; deterministic manual predictions; no real investment performance"},
              "actual": actual, "previous_stage3_synthetic_replay": old_synthetic,
              "code_sha256": code_version(ROOT), "verdicts": {"engine_correctness": "PASS", "real_data_readiness": "BLOCKED", "real_historical_performance": "BLOCKED",
              "stage5_synthetic_baseline": "PASS", "stage5_research": "CONDITIONAL", "m2_hardware": "BLOCKED"},
              "resolved_initial_stage4_checks": {"test_failures": 2, "test_causes": ["expected timestamp spelling lacked canonical microseconds", "expected four round trips ignored held-signal defer policy"],
              "build_failure": "runtime venv had no setuptools.build_meta; used preinstalled system setuptools 78.1.1 without network"},
              "network": "none", "models_trained": False, "automatic_trading": False, "commit_push": False}
    write_json(ROOT/"reports/stage4_validation.json", report)
    print(json.dumps({"status": "PASS", "tests": result.testsRun, "synthetic_cash": sim["terminal"]["cash"], "actual": actual["status"], "run_id": meta["run_id"]}))
    return 0


if __name__ == "__main__": sys.exit(main())
