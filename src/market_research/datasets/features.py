"""Pure feature arithmetic over ONE frozen price query; never imports label code."""
import math
import statistics
from .contracts import FEATURE_VERSION


def valid_number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def compute_features(result, calendar, *, anchor_date, price_kind, as_traded_mode, expected_semantics=None, expected_currency=None):
    if as_traded_mode != "price_change_only": raise ValueError("explicit_as_traded_price_change_contract_required")
    rows = [r for r in result["data"] if r["price_kind"] == price_kind and r["trading_date"] <= anchor_date]
    features = {}; reasons = []
    identity = {(r["source"], r["security_record_id"], r["price_semantics"], r["currency"], r["session_scope"],
                 r["volume_unit"], r["volume_adjustment_basis"]) for r in rows}
    if len(identity) > 1: reasons.append("mixed_series_meaning_currency_session_or_identity")
    if result["conflict_count"]: reasons.append("unresolved_query_conflict")
    if any(r["price_semantics"] == "unknown" or r["currency"] is None for r in rows): reasons.append("unknown_meaning_or_currency")
    if expected_semantics and any(r["price_semantics"]!=expected_semantics for r in rows): reasons.append("price_meaning_contract_mismatch")
    if expected_currency and any(r["currency"]!=expected_currency for r in rows): reasons.append("currency_contract_mismatch")
    dates = calendar.window(anchor_date, 61)
    by_date = {r["trading_date"]: r for r in rows}
    if len(by_date) != len(rows): reasons.append("duplicate_feature_session")
    meaning = rows[0]["price_semantics"] if rows else expected_semantics
    raw = meaning == "as_traded"
    return_prefix = "close_price_change" if raw else "close_return"
    volatility_prefix = "realized_log_price_change_volatility" if raw else "realized_log_return_volatility"

    def unavailable_reason(fields, n, include_current=True, required_by_position=None):
        expected = calendar.window(anchor_date, n)
        if len(expected) < n: return "lookback_outside_calendar"
        if not include_current: expected = calendar.window(anchor_date, n+1)[:-1]
        if len(expected) < n: return "lookback_outside_calendar"
        absent = [d for d in expected if d not in by_date]
        if absent:
            earliest = min(by_date) if by_date else None
            return "insufficient_history_or_prelisting_unverified" if earliest and all(d < earliest for d in absent) else "missing_session_or_collection_failure"
        required=[(d,f) for i,d in enumerate(expected) for f in (required_by_position.get(i,[]) if required_by_position else fields)]
        if any(by_date[d].get(f) is None for d,f in required): return "missing_input_field"
        if any(not valid_number(by_date[d].get(f)) for d,f in required): return "invalid_input_type_or_nonfinite"
        if any(by_date[d][f] <= 0 for d,f in required if f != "volume"): return "zero_or_negative_price"
        if any(by_date[d][f] < 0 for d,f in required if f == "volume"): return "negative_volume"
        return None

    used_ids = set()
    invalid_input_reasons = {"invalid_input_type_or_nonfinite", "zero_or_negative_price", "negative_volume"}
    def put(name, fields, n, fn, *, mean_kind="price_ratio", required_by_position=None):
        reason = reasons[0] if reasons else unavailable_reason(fields, n,required_by_position=required_by_position)
        expected = calendar.window(anchor_date, n)
        if reason:
            features[name] = {"value": None, "status": "invalid" if reason in invalid_input_reasons else "unavailable",
                              "reason": reason, "unit": "ratio", "measure_kind": mean_kind}
            return
        values = [by_date[d] for d in expected]
        try: value = fn(values)
        except (ValueError, ZeroDivisionError, OverflowError): value = None
        if value is None or not valid_number(value):
            features[name] = {"value": None, "status": "invalid", "reason": "invalid_numeric_result", "unit": "ratio", "measure_kind": mean_kind}
            return
        used_ids.update(r["version_id"] for r in values)
        features[name] = {"value": value, "status": "ready", "reason": None, "unit": "daily_log_stdev" if "volatility" in name else "ratio",
                          "measure_kind": mean_kind, "input_version_ids": [r["version_id"] for r in values]}

    for n in (1, 5, 20, 60):
        put(f"{return_prefix}_{n}", ["close"], n+1, lambda a: a[-1]["close"]/a[0]["close"]-1,
            mean_kind="non_economic_raw_price_change" if raw else "provider_adjusted_price_ratio")
    for n in (5, 20, 60):
        put(f"{volatility_prefix}_{n}", ["close"], n+1,
            lambda a: statistics.stdev(math.log(b["close"])-math.log(p["close"]) for p,b in zip(a,a[1:])),
            mean_kind="non_economic_raw_log_change_stdev" if raw else "provider_adjusted_log_return_stdev")
    for n in (20, 60):
        put(f"close_to_ma_{n}", ["close"], n, lambda a: a[-1]["close"]/statistics.fmean(r["close"] for r in a)-1)
    put("open_gap", ["open","close"], 2, lambda a: a[-1]["open"]/a[0]["close"]-1,
        mean_kind="non_economic_raw_price_gap" if raw else "same_meaning_price_gap",required_by_position={0:["close"],1:["open"]})
    put("intraday_high_low_ratio", ["high","low"], 1, lambda a: a[-1]["high"]/a[-1]["low"]-1)
    put("volume_to_prior20_mean", ["volume"], 21, lambda a: a[-1]["volume"]/statistics.fmean(r["volume"] for r in a[:-1]),
        mean_kind="provider_volume_ratio_not_dollar_turnover")
    last = max(by_date) if by_date else None
    elapsed = calendar.elapsed(last, anchor_date) if last else None
    features["elapsed_sessions_since_last_observation"] = {"value": elapsed,"status":"ready" if last else "unavailable","reason":None if last else "no_observations","unit":"sessions"}
    features["missing_any_numeric_feature"] = {"value": any(v["status"] != "ready" for v in features.values()),"status":"ready","reason":None,"unit":"boolean"}
    selected = [r for r in rows if r["version_id"] in used_ids]
    invalid = any(v["status"] == "invalid" for v in features.values())
    if invalid: reasons.append("invalid_numeric_feature_input_or_result")
    return {"feature_definition_version":FEATURE_VERSION,"anchor_date":anchor_date,"features":features,"used_version_ids":sorted(used_ids),
            "selected_inputs":selected,"status":"invalid" if invalid else "blocked" if reasons or not selected else "ready",
            "reasons":reasons or ([] if selected else ["no_computable_price_features"]),
            "last_observation_date":last,"price_semantics":meaning,
            "observation_counts":{"missing_calendar_sessions":sum(d not in by_date for d in dates),
                                  "reported_zero_volume":sum(r.get("volume")==0 for r in rows),
                                  "null_close":sum(r.get("close") is None for r in rows)},
            "limitations":["No economic return claim for as-traded ratios across corporate actions.",
                            "Current vendor adjustments do not prove historical adjustment versions.",
                            "Observed zero volume does not prove a trading halt; missing sessions are not filled."]}
