from collections import Counter
from datetime import date
import importlib.metadata
import json
import math
from pathlib import Path
from ..http import utc_now
from ..schemas import BAR_REQUIRED
from ..storage import Store, write_json, sha256, code_version


def validate_bars(rows, sessions=None, lifecycle=None):
    issues, seen, previous = [], {}, {}
    for index, row in enumerate(rows):
        def add(code, severity="error", **detail):
            issues.append({"code": code, "severity": severity, "row": index,
                           "date": row.get("trading_date"), **detail})
        malformed = False
        for k, typ in BAR_REQUIRED.items():
            if k not in row:
                add("missing_column", column=k); malformed = True
            elif not isinstance(row[k], typ) or isinstance(row[k], bool):
                add("wrong_type", column=k); malformed = True
        if malformed:
            continue
        try:
            d = date.fromisoformat(row["trading_date"])
            if d.isoformat() != row["trading_date"]:
                raise ValueError()
        except (ValueError, TypeError):
            add("invalid_date"); continue
        key = (row["source"], row["local_instrument_id"], row["trading_date"], row["price_semantics"])
        if key in seen:
            fields = ("open", "high", "low", "close", "volume", "adjusted_close")
            conflict = any(row[k] != seen[key][k] for k in fields)
            add("conflicting_duplicate" if conflict else "duplicate_key")
        else:
            seen[key] = row
        ohlc = [row[k] for k in ("open", "high", "low", "close")]
        if any(v is None for v in ohlc):
            add("missing_price", "warning")
        elif any(not math.isfinite(v) for v in ohlc):
            add("nonfinite_price")
        else:
            o, h, l, c = ohlc
            if l > min(o, c) + 1e-6 or h + 1e-6 < max(o, c) or l > h:
                add("inconsistent_ohlc")
            if min(ohlc) < 0:
                add("negative_price")
            if 0 in ohlc:
                add("zero_price", "warning")
            instrument = key[:2]
            p = previous.get(instrument)
            contiguous = (not p or sessions is None or not any(
                p["trading_date"] < s < row["trading_date"] for s in sessions))
            if p and p["close"] and c and contiguous and abs(c / p["close"] - 1) > 0.25:
                add("large_price_move_review_action_or_error", "warning",
                    previous_date=p["trading_date"], change=c/p["close"] - 1)
            previous[instrument] = row
        volume = row["volume"]
        if volume is None:
            add("missing_volume", "warning")
        elif volume < 0:
            add("negative_volume")
        elif volume == 0:
            add("reported_zero_volume_not_proof_of_no_trading", "warning")
        if sessions is not None and row["trading_date"] not in sessions:
            add("observation_outside_reference_calendar", "warning")
        interval = (lifecycle or {}).get(row["local_instrument_id"], {})
        if interval.get("first_trading_date") and row["trading_date"] < interval["first_trading_date"]:
            add("pre_listing_observation")
        if interval.get("last_trading_date") and row["trading_date"] > interval["last_trading_date"]:
            add("post_delisting_observation")
    return issues


def calendar_rows(start, end):
    import exchange_calendars as xc
    cal = xc.get_calendar("XNYS", start=start, end=end)
    schedule = cal.schedule.loc[start:end]
    rows = []
    for d, row in schedule.iterrows():
        rows.append({"trading_date": d.date().isoformat(), "open_utc": row["open"].isoformat(),
                     "close_utc": row["close"].isoformat(), "exchange": "XNYS",
                     "timezone": "America/New_York",
                     "source": "exchange_calendars_derived_reference",
                     "publication_time_utc": None})
    return rows


