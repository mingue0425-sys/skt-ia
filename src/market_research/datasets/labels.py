"""Future outcome arithmetic. Feature generation never calls or reads this module."""
from .actions import holdings_outcome
from .calendar import plus_seconds
from .contracts import LABEL_VERSION, LABEL_VERSION_ENDPOINT, label_version
from .evidence import assertion_availability
from .features import valid_number
from .label_dependencies import action_classification, cash_coverage_reason, endpoint_halt_reason, outside_holding_interval, price_coverage_reason, relevant_conflict
from ..pit.time import timestamp


def compute_labels(price_result, action_result, calendar, *, plan, evaluation_at, simulation_asof,
                   price_kind, coverage, trading_state, policy, assumption_rule=None,
                   include_receivables=True, processing_delay_seconds=0, normal_delay_hours=48, expected_currency=None,
                   label_definition_version=LABEL_VERSION):
    version = label_version(label_definition_version)
    endpoint_contract = version == LABEL_VERSION_ENDPOINT
    if isinstance(normal_delay_hours,bool) or not isinstance(normal_delay_hours,(int,float)) or not 0<=normal_delay_hours<=168:
        raise ValueError("invalid_normal_label_delay_hours")
    rows=[r for r in price_result["data"] if r["price_kind"]==price_kind]
    keys={(r["source"],r["security_record_id"],r["price_semantics"],r["currency"],r["session_scope"]) for r in rows}
    by_date={r["trading_date"]:r for r in rows}
    identities={r["security_record_id"] for r in rows}
    coverage_at,cov_reason=assertion_availability(coverage,policy=policy,cutoff=evaluation_at,rule=assumption_rule)
    state_at,state_reason=assertion_availability(trading_state,policy=policy,cutoff=evaluation_at,rule=assumption_rule)
    output=[]
    for h,target in plan["exits"].items():
        if endpoint_contract:
            dates = {plan["entry_date"], target["trading_date"]} if target else set()
            rows = [r for r in price_result["data"] if r["price_kind"] == price_kind and r["trading_date"] in dates]
            keys = {(r["source"],r["security_record_id"],r["price_semantics"],r["currency"],r["session_scope"]) for r in rows}
            by_date = {r["trading_date"]:r for r in rows}
            identities = {r["security_record_id"] for r in rows}
        base={"label_definition_version":version,"horizon_sessions":h,"intended_entry_at":plan["intended_entry_at"],
              "intended_exit_at":target["open_utc"] if target else None,"evaluation_at":evaluation_at,"simulation_asof":simulation_asof,
              "label_available_at":None,"availability_basis":"assumed" if policy=="research_assumed" else "policy_bound_dependencies_plus_declared_processing_delay",
              "price_return":None,"up_5":None,"down_5pct_5":None,"cash_total_return":None,"cash_total_return_status":"blocked",
              "cash_total_return_reason":"corporate_action_coverage_missing","used_price_version_ids":[],"used_action_version_ids":[],
              "query_policy":policy,"strictness":"NOT_STRICT_PIT" if policy=="research_assumed" else price_result["strictness"]}
        base.update(evidence_dependencies=[v["evidence"] for v in (coverage,trading_state) if v],
                    label_processing_time_basis="assumed_declared_processing_delay_not_actual_computation_record",
                    processing_delay_seconds=processing_delay_seconds)
        if endpoint_contract:
            base.update(cash_total_return_available_at=None, used_cash_action_version_ids=[],
                        endpoint_price_evaluation_only=True, order_execution_verified=False)
        def finish(status,reason):
            output.append(base|{"status":status,"reason":reason})
        if not target or not plan["intended_entry_at"]:
            finish("blocked","calendar_horizon_outside_provided_range"); continue
        if not plan["decision_at"]<plan["intended_entry_at"]<target["open_utc"] or evaluation_at>simulation_asof:
            finish("invalid","invalid_decision_entry_exit_or_evaluation_order"); continue
        if target["open_utc"]>simulation_asof:
            finish("pending","target_open_not_yet_reached"); continue
        if target["open_utc"]>evaluation_at:
            finish("pending","target_open_after_label_evaluation_asof"); continue
        if price_kind=="close_only":
            finish("blocked","opening_prices_unavailable_close_only"); continue
        if len(keys)>1 or len(by_date)!=len(rows):
            finish("blocked","mixed_label_series_or_duplicate_session"); continue
        if any(r["currency"] is None or (expected_currency and r["currency"]!=expected_currency) for r in rows):
            finish("blocked","label_currency_unknown_or_contract_mismatch"); continue
        holding_dates = (plan["entry_date"], target["trading_date"])
        conflict = (relevant_conflict(price_result, endpoint_dates=dates) or relevant_conflict(action_result, coverage=coverage, holding_dates=holding_dates)) if endpoint_contract else (price_result["conflict_count"] or action_result["conflict_count"])
        if conflict:
            finish("blocked","unresolved_label_query_conflict"); continue
        # Exchange sessions alone do not prove this security was tradable.
        if state_reason:
            finish("blocked","security_trading_status_unverified"); continue
        if trading_state.get("symbol") != (rows[0]["requested_symbol"] if rows else trading_state.get("symbol")) or trading_state.get("source") != (rows[0]["source"] if rows else trading_state.get("source")):
            finish("blocked","security_state_series_mismatch"); continue
        entry_date=plan["entry_date"]; exit_date=target["trading_date"]
        if trading_state.get("delisted_date") and trading_state["delisted_date"]<=exit_date:
            finish("blocked","delisting_outcome_missing_or_unsupported"); continue
        if endpoint_contract:
            halt_reason = endpoint_halt_reason(trading_state, plan["intended_entry_at"], target["open_utc"])
            if halt_reason:
                finish("blocked",halt_reason); continue
        elif entry_date in trading_state.get("halted_dates",[]) or exit_date in trading_state.get("halted_dates",[]):
            finish("blocked","target_security_halted_no_price_substitution"); continue
        if not trading_state.get("listed_from") or entry_date<trading_state["listed_from"]:
            finish("blocked","security_prelisting_or_listing_unknown"); continue
        if not trading_state.get("verified_through") or exit_date>trading_state["verified_through"]:
            finish("blocked","security_status_interval_not_covered"); continue
        entry,exit_row=by_date.get(entry_date),by_date.get(exit_date)
        if entry is None or exit_row is None or entry.get("open") is None or exit_row.get("open") is None:
            # Daily OHLC requires the bar's observation to finish; no instant availability at exit open.
            deadline=plus_seconds(target["close_utc"],normal_delay_hours*3600) if normal_delay_hours<=24 else None
            if deadline is None:
                from datetime import datetime,timedelta
                deadline=timestamp((datetime.fromisoformat(target["close_utc"].replace("Z","+00:00"))+timedelta(hours=normal_delay_hours)).isoformat())
            # The label version represents knowledge at evaluation_at. A later simulation
            # clock must not turn an ordinary wait at that past cutoff into a collection failure.
            if evaluation_at<=deadline:
                finish("pending","waiting_for_received_usable_open_price_within_delay"); continue
            finish("blocked","past_endpoint_price_missing_or_collection_failure"); continue
        if not all(valid_number(r["open"]) and r["open"]>0 for r in (entry,exit_row)):
            finish("invalid","invalid_or_zero_open_price"); continue
        if any(r["price_semantics"]!="as_traded" for r in (entry,exit_row)):
            finish("blocked","as_traded_open_required_for_quantity_return"); continue
        if cov_reason:
            finish("blocked","corporate_action_coverage_missing_or_not_available"); continue
        if coverage.get("symbol")!=entry["requested_symbol"] or coverage.get("source")!=entry["source"]:
            finish("blocked","corporate_action_coverage_series_mismatch"); continue
        if endpoint_contract:
            price_reason = price_coverage_reason(coverage, action_result["data"], entry_date, exit_date)
            if price_reason:
                finish("blocked",price_reason); continue
        price_actions = [a for a in action_result["data"] if action_classification(a, coverage) != "dividend" and not outside_holding_interval(a, entry_date, exit_date)] if endpoint_contract else action_result["data"]
        if any(a["security_record_id"] not in identities or a["source"]!=entry["source"] for a in price_actions):
            finish("blocked","corporate_action_security_or_source_mismatch"); continue
        outcome=holdings_outcome(entry,exit_row,action_result["data"],coverage=coverage,include_receivables=include_receivables,require_cash=False,
                                 allow_assumed_coverage=policy=="research_assumed", label_definition_version=version)
        if outcome["status"]!="ready":
            finish(outcome["status"],outcome["reason"]); continue
        cash_reason = cash_coverage_reason(coverage, action_result["data"], entry_date, exit_date) if endpoint_contract else None
        if endpoint_contract and relevant_conflict(action_result, coverage=coverage, require_cash=True, holding_dates=holding_dates):
            total = {"status":"blocked", "reason":"unresolved_cash_action_query_conflict"}
        elif cash_reason:
            total = {"status":"blocked", "reason":cash_reason}
        elif endpoint_contract and any(a["security_record_id"] not in identities or a["source"] != entry["source"] for a in action_result["data"] if not outside_holding_interval(a, entry_date, exit_date)):
            total = {"status":"blocked", "reason":"cash_action_security_or_source_mismatch"}
        else:
            total=holdings_outcome(entry,exit_row,action_result["data"],coverage=coverage,include_receivables=include_receivables,
                                   allow_assumed_coverage=policy=="research_assumed", label_definition_version=version)
        used=outcome["action_version_ids"]
        dependencies=[entry,exit_row]+[a for a in action_result["data"] if a["version_id"] in used]
        times = [r["availability"]["available_at"] for r in dependencies]
        if endpoint_contract:
            times = [timestamp(t) for t in times]
        available=max(times+[coverage_at,state_at,target["open_utc"]])
        available=plus_seconds(available,processing_delay_seconds)
        base.update(price_return=outcome["price_return"],up_5=outcome["price_return"]>0 if h==5 else None,
                    down_5pct_5=outcome["price_return"]<=-0.05 if h==5 else None,
                    label_available_at=available,used_price_version_ids=[entry["version_id"],exit_row["version_id"]],
                    used_action_version_ids=used,holdings=outcome,
                    cash_total_return=total.get("cash_total_return") if total["status"]=="ready" else None,
                    cash_total_return_status=total["status"],cash_total_return_reason=total.get("reason"),
                    cash_holdings=total if total["status"]=="ready" else None,
                    evidence_dependencies=[coverage["evidence"],trading_state["evidence"]])
        if endpoint_contract and total["status"] == "ready":
            cash_used = total["action_version_ids"]
            cash_dependencies = [entry, exit_row] + [a for a in action_result["data"] if a["version_id"] in cash_used]
            cash_available = plus_seconds(max([timestamp(r["availability"]["available_at"]) for r in cash_dependencies] + [coverage_at,state_at,target["open_utc"]]), processing_delay_seconds)
            base.update(cash_total_return_available_at=cash_available, used_cash_action_version_ids=cash_used)
            if cash_available > evaluation_at:
                base.update(cash_total_return=None, cash_holdings=None, cash_total_return_status="pending",
                            cash_total_return_reason="cash_label_processing_delay_not_completed")
        if available>evaluation_at:
            # Numbers may be computed for diagnostics, but are not an available label version yet.
            if endpoint_contract:
                base.update(price_return=None,up_5=None,down_5pct_5=None,holdings=None)
            else:
                base.update(price_return=None,up_5=None,down_5pct_5=None,cash_total_return=None,cash_total_return_status="pending",
                            holdings=None,cash_holdings=None)
            finish("pending","label_processing_delay_not_completed"); continue
        finish("ready",None)
    return output
