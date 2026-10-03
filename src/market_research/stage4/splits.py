"""Decision-date splits with actual timestamp purging, no future training windows."""
from datetime import date, datetime, time, timezone
from collections import Counter
from ..pit.time import timestamp
from .inputs import select_at, key


def midnight(day):
    return timestamp(datetime.combine(date.fromisoformat(day), time(), timezone.utc).isoformat())


def plans(rows, spec):
    dates = sorted({r["feature"]["decision_at"][:10] for r in rows if r["feature"].get("decision_at")})
    kind = spec["kind"]
    if kind == "explicit":
        return spec["folds"]
    if kind not in ("expanding", "rolling"): raise ValueError("unknown_split_kind")
    n, v, t, step = (spec[k] for k in ("train_dates", "validation_dates", "test_dates", "step_dates"))
    gap = spec.get("gap_dates", 0)
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in (n, v, t, step)) or not isinstance(gap, int) or gap < 0:
        raise ValueError("invalid_split_sizes")
    output = []
    for stop in range(n, len(dates)-v-t-2*gap+1, step):
        val, test = stop+gap, stop+gap+v+gap
        output.append({"train": [dates[0 if kind == "expanding" else stop-n], dates[stop-1]],
                       "validation": [dates[val], dates[val+v-1]], "test": [dates[test], dates[test+t-1]],
                       "training_asof": midnight(dates[val])})
    return output


def make_splits(store, pit, dataset_id, rows, spec, mode):
    horizon = spec.get("horizon")
    if horizon not in (1, 5, 20): raise ValueError("single_task_horizon_required")
    minimum = spec.get("min_samples", {"train": 1, "validation": 1, "test": 1})
    if any(isinstance(minimum.get(k), bool) or not isinstance(minimum.get(k), int) or minimum[k] < 1 for k in ("train", "validation", "test")):
        raise ValueError("invalid_min_samples")
    folds = []
    for i, plan in enumerate(plans(rows, spec)):
        intervals = [plan[k] for k in ("train", "validation", "test")]
        for lo, hi in intervals:
            date.fromisoformat(lo); date.fromisoformat(hi)
            if lo > hi: raise ValueError("reversed_split_range")
        if not intervals[0][1] < intervals[1][0] <= intervals[1][1] < intervals[2][0]:
            raise ValueError("split_ranges_overlap_or_not_chronological")
        asof = timestamp(plan["training_asof"])
        first_val = min((r["feature"]["decision_at"] for r in rows if r["feature"].get("decision_at") and
                         intervals[1][0] <= r["feature"]["decision_at"][:10] <= intervals[1][1]), default=midnight(intervals[1][0]))
        if not midnight(intervals[0][1]) < asof <= first_val:
            raise ValueError("training_asof_outside_past_to_future_boundary")
        first_test = min((r["feature"]["decision_at"] for r in rows if r["feature"].get("decision_at") and
                          intervals[2][0] <= r["feature"]["decision_at"][:10] <= intervals[2][1]), default=midnight(intervals[2][0]))
        selected, audit = select_at(store, pit, dataset_id, rows, asof, mode)
        eligible = {key(r) for r in selected}
        groups = {k: [] for k in ("train", "validation", "test")}
        excluded = []
        for r in rows:
            if r["horizon_sessions"] != horizon: continue
            f, l = r["feature"], r["label"]
            d = (f.get("decision_at") or "")[:10]
            part = next((k for k in groups if plan[k][0] <= d <= plan[k][1]), None)
            if part is None: continue
            reasons = []
            if part == "train":
                if key(r) not in eligible: reasons.append("training_ineligible_at_asof")
                # Conservative closed label interval [entry, exit]; touching the next decision is purged.
                if not l.get("intended_exit_at") or timestamp(l["intended_exit_at"]) >= first_val:
                    reasons.append("label_interval_overlaps_validation")
            else:
                # Evaluation membership is planned without inspecting outcome readiness or values.
                if not f.get("decision_at") or not l.get("intended_exit_at"): reasons.append("invalid_schedule")
                if part == "validation" and l.get("intended_exit_at") and timestamp(l["intended_exit_at"]) >= first_test:
                    reasons.append("validation_label_overlaps_test")
            item = {k: r[k] for k in ("sample_id", "horizon_sessions", "feature_version_id", "label_version_id")}
            if reasons: excluded.append(item | {"partition": part, "reasons": reasons})
            else: groups[part].append(item)
        status = "OK" if all(len(groups[k]) >= minimum[k] for k in groups) else "INSUFFICIENT_DATA"
        folds.append({"fold_id": str(i), "status": status, "mode": mode, "plan": plan, "training_asof": asof,
                      "partitions": groups, "counts": {k: len(v) for k, v in groups.items()},
                      "excluded": excluded, "exclusion_counts": dict(Counter(x for r in excluded for x in r["reasons"])),
                      "training_audit": audit})
    return {"status": "OK" if folds and all(f["status"] == "OK" for f in folds) else "INSUFFICIENT_DATA",
            "mode": mode, "dataset_snapshot_id": dataset_id, "spec": spec, "folds": folds,
            "multi_horizon_policy": "task_specific; only the declared horizon must mature",
            "gap": "decision dates omitted by plan", "purging": "closed label interval cannot touch next partition decision",
            "embargo": "none; no future training segment exists"}
