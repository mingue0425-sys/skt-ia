"""Deterministic long/cash account. Costs are assumptions, not fitted market claims."""
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_FLOOR, localcontext
from collections import Counter
import statistics
from ..pit.time import timestamp
from .inputs import check_mode
from .predictions import finite

POLICY_VERSION = "4.0.0"
CENT, PRICE_TICK = Decimal("0.01"), Decimal("0.000001")


def dec(value):
    if isinstance(value, bool): raise ValueError("boolean_not_numeric")
    number = Decimal(str(value))
    if not number.is_finite(): raise ValueError("nonfinite_simulation_number")
    return number


def money(value): return value.quantize(CENT, rounding=ROUND_HALF_EVEN)
def scalar(value): return format(value, "f")


class CostModel:
    def __init__(self, config):
        self.config = config
        self.rates = {k: dec(config.get(k, 0)) for k in ("commission_bps", "commission_fixed", "half_spread_bps",
                      "slippage_bps", "impact_bps_at_full_prior_volume", "sell_tax_bps", "other_fixed")}
        if any(v < 0 for v in self.rates.values()): raise ValueError("negative_cost_assumption")

    def quote(self, side, quantity, reference, prior_volume):
        impact = self.rates["impact_bps_at_full_prior_volume"]
        if impact and prior_volume <= 0: raise ValueError("impact_requires_prior_volume")
        rates = {"spread": self.rates["half_spread_bps"], "slippage": self.rates["slippage_bps"],
                 "impact": impact*quantity/prior_volume if impact else Decimal(0)}
        adjustment = sum(rates.values())/10000
        if adjustment >= 1: raise ValueError("execution_cost_exceeds_price")
        price = (reference*(1+adjustment if side == "buy" else 1-adjustment)).quantize(PRICE_TICK, rounding=ROUND_HALF_EVEN)
        notional = money(quantity*price)
        commission = money(notional*self.rates["commission_bps"]/10000 + self.rates["commission_fixed"])
        tax = money(notional*self.rates["sell_tax_bps"]/10000) if side == "sell" else Decimal(0)
        other = money(self.rates["other_fixed"])
        effects = {name: quantity*reference*rate/10000 for name, rate in rates.items()}
        direct = commission+tax+other
        return {"price": price, "notional": notional, "commission": commission, "tax": tax, "other": other,
                "direct": direct, "effects": effects,
                "cash_change": -(notional+direct) if side == "buy" else notional-direct,
                "execution_difference": quantity*(price-reference) if side == "buy" else quantity*(reference-price)}


def simulate(tape, signals, config, mode):
    check_mode(mode)
    with localcontext() as context:
        context.prec = 34
        return _simulate(tape, signals, config, mode)


