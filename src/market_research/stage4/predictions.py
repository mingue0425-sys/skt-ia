"""Prediction records are supplied separately from frozen evaluation outcomes."""
import math
from collections import Counter
from ..pit.time import timestamp
from .inputs import check_mode, key

PREDICTION_VERSION = "4.0.0"


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_predictions(records, rows, dataset_id, dataset_hash, mode, *, oracle_test=False):
    check_mode(mode)
    lookup = {key(r): r for r in rows}
    ids, pairs, accepted, rejected = {}, set(), [], []
    required = ("prediction_id", "sample_id", "model_or_signal_id", "model_version", "training_asof", "generated_at",
                "decision_at", "intended_entry_at", "horizon", "predicted_return", "probability_up",
                "dataset_snapshot_id", "dataset_content_sha256", "feature_version_id", "feature_snapshot_id", "mode",
                "prediction_version", "generation_kind")
    for p in records:
        reasons = []
        if any(k not in p or (p[k] is None and k not in ("predicted_return", "probability_up")) for k in required): reasons.append("missing_prediction_field")
        pid = p.get("prediction_id")
        if pid in ids: reasons.append("duplicate_prediction_id" if ids[pid] == p else "conflicting_prediction_id")
        ids[pid] = p
        pair = (p.get("sample_id"), p.get("horizon"))
        if pair in pairs: reasons.append("duplicate_sample_horizon_prediction")
        pairs.add(pair)
        row = lookup.get(pair)
        if row is None: reasons.append("prediction_sample_not_in_dataset")
        if p.get("dataset_snapshot_id") != dataset_id or p.get("dataset_content_sha256") != dataset_hash:
            reasons.append("prediction_dataset_mismatch")
        if p.get("mode") != mode or p.get("prediction_version") != PREDICTION_VERSION:
            reasons.append("prediction_mode_or_version_mismatch")
        task = p.get("output_task", "legacy_joint")
        target = p.get("target_id", "price_return")
        if task not in ("regression", "classification", "legacy_joint"): reasons.append("invalid_output_task")
        if target not in ("price_return", "cash_total_return"): reasons.append("invalid_target_id")
        if task != "classification" and not finite(p.get("predicted_return")): reasons.append("invalid_predicted_return")
        if task == "classification" and p.get("predicted_return") is not None: reasons.append("classification_return_must_be_null")
        if task == "regression" and p.get("probability_up") is not None: reasons.append("regression_probability_must_be_null")
        prob = p.get("probability_up")
        if task != "regression" and (not finite(prob) or not 0 <= prob <= 1): reasons.append("probability_out_of_range")
        q = p.get("predicted_quantiles")
        if q is not None:
            try:
                quantiles = sorted((float(k), v) for k, v in q.items())
                if not quantiles or len({k for k, _ in quantiles}) != len(quantiles) or any(not finite(k) or not 0 < k < 1 or not finite(v) for k, v in quantiles):
                    raise ValueError()
                if any(a[1] > b[1] for a, b in zip(quantiles, quantiles[1:])): reasons.append("crossed_quantiles")
            except (ValueError, TypeError, AttributeError): reasons.append("invalid_quantiles")
        tradable = True
        try:
            train, decision, entry, generated = [timestamp(p.get(k)) for k in ("training_asof", "decision_at", "intended_entry_at", "generated_at")]
            if not all((train, decision, entry, generated)) or not train <= decision < entry:
                reasons.append("prediction_time_order")
            kind = p.get("generation_kind")
            if kind == "operational":
                if mode == "synthetic": reasons.append("synthetic_cannot_claim_operational_generation")
                effective = generated
            elif kind == "replay":
                effective = timestamp(p.get("simulated_generated_at"))
                if not effective: reasons.append("replay_decision_clock_required")
                if mode == "strict": reasons.append("strict_requires_operational_predictions")
            else:
                reasons.append("unknown_generation_kind"); effective = None
            if effective and not decision <= effective < entry:
                tradable = False
            if row:
                f = row["feature"]
                if decision != f["decision_at"] or entry != f["intended_entry_at"] or p.get("feature_version_id") != row["feature_version_id"] or p.get("feature_snapshot_id") != f["feature_snapshot_id"]:
                    reasons.append("prediction_feature_or_schedule_mismatch")
                if not f.get("feature_ready_at") or (effective and timestamp(f["feature_ready_at"]) > effective):
                    reasons.append("prediction_before_feature_ready")
        except (ValueError, TypeError): reasons.append("invalid_prediction_timestamp")
        if p.get("oracle", False) and not oracle_test: reasons.append("oracle_accuracy_test_only")
        if reasons: rejected.append({"prediction_id": pid, "reasons": sorted(set(reasons))})
        else: accepted.append(p | {"tradable": tradable, "trade_block_reason": None if tradable else "generated_outside_decision_entry_window"})
    # Every supplied duplicate is unusable, including its first occurrence.
    duplicate_pairs = {pair for pair, count in Counter((p.get("sample_id"), p.get("horizon")) for p in records).items() if count > 1}
    duplicate_ids = {pid for pid, count in Counter(p.get("prediction_id") for p in records).items() if count > 1}
    accepted = [p for p in accepted if (p["sample_id"], p["horizon"]) not in duplicate_pairs and p["prediction_id"] not in duplicate_ids]
    missing = sorted(set(lookup) - {(p["sample_id"], p["horizon"]) for p in accepted})
    return {"status": "INVALID" if rejected else "PARTIAL" if missing else "OK", "mode": mode,
            "accepted": accepted, "rejected": rejected, "missing": [{"sample_id": s, "horizon": h} for s, h in missing],
            "exclusion_counts": dict(Counter(r for p in rejected for r in p["reasons"])),
            "scope": "ORACLE_ACCURACY_TEST_ONLY" if oracle_test else "fixed_external_prediction_records"}