def action_checks(bars, actions):
    results = []
    lookup = {(b["source"], b["requested_symbol"], b["trading_date"]): b for b in bars}
    for a in actions:
        if a["source"] != "yahoo":
            continue
        candidates = sorted([b for b in bars if b["source"] == a["source"] and
            b["requested_symbol"] == a["requested_symbol"] and b["trading_date"] < a["event_date"]],
            key=lambda b: b["trading_date"])
        post = lookup.get((a["source"], a["requested_symbol"], a["event_date"]))
        item = {"symbol": a["requested_symbol"], "date": a["event_date"], "type": a["event_type"]}
        if not candidates or not post:
            results.append({**item, "status": "skipped", "reason": "event_boundary_outside_sample"}); continue
        pre = candidates[-1]
        if a["event_type"] == "splits":
            if not a["denominator"] or not a["numerator"] or not pre["close"] or not post["close"]:
                results.append({**item, "status": "failed", "reason": "invalid_split_or_price"}); continue
            results.append({**item, "status": "observed",
                "ratio": a["numerator"] / a["denominator"],
                "pre_close": pre["close"], "post_close": post["close"],
                "price_ratio": post["close"] / pre["close"],
                "price_semantics": pre["price_semantics"],
                "raw_adjusted_reconciliation": "skipped_no_independent_raw_split_window"})
        elif all(b["close"] and b["adjusted_close"] for b in (pre, post)):
            observed = (pre["adjusted_close"] / pre["close"]) / (post["adjusted_close"] / post["close"])
            expected = 1 - a["amount"] / pre["close"]
            results.append({**item, "status": "passed" if abs(observed-expected) < 0.00005 else "review",
                            "observed_factor_ratio": observed, "expected_factor_ratio": expected,
                            "cash_amount": a["amount"], "tolerance": 0.00005,
                            "scope": "vendor_internal_dividend_adjustment_only_not_raw_cash_certification"})
    return results


def cross_provider_comparison(bars):
    left = {b["trading_date"]: b for b in bars if b["source"] == "alpha_vantage" and b["requested_symbol"] == "IBM"}
    right = {b["trading_date"]: b for b in bars if b["source"] == "yahoo" and b["requested_symbol"] == "IBM"}
    overlap = sorted(left.keys() & right.keys())
    if not overlap:
        return {"status": "skipped", "reason": "no_independent_price_overlap"}
    differences = []
    for d in overlap:
        for field in ("open", "high", "low", "close", "volume"):
            a, b = left[d][field], right[d][field]
            if a is None or b is None:
                continue
            absolute = abs(a - b)
            relative = absolute / abs(a) if a else None
            tolerance = 0.0001 if field == "volume" else 0.00001
            if relative is None or relative > tolerance:
                differences.append({"date": d, "field": field, "alpha_vantage": a, "yahoo": b,
                                    "absolute_difference": absolute, "relative_difference": relative})
    return {"status": "review" if differences else "passed_with_scope_limit",
            "symbol": "IBM", "dates_compared": len(overlap), "start": overlap[0], "end": overlap[-1],
            "differences": differences,
            "definitions": {"alpha_vantage": "raw_as_traded", "yahoo": "vendor_split_adjusted_ohlc_not_raw"},
            "note": "No splits in observed overlap; volume/session/rounding definitions remain unverified. Neither source is designated truth."}


