"""Flat CLI, explicit mode and input hashes, distinct failure/block/partial codes."""
import json
import sqlite3
from pathlib import Path
from decimal import DecimalException
from ..storage import write_json
from ..pit import Database
from ..datasets.store import DatasetStore
from .inputs import load_fixed
from .splits import make_splits
from .predictions import validate_predictions
from .metrics import evaluate
from .runs import save_run, read_run, replay_run, aggregate_run

COMMANDS = ("split-plan", "split-check", "prediction-check", "prediction-evaluate", "simulation-run", "run-show", "run-replay", "stage4-report")


def options(parser):
    parser.add_argument("--mode", choices=("strict", "research", "synthetic"))
    parser.add_argument("--dataset-hash")
    parser.add_argument("--predictions")
    parser.add_argument("--evaluation-asof")
    parser.add_argument("--horizon", type=int, choices=(1, 5, 20))
    parser.add_argument("--run-root", default="data/stage4/runs")
    parser.add_argument("--run-file")


def exit_code(result):
    status = result["status"]
    return 1 if status in ("ERROR", "INVALID", "FAIL") else 3 if status in ("BLOCKED", "INSUFFICIENT_DATA") else 2 if status == "PARTIAL" else 0


def run(args):
    try:
        if args.command in ("run-show", "stage4-report"):
            if not args.run_file: raise ValueError("run_file_required")
            value = read_run(args.run_file)
            result = aggregate_run(value) if args.command == "stage4-report" else value
            if args.command == "run-show": result = {"status": "OK", "mode": value["result"]["mode"], "run": value}
        else:
            # Guard absent DB paths so read-only commands cannot create replacement input DBs.
            if not Path(args.db).is_file() or not Path(args.dataset_db).is_file(): raise ValueError("fixed_input_database_missing")
            with Database(args.db) as pit, DatasetStore(args.dataset_db) as store:
                if args.command == "run-replay":
                    if not args.run_file: raise ValueError("run_file_required")
                    result = replay_run(args.run_file, pit, store, args.project_root)
                elif args.command == "simulation-run":
                    config = json.loads(Path(args.config).read_text())
                    if not args.mode or args.mode != config.get("mode"): raise ValueError("explicit_cli_config_mode_match_required")
                    predictions = json.loads(Path(args.predictions).read_text()) if args.predictions else []
                    result = save_run(args.run_root, pit, store, config, predictions, args.project_root)
                else:
                    if not args.dataset_id or not args.dataset_hash or not args.mode: raise ValueError("dataset_id_hash_mode_required")
                    _, rows = load_fixed(store, pit, args.dataset_id, args.dataset_hash, args.mode)
                    if args.command in ("split-plan", "split-check"):
                        result = make_splits(store, pit, args.dataset_id, rows, json.loads(Path(args.config).read_text()), args.mode)
                    else:
                        predictions = json.loads(Path(args.predictions).read_text())
                        result = validate_predictions(predictions, rows, args.dataset_id, args.dataset_hash, args.mode)
                        if args.command == "prediction-evaluate" and result["status"] != "INVALID":
                            if not args.evaluation_asof or not args.horizon: raise ValueError("evaluation_asof_horizon_required")
                            checked = result
                            result = evaluate(rows, checked["accepted"], evaluation_asof=args.evaluation_asof, mode=args.mode, horizon=args.horizon)
                            result["prediction_exclusion_counts"] = checked["exclusion_counts"]
        if args.output: write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return exit_code(result)
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, DecimalException) as exc:
        result = {"status": "ERROR", "mode": args.mode, "reason": str(exc) if isinstance(exc, ValueError) else "input_schema_file_or_database_error"}
        if args.output: write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False)); return 1
