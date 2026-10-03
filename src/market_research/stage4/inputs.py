"""Read only frozen Stage3 membership and provenance; never issue a new query."""
from collections import Counter
from ..datasets.store import replay_dataset, iter_dataset_rows, training_selection
from ..pit.time import timestamp

MODES = ("strict", "research", "synthetic")


def check_mode(mode):
    if mode not in MODES:
        raise ValueError("explicit_strict_research_synthetic_mode_required")


def key(row):
    return (row["sample_id"], row["horizon_sessions"])


def load_fixed(store, pit, dataset_id, content_hash, mode, *, max_rows=100000):
    check_mode(mode)
    fixed = replay_dataset(store, pit, dataset_id, verify_files=True)
    if fixed["content_sha256"] != content_hash:
        raise ValueError("input_dataset_hash_mismatch")
    if fixed["items"] > max_rows:
        raise ValueError("local_row_limit_exceeded")
    rows = list(iter_dataset_rows(store, pit, dataset_id, verify_files=True))
    return fixed, rows


def contract_reasons(row, mode):
    f, l = row["feature"], row["label"]
    reasons = []
    if f.get("definition_version") not in ("3.0.0", "3.0.1") or l.get("definition_version") not in ("3.0.0", "3.0.1"):
        reasons.append("unsupported_feature_label_version")
    if f.get("sample_status") != "ready": reasons.append("feature_" + str(f.get("sample_status")))
    if l.get("status") != "ready": reasons.append("label_" + str(l.get("status")))
    try:
        decision, cutoff, ready, entry, exit_at = [timestamp(v) for v in (
            f.get("decision_at"), f.get("feature_cutoff"), f.get("feature_ready_at"),
            f.get("intended_entry_at"), l.get("intended_exit_at"))]
        if not all((decision, cutoff, ready, entry, exit_at)) or not cutoff <= ready <= decision < entry < exit_at:
            reasons.append("feature_decision_entry_exit_order")
        if l.get("intended_entry_at") != f.get("intended_entry_at"):
            reasons.append("label_entry_mismatch")
        if l.get("label_available_at") and exit_at and timestamp(l["label_available_at"]) < exit_at:
            reasons.append("label_availability_before_exit")
    except (ValueError, TypeError): reasons.append("invalid_time_contract")
    for payload, names in ((f, ("feature_snapshot_id",)), (l, ("label_snapshot_id", "action_snapshot_id"))):
        if any(not payload.get(n) for n in names): reasons.append("snapshot_link_missing")
        if payload.get("query_policy") not in ("observed", "historical_verified", "research_assumed"):
            reasons.append("query_policy_missing")
        if payload.get("query_policy") == "research_assumed" and not payload.get("assumption_policy_id"):
            reasons.append("assumption_policy_missing")
    fp, lp = f.get("price_contract", {}), l.get("price_contract", {})
    if any(not p.get(n) for p in (fp, lp) for n in ("source", "symbol", "currency", "price_kind", "price_semantics")):
        reasons.append("price_currency_identity_contract_missing")
    if fp.get("source") != lp.get("source") or fp.get("symbol") != lp.get("symbol") or fp.get("currency") != lp.get("currency"):
        reasons.append("feature_label_series_mismatch")
    if not f.get("security_record_ids") or not f.get("identifier_verification"):
        reasons.append("security_identity_missing")
    if mode == "synthetic" and (f.get("input_domain") != "synthetic" or l.get("input_domain") != "synthetic"):
        reasons.append("synthetic_domain_required")
    if mode == "strict":
        if f.get("input_domain") != "market": reasons.append("market_domain_required")
        if f.get("identifier_verification") != ["verified"]: reasons.append("security_identifier_unverified")
        if not f.get("historical_pit_eligible") or f.get("query_policy") != "historical_verified" or l.get("query_policy") != "historical_verified":
            reasons.append("strict_historical_pit_unmet")
        if f.get("execution_status") != "ready": reasons.append("operational_availability_unverified")
        if not l.get("learning_ready"): reasons.append("learning_rights_or_strict_pit_unmet")
    return sorted(set(reasons))


def select_at(store, pit, dataset_id, rows, asof, mode):
    """The existing selector remains authoritative, with stricter decision-time checks."""
    check_mode(mode)
    asof = timestamp(asof)
    selection = training_selection(store, pit, dataset_id, training_asof=asof,
                                   allow_research=(mode != "strict"))
    candidates = {key(r) for r in selection["selected"]}
    selected, excluded = [], []
    counts = Counter(selection["exclusion_counts"])
    for row in rows:
        reasons = contract_reasons(row, mode)
        if key(row) not in candidates: reasons.append("training_selection_rejected")
        available = row["label"].get("label_available_at")
        if not available or timestamp(available) > asof: reasons.append("label_not_available_at_asof")
        if any(row["feature"].get(k) and timestamp(row["feature"][k]) > asof for k in ("decision_at", "feature_ready_at")):
            reasons.append("feature_not_available_at_asof")
        if reasons:
            excluded.append({"sample_id": row["sample_id"], "horizon": row["horizon_sessions"], "reasons": sorted(set(reasons))})
            counts.update(set(reasons))
        else: selected.append(row)
    return selected, {"mode": mode, "asof": asof, "selected_count": len(selected),
                      "selector": selection, "exclusion_counts": dict(counts), "excluded": excluded}


def eligibility_report(store, pit, dataset_id, rows, asof, mode):
    selected, report = select_at(store, pit, dataset_id, rows, asof, mode)
    features = {r["sample_id"]: r["feature"] for r in rows}
    report.update(status="OK" if selected else "BLOCKED", dataset_snapshot_id=dataset_id,
                  eligible_membership=[{k: r[k] for k in ("sample_id", "horizon_sessions", "feature_version_id", "label_version_id")} for r in selected],
                  feature_status_counts=dict(Counter(f["sample_status"] for f in features.values())),
                  label_status_counts=dict(Counter(r["label"]["status"] for r in rows)),
                  source_reason_counts=dict(Counter(reason for f in features.values() for reason in f.get("sample_reasons", [])) +
                                            Counter(r["label"]["reason"] for r in rows if r["label"].get("reason"))),
                  historical_performance="BLOCKED" if mode != "strict" or not selected else "CONDITIONAL")
    return report
