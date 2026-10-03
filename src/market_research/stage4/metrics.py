"""Descriptive metrics only; overlapping horizons are not independent evidence."""
import math
import statistics
from collections import Counter, defaultdict
from ..pit.time import timestamp
from .predictions import finite
from .inputs import contract_reasons, key


def mean(values):
    if not values or any(v is None or not finite(v) for v in values): return None
    scale = max(abs(v) for v in values)
    return statistics.fmean(v/scale for v in values)*scale if scale else 0.0


def ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    output = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]: j += 1
        for index in order[i:j]: output[index] = (i + 1 + j) / 2
        i = j
    return output


def rank_ic(pairs):
    if len(pairs) < 2: return {"value": None, "reason": "fewer_than_two_securities", "count": len(pairs)}
    x, y = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    mx, my = mean(x), mean(y)
    vx, vy = sum((v-mx)**2 for v in x), sum((v-my)**2 for v in y)
    if vx == 0 or vy == 0: return {"value": None, "reason": "constant_predictions_or_targets", "count": len(pairs)}
    return {"value": sum((a-mx)*(b-my) for a, b in zip(x, y))/math.sqrt(vx*vy), "reason": None, "count": len(pairs)}


def evaluate(rows, predictions, *, evaluation_asof, mode, horizon, oracle_test=False):
    asof = timestamp(evaluation_asof)
    if not asof: raise ValueError("evaluation_asof_required")
    pred = {(p["sample_id"], p["horizon"]): p for p in predictions}
    excluded, daily = Counter(), defaultdict(list)
    bins = [[] for _ in range(10)]
    qloss, coverage = defaultdict(list), defaultdict(list)
    for r in rows:
        if r["horizon_sessions"] != horizon: continue
        f, l = r["feature"], r["label"]
        p = pred.get(key(r))
        reasons = contract_reasons(r, mode)
        if not p: reasons.append("missing_prediction")
        if not l.get("label_available_at") or timestamp(l["label_available_at"]) > asof: reasons.append("immature_label")
        if mode == "strict" and l.get("learning_rights_available_at") and timestamp(l["learning_rights_available_at"]) > asof:
            reasons.append("learning_rights_after_evaluation_asof")
        target = p.get("target_id", "price_return") if p else "price_return"
        if target == "cash_total_return" and l.get("cash_total_return_status") != "ready": reasons.append("total_return_not_ready")
        if not finite(l.get(target)): reasons.append("missing_or_invalid_target")
        if p and p.get("oracle") and not oracle_test: reasons.append("oracle_not_general_performance")
        if reasons:
            excluded.update(set(reasons)); continue
        actual, predicted, prob = l[target], p["predicted_return"], p["probability_up"]
        up = int(actual > 0)  # Zero is the non-up class, shared with Stage3 up_5.
        clipped = min(1-1e-15, max(1e-15, prob)) if prob is not None else None
        error = predicted-actual if predicted is not None else None
        squared = error*error if error is not None else None
        values = {"mae": abs(error) if finite(error) else None, "mse": squared if finite(squared) else None,
                  "direction_accuracy": float((predicted > 0 if predicted is not None else prob > .5) == bool(up)), "brier": (prob-up)**2 if prob is not None else None,
                  "binary_log_loss": -(up*math.log(clipped)+(1-up)*math.log1p(-clipped)) if clipped is not None else None,
                  "predicted": predicted, "actual": actual, "security_ids": tuple(f["security_record_ids"])}
        daily[f["decision_at"][:10]].append(values)
        if prob is not None: bins[min(9, int(prob*10))].append((prob, up))
        if p.get("predicted_quantiles"):
            qs = sorted((float(k), v) for k, v in p["predicted_quantiles"].items())
            for q, value in qs:
                error = actual-value
                loss = max(q*error, (q-1)*error)
                qloss[str(q)].append(loss if finite(loss) else None)
            if len(qs) >= 2:
                lo, hi = qs[0], qs[-1]
                coverage[str(lo[0])+":"+str(hi[0])].append(float(lo[1] <= actual <= hi[1]))
    names = ("mae", "mse", "direction_accuracy", "brier", "binary_log_loss")
    metrics = {}
    for name in names:
        value = mean([x[name] for group in daily.values() for x in group])
        metrics[name] = {"value": value, "reason": "no_mature_eligible_predictions" if not daily else "output_not_provided" if value is None and (all(p.get("predicted_return") is None for p in predictions) if name in ("mae", "mse") else all(p.get("probability_up") is None for p in predictions) if name in ("brier", "binary_log_loss") else False) else "numeric_overflow" if value is None else None}
    by_date = {d: {"count": len(group), **{name: mean([x[name] for x in group]) for name in names},
                   "rank_ic": rank_ic([(x["predicted"], x["actual"]) for x in group if x["predicted"] is not None]) if len({x["security_ids"] for x in group}) == len(group)
                   else {"value": None, "reason": "duplicate_security_on_decision_date", "count": len(group)}} for d, group in sorted(daily.items())}
    ics = [v["rank_ic"]["value"] for v in by_date.values() if v["rank_ic"]["value"] is not None]
    return {"status": "OK" if daily else "INSUFFICIENT_DATA", "mode": mode, "horizon": horizon,
            "evaluation_asof": asof, "count": sum(len(g) for g in daily.values()), "metrics_row_weighted": metrics,
            "metrics_equal_date_weighted": {name: mean([v[name] for v in by_date.values()]) for name in names},
            "rank_ic_equal_date_mean": {"value": mean(ics), "reason": None if ics else "no_defined_daily_ic"},
            "daily": by_date, "exclusion_counts": dict(excluded),
            "calibration": [{"lower": i/10, "upper": (i+1)/10, "count": len(group),
                             "mean_probability": mean([p for p, _ in group]), "observed_up_frequency": mean([y for _, y in group])}
                            for i, group in enumerate(bins)],
            "pinball_loss": {q: mean(v) for q, v in sorted(qloss.items())} or None,
            "pinball_loss_reasons": {q: "numeric_overflow" for q, v in qloss.items() if mean(v) is None},
            "interval_coverage": {q: {"value": mean(v), "count": len(v)} for q, v in coverage.items()} or None,
            "quantile_reason": None if qloss else "no_valid_quantile_input",
            "log_loss_epsilon": 1e-15, "zero_return_class": "not_up",
            "scope": "ORACLE_ACCURACY_TEST_ONLY" if oracle_test else "descriptive; overlapping labels; no significance or investment performance claim"}
