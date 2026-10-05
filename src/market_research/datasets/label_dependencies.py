"""Evidence and dependency rules exclusive to opt-in label contract 3.1.0."""
from ..pit.time import timestamp, day


def outside_holding_interval(action, entry_date, exit_date):
    kind = action.get("event_type")
    event = (action.get("effective_date") if kind in ("split", "splits") else action.get("ex_date")
             if kind in ("dividend", "dividends") else action.get("effective_date") or action.get("ex_date"))
    return event is not None and not entry_date < day(event) <= exit_date


def action_classification(action, coverage):
    # Cash semantics/amounts do not distinguish a special distribution from an ordinary one.
    kind = action.get("event_type")
    classifications = coverage.get("action_classifications", {}) if coverage else {}
    reviewed = classifications.get(action.get("version_id")) if isinstance(classifications, dict) else None
    if kind in ("split", "splits") and reviewed == "share_split":
        return "split"
    if kind in ("dividend", "dividends") and reviewed == "ordinary_cash_dividend":
        return "dividend"
    return None


def relevant_conflict(result, *, endpoint_dates=None, coverage=None, require_cash=False, holding_dates=None):
    if not result["conflict_count"]:
        return False
    conflicts = result.get("conflicts")
    if not conflicts or len(conflicts) != result["conflict_count"]:
        return True  # A count with no scoped evidence must not disappear.
    classifications = coverage.get("action_classifications", {}) if coverage else {}
    for conflict in conflicts:
        if endpoint_dates is not None:
            key = conflict.get("key", [])
            date = conflict.get("date")
            if date is None and conflict.get("kind") == "unordered_same_source_versions" and len(key) >= 3:
                date = key[2]
            if date is None and conflict.get("kind") == "cross_provider_disagreement" and len(key) >= 2:
                date = key[1]
            if date is not None and date not in endpoint_dates:
                continue
        elif not require_cash:
            ids = conflict.get("version_ids")
            # Only version-bound ordinary cash conflicts can be excluded from price arithmetic.
            if ids and isinstance(classifications, dict) and all(classifications.get(i) == "ordinary_cash_dividend" for i in ids):
                continue
        if holding_dates is not None:
            ids = conflict.get("version_ids")
            dates = coverage.get("action_effective_dates", {}) if coverage else {}
            if ids and all(i in dates for i in ids) and all(not holding_dates[0] < day(dates[i]) <= holding_dates[1] for i in ids):
                continue
        return True
    return False


def cash_coverage_reason(coverage, actions, entry_date, exit_date):
    required = coverage.get("cash_action_version_ids")
    if not isinstance(required, list) or any(not isinstance(i, str) for i in required) or len(set(required)) != len(required):
        return "cash_action_coverage_unverified"
    by_id = {a["version_id"]:a for a in actions}
    required = interval_required_ids(required, by_id, coverage, entry_date, exit_date)
    if any(i not in by_id for i in required):
        return "cash_action_required_version_unavailable"
    if any(action_classification(by_id[i], coverage) != "dividend" for i in required):
        return "cash_action_coverage_type_unverified"
    for action in actions:
        ex_date = action.get("ex_date")
        if action_classification(action, coverage) == "dividend" and ex_date and entry_date < ex_date <= exit_date and action["version_id"] not in required:
            return "cash_action_coverage_mismatch"
    return None


def interval_required_ids(required, by_id, coverage, entry_date, exit_date):
    dates = coverage.get("action_effective_dates", {})
    # These dates must be reviewed against the exact action version, never its declaration date.
    return [i for i in required if not (i in by_id and outside_holding_interval(by_id[i], entry_date, exit_date))
            and not (i not in by_id and i in dates and not entry_date < day(dates[i]) <= exit_date)]


def price_coverage_reason(coverage, actions, entry_date, exit_date):
    required = coverage.get("price_action_version_ids")
    if not isinstance(required, list) or any(not isinstance(i, str) for i in required) or len(set(required)) != len(required):
        return "price_action_coverage_unverified"
    by_id = {a["version_id"]:a for a in actions}
    required = interval_required_ids(required, by_id, coverage, entry_date, exit_date)
    if any(i not in by_id for i in required):
        return "price_action_required_version_unavailable"
    if any(action_classification(by_id[i], coverage) != "split" for i in required):
        return "corporate_action_type_unverified_or_unsupported"
    if any(action_classification(a, coverage) != "dividend" and not outside_holding_interval(a, entry_date, exit_date)
           and a["version_id"] not in required for a in actions):
        return "price_action_coverage_mismatch"
    return None


def endpoint_halt_reason(state, entry_at, exit_at):
    if state.get("halted_dates"):
        return "security_halt_date_only_evidence"
    coverage = state.get("halt_coverage")
    intervals = state.get("halt_intervals")
    if (not isinstance(coverage, dict) or coverage.get("status") != "verified_complete"
            or coverage.get("includes_prior_unresolved_halts") is not True or not isinstance(intervals, list)):
        return "security_halt_interval_coverage_unverified"
    try:
        lo, hi = timestamp(coverage.get("start_at")), timestamp(coverage.get("end_at"))
        targets = (timestamp(entry_at), timestamp(exit_at))
        if not lo or not hi or lo > hi or not all(lo <= t <= hi for t in targets):
            return "security_halt_interval_not_covered"
        parsed = []
        for interval in intervals:
            start = timestamp(interval.get("start_at"))
            # A missing trading resumption is unresolved, including quote-only resumption.
            end = timestamp(interval.get("resumed_at"))
            if not start or (end is not None and end <= start):
                return "security_halt_interval_invalid"
            parsed.append((start, end))
    except (AttributeError, TypeError, ValueError):
        return "security_halt_interval_invalid"
    for target in targets:
        for start, end in parsed:
            if start <= target and (end is None or target < end):
                return "target_security_halted_or_resumption_unknown"
            if end == target:
                return "target_security_resumption_boundary_unverified"
    return None
