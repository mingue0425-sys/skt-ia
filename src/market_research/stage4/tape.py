"""Execution tape assembled only from hash-verified fixed snapshot dependencies."""
from ..pit import replay_snapshot
from ..datasets.calendar import SessionCalendar
from ..datasets.evidence import load_assertion
from ..storage import canonical, sha256


def frozen_tape(pit, fixed, rows):
    config = fixed["metadata"]["config"]
    domain = config.get("input_domain", "market")
    cal = SessionCalendar(pit, config["calendar_version"], input_domain=domain)
    snapshots, prices, actions = {}, {}, {}
    for row in rows:
        for payload, field in ((row["feature"], "feature_snapshot_id"), (row["label"], "label_snapshot_id"), (row["label"], "action_snapshot_id")):
            sid = payload.get(field)
            if not sid or sid in snapshots: continue
            result = replay_snapshot(pit, sid, verify_files=True)["result"]
            snapshots[sid] = sid
            for r in result["data"]:
                if field == "action_snapshot_id":
                    identity = (r["source"], r["security_record_id"], r["event_key"])
                    previous = actions.get(identity)
                    fields = ("event_type", "event_date", "effective_date", "ex_date", "payment_date",
                              "numerator", "denominator", "amount", "amount_semantics", "currency")
                    if previous and any(previous.get(k) != r.get(k) for k in fields):
                        raise ValueError("conflicting_frozen_corporate_action_versions")
                    if previous is None or r["version_id"] < previous["version_id"]:
                        actions[identity] = r
                elif r["price_kind"] == "ohlcv" and r["price_semantics"] == "as_traded":
                    identity = (r["security_record_id"], r["trading_date"])
                    if identity in prices and prices[identity]["version_id"] != r["version_id"]:
                        if any(prices[identity][k] != r[k] for k in ("open", "close", "volume")):
                            raise ValueError("conflicting_frozen_execution_prices")
                    prices[identity] = r
    coverage = load_assertion(config.get("action_coverage"), kind="corporate_action_coverage", domain=domain)
    state = load_assertion(config.get("trading_state"), kind="security_trading_state", domain=domain)
    bars = [{"symbol": r["security_record_id"], "date": r["trading_date"], "open": r["open"], "close": r["close"],
             "volume": r["volume"], "available_at": r["availability"]["available_at"], "currency": r["currency"],
             "price_semantics": r["price_semantics"], "session_scope": r["session_scope"], "version_id": r["version_id"]}
            for r in sorted(prices.values(), key=lambda x: (x["trading_date"], x["security_record_id"]))]
    events = []
    for r in actions.values():
        kind = {"splits": "split", "dividends": "dividend"}.get(r["event_type"], r["event_type"])
        events.append({"action_id": r["version_id"], "symbol": r["security_record_id"], "type": kind,
                       "event_key": r["event_key"],
                       "date": r.get("effective_date") if kind == "split" else r.get("ex_date"),
                       "numerator": r.get("numerator"), "denominator": r.get("denominator"), "amount": r.get("amount"),
                       "amount_semantics": r.get("amount_semantics"), "currency": r.get("currency"), "payment_date": r.get("payment_date")})
    tape = {"version": "4.0.0", "input_domain": domain, "dataset_snapshot_id": fixed["snapshot_id"],
            "snapshot_dependencies": sorted(snapshots), "calendar": [{k: r[k] for k in ("trading_date", "open_utc", "close_utc")} for r in cal.rows],
            "calendar_metadata": cal.metadata, "bars": bars, "actions": sorted(events, key=lambda a: a["action_id"]),
            "coverage": coverage, "trading_state": state}
    return tape, sha256(canonical(tape))
