"""Flat db-* commands alongside the existing collect/smoke/validate commands."""
import json
from pathlib import Path
import sqlite3
from ..storage import write_json
from .database import Database, SCHEMA_VERSION
from .importer import import_artifacts, import_calendar
from .query import query
from .snapshots import create_snapshot, replay_snapshot

COMMANDS = ("db-init", "db-import", "db-audit", "db-query", "snapshot-create", "snapshot-verify", "snapshot-replay", "db-report")


def options(parser):
    parser.add_argument("--db", default="data/stage2.sqlite")
    parser.add_argument("--cutoff")
    parser.add_argument("--policy", choices=("observed", "historical_verified", "research_assumed"))
    parser.add_argument("--symbols", help="Comma-separated symbols")
    parser.add_argument("--sources", help="Comma-separated sources")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--allow-temporary", action="store_true")
    parser.add_argument("--price-semantics", default="as_traded", help="Comma-separated explicit meanings")
    parser.add_argument("--family", choices=("prices", "actions"), default="prices")
    parser.add_argument("--symbol-mode", choices=("effective_mapping", "provider_label"), default="effective_mapping")
    parser.add_argument("--calendar", help="Existing reference calendar JSON to import")
    parser.add_argument("--calendar-version")
    parser.add_argument("--input-domain", choices=("market", "synthetic"), default="market")
    parser.add_argument("--assumption-rule", help="JSON file: rule id/version/timezone/delay_hours")
    parser.add_argument("--session-requirement")
    parser.add_argument("--snapshot-id")
    parser.add_argument("--require-data", action="store_true")
    parser.add_argument("--require-unambiguous", action="store_true")
    parser.add_argument("--skip-file-verification", action="store_true", help="Explicit metadata-only audit/replay")


def run(args):
    try:
        with Database(args.db, initialize=args.command == "db-init") as db:
            if args.command == "db-init":
                result = {"status": "PASS", "schema_version": SCHEMA_VERSION, "db": str(Path(args.db).resolve())}
            elif args.command == "db-import":
                result = import_artifacts(db, args.data_dir, domain=args.input_domain)
                if args.calendar:
                    result["calendar_import"] = import_calendar(db, args.calendar, domain=args.input_domain)
            elif args.command == "db-audit":
                result = db.audit(files=not args.skip_file_verification)
            elif args.command in ("snapshot-replay", "snapshot-verify"):
                if not args.snapshot_id: raise ValueError("snapshot_id_required")
                result = replay_snapshot(db, args.snapshot_id, verify_files=not args.skip_file_verification)
                if args.command == "snapshot-verify":
                    result = {k: v for k, v in result.items() if k != "result"} | {
                        "selected_count": result["result"]["selected_count"], "data_status": result["result"]["status"],
                        "conflict_count":result["result"]["conflict_count"]}
            else:
                if not args.cutoff or not args.policy: raise ValueError("cutoff_and_explicit_policy_required")
                rule = json.loads(Path(args.assumption_rule).read_text()) if args.assumption_rule else None
                result = query(db, cutoff=args.cutoff, policy=args.policy,
                    symbols=args.symbols.split(",") if args.symbols else None,
                    sources=args.sources.split(",") if args.sources else None, start=args.start, end=args.end,
                    identifier_requirement="temporary_allowed" if args.allow_temporary else "verified",
                    price_semantics=tuple(args.price_semantics.split(",")), family=args.family,
                    symbol_mode=args.symbol_mode, calendar_version=args.calendar_version,
                    assumption_rule=rule, input_domain=args.input_domain, session_requirement=args.session_requirement)
                failed = (args.require_data and result["selected_count"] == 0) or (args.require_unambiguous and result["conflict_count"] > 0)
                if args.command == "snapshot-create":
                    result = create_snapshot(db, result) | {"requirements_failed": bool(failed)}
                elif args.command == "db-report":
                    audit = db.audit()
                    result = {"status": "FAIL" if failed or audit["status"] != "PASS" else "PASS",
                              "data_status": result["status"], "selected_count": result["selected_count"],
                              "exclusion_counts": result["exclusion_counts"], "conflict_count": result["conflict_count"],
                              "request": result["request"], "limitations": result["limitations"], "strictness": result["strictness"],
                              "audit": audit, "evidence_scope": "engine audit and query summary, not data-readiness certification"}
                else:
                    result["requirements_failed"] = bool(failed)
            target=result.get("result",result)
            if args.require_data and target.get("selected_count")==0: result["requirements_failed"]=True
            if args.require_unambiguous and target.get("conflict_count",0)>0: result["requirements_failed"]=True
            if args.output: write_json(args.output, result)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
            if result.get("status") in ("FAIL", "ERROR") or result.get("requirements_failed"): return 1
            if result.get("status") == "PARTIAL": return 2
            return 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        # No exception containing a URL, SQL payload, credentials or response body is printed.
        reason = str(exc) if isinstance(exc, ValueError) and str(exc).replace("_", "").isalnum() else type(exc).__name__
        result = {"status": "ERROR", "reason": reason}
        if args.output: write_json(args.output, result)
        print(json.dumps(result)); return 1
