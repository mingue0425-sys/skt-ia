import argparse
import json
from pathlib import Path
import platform
import sys
from .collect import collect_batch
from .storage import write_json, sha256, code_version
from .http import utc_now
from .validation import build_report
from .pit.cli import COMMANDS as DB_COMMANDS, options as db_options, run as run_db
from .datasets.cli import COMMANDS as DATASET_COMMANDS, options as dataset_options, run as run_dataset
from .stage4.cli import COMMANDS as STAGE4_COMMANDS, options as stage4_options, run as run_stage4


from .training.cli import COMMANDS as TRAINING_COMMANDS, options as training_options, run as run_training_cli


from .observed.cli import COMMANDS as OBSERVED_COMMANDS, run as run_observed


def main(argv=None):
    parser = argparse.ArgumentParser(description="Market data collection and local versioned queries")
    parser.add_argument("command", choices=("collect", "smoke", "validate") + DB_COMMANDS + DATASET_COMMANDS + STAGE4_COMMANDS + TRAINING_COMMANDS + OBSERVED_COMMANDS)
    parser.add_argument("--config", default="configs/sample.json")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output")
    parser.add_argument("--refresh", action="store_true", help="Preserve a new response observation, never overwrite raw")
    db_options(parser)
    dataset_options(parser)
    stage4_options(parser)
    training_options(parser)
    args = parser.parse_args(argv)
    if args.command in OBSERVED_COMMANDS:
        return run_observed(args)
    if args.command in TRAINING_COMMANDS:
        return run_training_cli(args)
    if args.command in DB_COMMANDS:
        return run_db(args)
    if args.command in DATASET_COMMANDS:
        return run_dataset(args)
    if args.command in STAGE4_COMMANDS:
        return run_stage4(args)
    config_path = Path(args.config)
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes)
    started = utc_now()
    if args.command == "validate":
        report = build_report(args.data_dir, args.project_root, config_path,
                              args.output or "reports/sample_quality.json")
        print(json.dumps({"structural_validation": report["structural_validation"],
                          "counts": report["counts"], "network_status_counts": report["network_status_counts"]}))
        return 0 if report["structural_validation"] == "PASS" else 1
    specs = config["requests"]
    if args.command == "smoke":
        specs = [s for s in specs if s.get("smoke")]
    results = collect_batch(specs, args.data_dir, args.project_root, config_bytes,
                            refresh=args.refresh)
    execution = {"command": " ".join(sys.argv), "started_at_utc": started,
                 "finished_at_utc": utc_now(), "environment": {"python": sys.version,
                 "platform": platform.platform(), "machine": platform.machine()},
                 "config_sha256": sha256(config_bytes), "code_sha256": code_version(args.project_root),
                 "results": results}
    # A single writer is deliberate; simultaneous collector processes unsupported.
    write_json(args.output or "reports/execution-" + args.command + ".json", execution)
    for result in results:
        r = result["manifest_record"]
        print(json.dumps({"source": r["source"], "kind": r["request_parameters"]["kind"],
                          "symbol": r["request_parameters"].get("symbol"), "status": result["status"],
                          "http_status": r["http_status"], "counts": r.get("counts")}))
    # Exit 2 signifies a completed batch with partial failures, not a false success.
    return 0 if all(r["status"] in ("success", "cached", "reprocessed") for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
