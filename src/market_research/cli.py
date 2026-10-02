import argparse
import json
from pathlib import Path
import platform
import sys
from .collect import collect_batch
from .storage import write_json, sha256, code_version
from .http import utc_now
from .validation import build_report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stage 1 market data feasibility")
    parser.add_argument("command", choices=("collect", "smoke", "validate"))
    parser.add_argument("--config", default="configs/sample.json")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output")
    parser.add_argument("--refresh", action="store_true", help="Preserve a new response observation, never overwrite raw")
    args = parser.parse_args(argv)
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