def _simulate(tape, signals, config, mode):
    horizon = config["horizon"]
    if horizon not in (1, 5, 20): raise ValueError("single_strategy_horizon_required")
    if config.get("policy_version") != POLICY_VERSION: raise ValueError("simulation_policy_version_required")
    currency = config["currency"]
    initial = money(dec(config["initial_cash"]))
    if initial <= 0: raise ValueError("positive_initial_cash_required")
    fraction, participation = dec(config.get("allocation_fraction", 1)), dec(config.get("max_prior_volume_participation", 1))
    if not 0 < fraction <= 1 or not 0 < participation <= 1: raise ValueError("invalid_allocation_or_participation")
    if config.get("insufficient_cash_policy", "partial_cancel") not in ("reject", "partial_cancel"):
        raise ValueError("unknown_insufficient_cash_policy")
    if config.get("repeat_signal_policy", "defer") != "defer": raise ValueError("unsupported_repeat_signal_policy")
    if mode == "synthetic" and tape["input_domain"] != "synthetic": raise ValueError("synthetic_tape_required")
    if mode == "strict" and tape["input_domain"] != "market": raise ValueError("strict_market_tape_required")
    cost = CostModel(config["costs"])
    sessions = tape["calendar"]
    start, end = timestamp(config["start_at"]), timestamp(config["end_at"])
    if not start or not end or start >= end: raise ValueError("invalid_simulation_interval")
    if any(timestamp(s["open_utc"]) >= timestamp(s["close_utc"]) for s in sessions): raise ValueError("invalid_session_order")
    if len({s["trading_date"] for s in sessions}) != len(sessions) or sessions != sorted(sessions, key=lambda s: s["open_utc"]):
        raise ValueError("calendar_not_unique_chronological")
    index = {timestamp(s["open_utc"]): i for i, s in enumerate(sessions)}
    bars = {}
    for b in tape["bars"]:
        if b["currency"] != currency or b["price_semantics"] != "as_traded" or not b.get("session_scope"):
            raise ValueError("raw_price_currency_session_contract_required")
        for field in ("open", "close", "volume"):
            if b.get(field) is not None and (not finite(b[field]) or b[field] < 0 or (field != "volume" and b[field] == 0)):
                raise ValueError("invalid_tape_price_or_volume")
        identity = b["symbol"], b["date"]
        if identity in bars: raise ValueError("duplicate_tape_bar")
        bars[identity] = b
    coverage, state = tape.get("coverage"), tape.get("trading_state")
    if not coverage or coverage.get("status") != "verified_complete" or coverage.get("same_day_order") != "split_then_dividend_postsplit":
        return {"status": "BLOCKED", "mode": mode, "reason": "corporate_action_coverage_or_order_unverified"}
    if not state or not state.get("verified_through") or not state.get("listed_from"):
        return {"status": "BLOCKED", "mode": mode, "reason": "security_trading_state_unverified"}
    if not coverage["start_date"] <= start[:10] <= end[:10] <= min(coverage["end_date"], state["verified_through"]) or start[:10] < state["listed_from"]:
        return {"status": "BLOCKED", "mode": mode, "reason": "action_or_state_interval_not_covered"}
    actions, event_keys = {}, {}
    for a in tape["actions"]:
        aid = a["action_id"]
        if aid in actions and actions[aid] != a: raise ValueError("conflicting_corporate_action_id")
        event_identity = (a["symbol"], a.get("event_key", aid))
        if event_identity in event_keys and event_keys[event_identity] != aid:
            raise ValueError("duplicate_corporate_action_event_versions")
        event_keys[event_identity] = aid
        actions[aid] = a  # Identical repeated versions are processed exactly once.
        if not a.get("date"):
            return {"status": "BLOCKED", "mode": mode, "reason": "corporate_action_effective_or_ex_date_missing"}
        if a["type"] == "split" and (dec(a.get("numerator")) <= 0 or dec(a.get("denominator")) <= 0): raise ValueError("invalid_split_ratio")
        if a["type"] == "dividend" and (a.get("currency") != currency or a.get("amount_semantics") != "as_declared_cash_per_share" or
                a.get("amount") is None or dec(a["amount"]) < 0 or not a.get("payment_date") or a["payment_date"] < a["date"]):
            return {"status": "BLOCKED", "mode": mode, "reason": "dividend_cash_or_payment_contract_unverified"}
    events = []
    for s in sessions:
        for kind, clock in (("open", s["open_utc"]), ("close", s["close_utc"])):
            if start <= timestamp(clock) <= end: events.append((timestamp(clock), 0 if kind == "open" else 1, kind, s))
    for signal in signals:
        if signal["horizon"] != horizon: raise ValueError("mixed_strategy_horizons")
        if signal.get("oracle"): raise ValueError("oracle_cannot_drive_portfolio")
        effective = signal.get("simulated_generated_at") if signal["generation_kind"] == "replay" else signal["generated_at"]
        if start <= timestamp(effective) <= end: events.append((timestamp(effective), 2, "signal", signal))
    events.sort(key=lambda e: (e[0], e[1], e[3].get("prediction_id", "")))
    cash, positions, pending, orders, fills, receivables, ledger, marks = initial, {}, [], [], [], {}, [], {}
    snapshots, processed, issues, deferred, unresolved_symbols = [], set(), [], [], set()
    totals = {k: Decimal(0) for k in ("commission", "tax", "other", "spread", "slippage", "impact", "execution_difference")}

    def invariant():
        if cash < 0 or any(p["quantity"] < 0 for p in positions.values()): raise AssertionError("negative_cash_or_holdings")
        reconciled = initial + sum((dec(f["cash_change"]) for f in fills), Decimal(0)) + sum((dec(e["cash"]) for e in ledger if e["type"] == "dividend_payment"), Decimal(0))
        if reconciled != cash: raise AssertionError("cash_ledger_mismatch")

    def order(symbol, side, qty, at, origin, prior=Decimal(0)):
        value = {"order_id": str(len(orders)), "symbol": symbol, "side": side, "quantity": scalar(qty), "created_at": origin,
                 "intended_fill_at": at, "status": "created", "history": ["created"], "prior_volume": scalar(prior),
                 "filled_quantity": "0", "cancelled_quantity": "0"}
        orders.append(value)
        return value

    def status(o, value, reason=None):
        o["status"] = value; o["history"].append(value)
        if reason: o["reason"] = reason

    def execute(o, b, at):
        nonlocal cash
        qty, side, symbol = dec(o["quantity"]), o["side"], o["symbol"]
        if symbol in unresolved_symbols:
            status(o, "expired", "merger_or_delisting_consideration_missing"); return Decimal(0)
        if b is None or b.get("open") is None:
            status(o, "expired", "scheduled_open_missing"); return Decimal(0)
        if b["date"] in state.get("halted_dates", []):
            status(o, "expired", "security_halted"); return Decimal(0)
        if state.get("delisted_date") and state["delisted_date"] <= b["date"]:
            status(o, "expired", "delisting_consideration_missing"); return Decimal(0)
        reference, prior = dec(b["open"]), dec(o["prior_volume"])
        if side == "buy":
            if symbol in positions:
                status(o, "rejected", "already_holding_at_fill"); return Decimal(0)
            quote = cost.quote(side, qty, reference, prior)
            if cash + quote["cash_change"] < 0:
                if config.get("insufficient_cash_policy", "partial_cancel") == "reject":
                    status(o, "rejected", "insufficient_cash_at_fill"); return Decimal(0)
                # Monotone affordability, binary search avoids per-share loops.
                lo, hi = 0, int(qty)
                while lo < hi:
                    mid = (lo+hi+1)//2
                    if cash + cost.quote(side, Decimal(mid), reference, prior)["cash_change"] >= 0: lo = mid
                    else: hi = mid-1
                qty = Decimal(lo)
        if qty <= 0:
            status(o, "rejected", "insufficient_cash_or_zero_quantity"); return Decimal(0)
        quote = cost.quote(side, qty, reference, prior)
        if cash + quote["cash_change"] < 0:
            status(o, "rejected", "fees_exceed_sale_cash"); return Decimal(0)
        before = cash; cash += quote["cash_change"]
        fills.append({"order_id": o["order_id"], "at": at, "symbol": symbol, "side": side,
                      "quantity": scalar(qty), "reference_open": scalar(reference), "execution_price": scalar(quote["price"]),
                      "notional": scalar(quote["notional"]), "cash_before": scalar(before), "cash_after": scalar(cash),
                      "cash_change": scalar(quote["cash_change"]), "commission": scalar(quote["commission"]),
                      "tax": scalar(quote["tax"]), "other": scalar(quote["other"]),
                      "execution_difference": scalar(quote["execution_difference"]),
                      "embedded_costs": {k: scalar(v) for k, v in quote["effects"].items()}, "prior_volume": scalar(prior)})
        for k in ("commission", "tax", "other", "execution_difference"): totals[k] += quote[k]
        for k, v in quote["effects"].items(): totals[k] += v
        o["filled_quantity"] = scalar(qty)
        if qty < dec(o["quantity"]):
            status(o, "partially_filled"); o["cancelled_quantity"] = scalar(dec(o["quantity"])-qty)
            status(o, "cancelled", "opening_order_remainder_cancelled")
        else: status(o, "filled")
        if side == "buy":
            entry_index = index[at]
            exit_at = timestamp(sessions[entry_index+horizon]["open_utc"]) if entry_index+horizon < len(sessions) else None
            positions[symbol] = {"quantity": qty, "entry_at": at, "intended_exit_at": exit_at, "exit_attempted": False,
                                 "prior_volume": prior, "uncertainty": []}
            marks[symbol] = {"price": reference, "date": b["date"], "stale": False}
        else: del positions[symbol]
        return qty

    def value_at(at):
        invested = Decimal(0); details = []
        for symbol, p in sorted(positions.items()):
            mark = marks.get(symbol)
            stale = not mark or mark["date"] != at[:10] or mark.get("stale") or bool(p["uncertainty"])
            value = p["quantity"]*mark["price"] if mark else None
            if value is not None: invested += value
            details.append({"symbol": symbol, "quantity": scalar(p["quantity"]), "price": scalar(mark["price"]) if mark else None,
                            "price_date": mark["date"] if mark else None, "stale": bool(stale),
                            "value": scalar(value) if value is not None else None, "entry_at": p["entry_at"],
                            "intended_exit_at": p["intended_exit_at"], "uncertainty": list(p["uncertainty"])})
        recv = sum((r["amount"] for r in receivables.values() if not r["paid"]), Decimal(0))
        nav = cash + invested + recv if all(p["value"] is not None for p in details) else None
        return {"at": at, "cash": scalar(cash), "invested_value": scalar(invested), "dividend_receivables": scalar(recv),
                "nav": scalar(nav) if nav is not None else None, "positions": details,
                "valuation_uncertain": nav is None or any(p["stale"] for p in details),
                "invested_weight": float(invested/nav) if nav and nav > 0 else None}

    for at, _, kind, item in events:
        if kind == "signal":
            p = item; symbol = p["security_id"]
            reason = None
            if not p.get("tradable", True): reason = "generated_outside_decision_entry_window"
            elif symbol in unresolved_symbols: reason = "merger_or_delisting_consideration_missing"
            elif symbol in positions or any(o["symbol"] == symbol for o in pending): reason = "already_holding_or_pending_entry"
            elif (p["probability_up"] if p.get("output_task") == "classification" else p["predicted_return"]) <= config.get("signal_threshold", 0): reason = "signal_below_threshold"
            entry = timestamp(p["intended_entry_at"])
            if entry not in index or entry <= at: reason = reason or "entry_not_future_session_open"
            if reason:
                deferred.append({"prediction_id": p["prediction_id"], "reason": reason, "at": at}); continue
            known = [b for b in tape["bars"] if b["symbol"] == symbol and b["date"] <= at[:10] and b.get("close") is not None and
                     b.get("available_at") and timestamp(b["available_at"]) <= at]
            previous = max(known, key=lambda b: b["date"]) if known else None
            if previous is None or previous.get("volume") is None:
                deferred.append({"prediction_id": p["prediction_id"], "reason": "prior_available_price_or_volume_missing", "at": at}); continue
            qty = min((cash*fraction/dec(previous["close"])).to_integral_value(rounding=ROUND_FLOOR),
                      (dec(previous["volume"])*participation).to_integral_value(rounding=ROUND_FLOOR))
            o = order(symbol, "buy", qty, entry, at, dec(previous["volume"]))
            o.update(prediction_id=p["prediction_id"], sizing_price=scalar(dec(previous["close"])), sizing_price_date=previous["date"],
                     sizing_available_at=previous["available_at"])
            if qty <= 0: status(o, "rejected", "zero_quantity_from_cash_or_prior_volume")
            else: status(o, "pending"); pending.append(o)
        elif kind == "open":
            day = item["trading_date"]
            # Conservative date-only payments become cash on first observed session AFTER payment date.
            for aid, r in receivables.items():
                if not r["paid"] and r["payment_date"] < day:
                    cash += r["amount"]; r["paid"] = True
                    ledger.append({"type": "dividend_payment", "action_id": aid, "at": at, "cash": scalar(r["amount"])})
            for a in sorted(actions.values(), key=lambda a: (a["date"], 0 if a["type"] == "split" else 1, a["action_id"])):
                aid, symbol = a["action_id"], a["symbol"]
                if aid in processed or a["date"] > day or a["date"] < start[:10]: continue
                processed.add(aid)
                if a["type"] == "split":
                    ratio = dec(a["numerator"])/dec(a["denominator"])
                    before = positions.get(symbol, {}).get("quantity", Decimal(0))
                    if symbol in positions:
                        positions[symbol]["quantity"] *= ratio; positions[symbol]["prior_volume"] *= ratio
                    if symbol in marks: marks[symbol]["price"] /= ratio
                    for o in pending:
                        if o["symbol"] == symbol:
                            o["quantity"] = scalar(dec(o["quantity"])*ratio)
                            o["prior_volume"] = scalar(dec(o["prior_volume"])*ratio)
                            o["sizing_price"] = scalar(dec(o["sizing_price"])/ratio)
                    ledger.append({"type": "split", "action_id": aid, "at": at, "symbol": symbol,
                                   "quantity_before": scalar(before), "quantity_after": scalar(before*ratio), "ratio": scalar(ratio)})
                elif a["type"] == "dividend":
                    qty = positions.get(symbol, {}).get("quantity", Decimal(0))
                    amount = money(qty*dec(a["amount"]))
                    receivables[aid] = {"amount": amount, "payment_date": a["payment_date"], "paid": False}
                    ledger.append({"type": "dividend_right", "action_id": aid, "at": at, "quantity": scalar(qty), "receivable": scalar(amount)})
                else:
                    unresolved_symbols.add(symbol)
                    if symbol in positions: positions[symbol]["uncertainty"].append("merger_or_delisting_consideration_missing")
                    issues.append({"symbol": symbol, "at": at, "reason": "merger_or_delisting_consideration_missing"})
            for symbol, p in list(positions.items()):
                if state.get("delisted_date") and day >= state["delisted_date"] and "delisting_consideration_missing" not in p["uncertainty"]:
                    p["uncertainty"].append("delisting_consideration_missing")
                if p["intended_exit_at"] == at and not p["exit_attempted"]:
                    p["exit_attempted"] = True
                    o = order(symbol, "sell", p["quantity"], at, at, p["prior_volume"])
                    status(o, "pending")
                    if execute(o, bars.get((symbol, day)), at) == 0:
                        p["uncertainty"].append(o["reason"])
                        issues.append({"symbol": symbol, "at": at, "reason": o["reason"]})
            for o in list(pending):
                if o["intended_fill_at"] == at:
                    execute(o, bars.get((o["symbol"], day)), at); pending.remove(o)
        else:
            for symbol in positions:
                b = bars.get((symbol, item["trading_date"]))
                halted = item["trading_date"] in state.get("halted_dates", [])
                delisted = state.get("delisted_date") and item["trading_date"] >= state["delisted_date"]
                if b and b.get("close") is not None and not halted and not delisted and symbol not in unresolved_symbols:
                    marks[symbol] = {"price": dec(b["close"]), "date": b["date"], "stale": False}
                elif symbol in marks: marks[symbol]["stale"] = True
            snapshots.append(value_at(at))
        invariant()
    terminal = value_at(end)
    values = [initial] + [dec(s["nav"]) for s in snapshots if s["nav"] is not None]
    if terminal["nav"] is not None and (not snapshots or snapshots[-1]["at"] != end): values.append(dec(terminal["nav"]))
    peak, drawdown = initial, Decimal(0)
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak-value)/peak)
    uncertain = terminal["valuation_uncertain"] or any(s["valuation_uncertain"] for s in snapshots) or bool(issues)
    weights = [s["invested_weight"] for s in snapshots if s["invested_weight"] is not None]
    avg_nav = statistics.fmean(float(s["nav"]) for s in snapshots if s["nav"] is not None) if snapshots else float(initial)
    turnover = sum(float(f["notional"]) for f in fills)/avg_nav if avg_nav > 0 else None
    pending_state = [dict(o) for o in pending]
    result = {"status": "PARTIAL" if uncertain or pending_state or terminal["positions"] else "OK", "mode": mode,
              "performance_grade": "CONDITIONAL" if uncertain else "PASS_SYNTHETIC" if mode == "synthetic" else "CONDITIONAL",
              "historical_performance": "BLOCKED" if mode != "strict" else "CONDITIONAL",
              "initial_cash": scalar(initial), "currency": currency, "daily_nav": snapshots, "terminal": terminal,
              "pending_orders": pending_state, "orders": orders, "fills": fills, "corporate_action_ledger": ledger,
              "receivables": {aid: {**r, "amount": scalar(r["amount"])} for aid, r in receivables.items()},
              "deferred_signals": deferred, "issues": issues,
              "performance": {"total_return": float(dec(terminal["nav"])/initial-1) if terminal["nav"] is not None and not uncertain else None,
                              "reference_total_return": float(dec(terminal["nav"])/initial-1) if terminal["nav"] is not None else None,
                              "return_reason": "unresolved_or_stale_valuation" if uncertain else None,
                              "max_drawdown": float(drawdown) if not uncertain else None,
                              "reference_max_drawdown": float(drawdown), "turnover_one_way": turnover,
                              "average_invested_weight": statistics.fmean(weights) if weights else None,
                              "max_invested_weight": max(weights) if weights else None,
                              "trade_count": sum(f["side"] == "sell" for f in fills), "fill_count": len(fills),
                              "order_status_counts": dict(Counter(o["status"] for o in orders)),
                              "partial_fill_count": sum("partially_filled" in o["history"] for o in orders),
                              "unfilled_order_count": sum(o["filled_quantity"] == "0" for o in orders),
                              "costs": {k: scalar(v) for k, v in totals.items()},
                              "sharpe": None, "sharpe_reason": "not_provided_stage4_descriptive_accounting_only"},
              "assumptions": {"entry": "next_regular_session_open_hypothesis", "settlement": "immediate_cash_reuse",
                              "event_order": "date-only prior-day payments, split, ex-dividend rights, exits, entries, close marks, signals",
                              "liquidity": "previous_available_volume_only", "exit_failure": "expire_once_preserve_position_no_reschedule",
                              "fractional_shares": "split_created_fractions_preserved_no_cash_in_lieu", "force_terminal_liquidation": False,
                              "costs": "explicit_assumptions_not_actual_cost_estimates", "turnover": "sum_buy_plus_sell_notional/mean_daily_nav; one_way"}}
    return result