def build_report(data_root, project_root, config_path, output):
    store = Store(data_root)
    records = store.records()
    latest = {}
    for r in sorted(records, key=lambda r: r.get("recorded_at_utc", r["fetched_at_utc"])):
        latest[r["request_id"]] = r
    bars, actions, directory, groups = [], [], [], []
    calendar = calendar_rows("2022-01-01", "2026-12-31")
    sessions = {r["trading_date"] for r in calendar}
    datasets = []
    # Use one successful vintage per request even if a later refresh failed.
    for request_id, latest_record in latest.items():
        record = latest_record if latest_record["status"] == "success" else store.cached(request_id)
        if not record:
            continue
        obj = json.loads((store.root / record["normalized_path"]).read_text())
        if record.get("normalized_sha256") and sha256((store.root / record["normalized_path"]).read_bytes()) != record["normalized_sha256"]:
            raise RuntimeError("normalized_integrity_error")
        if sha256((store.root / record["raw_path"]).read_bytes()) != record["sha256"]:
            raise RuntimeError("raw_integrity_error")
        part = obj["bars"]
        if record["source"] == "alpha_vantage" and record["request_parameters"].get("outputsize") == "full":
            # Retain the response exactly as delivered. Only these study windows
            # enter the sample-quality report; no giant full-history evaluation.
            part = [b for b in part if "2024-01-01" <= b["trading_date"] <= "2024-12-31" or
                    "2026-05-01" <= b["trading_date"] <= "2026-10-01"]
            write_json(store.root / "derived/alpha_ibm_sample.json", {"bars": part,
                       "source_raw_sha256": record["sha256"],
                       "scope": "2024 study and 2026 independent comparison windows"})
        bars.extend(part); actions.extend(obj["actions"]); directory.extend(obj["directory"])
        datasets.append({"source": record["source"], "request": record["request_parameters"],
                         "counts": record["counts"], "actual_data_period": record["actual_data_period"],
                         "raw_path": record["raw_path"], "normalized_path": record["normalized_path"],
                         "sha256": record["sha256"], "quality_sample_bars": len(part)})
        if part:
            dates = sorted(b["trading_date"] for b in part)
            if record["source"] == "alpha_vantage" and record["request_parameters"].get("outputsize") == "full":
                expected = {d for d in sessions if "2024-01-01" <= d <= "2024-12-31" or
                            "2026-05-01" <= d <= "2026-10-01"}
            else:
                expected = {d for d in sessions if dates[0] <= d <= dates[-1]}
            missing = sorted(expected - set(dates))
            groups.append({"source": record["source"], "symbol": part[0]["requested_symbol"],
                           "actual_start": dates[0], "actual_end": dates[-1],
                           "missing_reference_sessions": missing,
                           "missing_reason": "unknown_listing_halt_or_collection_failure" if missing else None,
                           "boundary_coverage": "request bounds must be checked separately; no missing-date imputation"})
    # De-duplicate overlapping request vintages only for report aggregation;
    # preserve and flag conflicts rather than silently selecting a truth.
    unique, conflicts = {}, []
    for b in bars:
        key = (b["source"], b["requested_symbol"], b["trading_date"], b["price_semantics"])
        if key in unique and any(unique[key][k] != b[k] for k in ("open", "high", "low", "close", "volume", "adjusted_close")):
            conflicts.append({"code": "conflicting_overlapping_requests", "severity": "error", "key": key})
        else:
            unique[key] = b
    bars = sorted(unique.values(), key=lambda b: (b["source"], b["requested_symbol"], b["trading_date"]))
    actions = list({(a["source"], a["requested_symbol"], a["event_type"], a["event_date"]): a for a in actions}.values())
    issues = validate_bars(bars, sessions) + conflicts
    alias_issues = [b for b in bars if b["source"] == "yahoo" and b["requested_symbol"] == "META" and b["trading_date"] < "2022-06-09"]
    if alias_issues:
        issues.append({"code": "current_symbol_labels_pre_rename_history", "severity": "warning",
                       "rows": len(alias_issues), "symbol": "META", "old_symbol": "FB",
                       "note": "Official rename date verified separately; automatic permanent-ID linkage unavailable"})
    report = {"schema_version": "1.0.0", "generated_at_utc": utc_now(),
        "environment": {"calendar_library": importlib.metadata.version("exchange_calendars")},
        "code_sha256": code_version(project_root), "config_sha256": sha256(Path(config_path).read_bytes()),
        "network_status_counts": dict(Counter(r["status"] for r in latest.values())),
        "latest_request_results": [{"request": r["request_parameters"], "status": r["status"],
                                    "http_status": r.get("http_status")} for r in latest.values()],
        "counts": {"bars": len(bars), "actions": len(actions), "directory_rows": len(directory)},
        "datasets": datasets, "calendar_coverage": groups, "issues": issues,
        "issue_counts": dict(Counter(x["code"] for x in issues)),
        "corporate_action_checks": action_checks(bars, actions),
        "cross_provider_comparison": cross_provider_comparison(bars),
        "skipped_checks": [
            {"check": "listing_and_delisting_boundaries", "reason": "No verified full security lifecycle intervals"},
            {"check": "raw_vs_adjusted_split_reconciliation", "reason": "No independent raw OHLC split-window source"},
            {"check": "historical_receipt_and_initial_versions", "reason": "Current responses are not historical first-received vintages"},
            {"check": "full_10_to_15_year_coverage", "reason": "Small samples only"}],
        "calendar": {"source": "exchange_calendars/XNYS", "historical_notice_versions": "unavailable",
                     "path": "derived/calendar.json", "row_count": len(calendar),
                     "sha256": sha256(json.dumps(calendar, ensure_ascii=False, indent=2, sort_keys=True).encode()+b"\n")},
        "structural_validation": "PASS" if not any(x["severity"] == "error" for x in issues) else "FAIL",
        "historical_pit_validation": "BLOCKED",
        "note": "Structural PASS does not certify raw data, historical PIT or licensing."}
    write_json(store.root / "derived/calendar.json", calendar)
    write_json(output, report)
    return report
