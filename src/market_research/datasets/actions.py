"""Auditable share/cash ledger for explicitly verified, simple synthetic/vendor actions."""
from .features import valid_number
from .contracts import LABEL_VERSION, LABEL_VERSION_ENDPOINT, label_version
from .label_dependencies import action_classification


def holdings_outcome(entry, exit_row, actions, *, coverage, include_receivables, initial_quantity=1, require_cash=True,
                     allow_assumed_coverage=False, label_definition_version=LABEL_VERSION):
    endpoint_contract = label_version(label_definition_version) == LABEL_VERSION_ENDPOINT
    if not isinstance(include_receivables,bool):
        return {"status":"invalid","reason":"invalid_receivable_policy_boolean_required"}
    accepted = coverage and (coverage.get("status") == "verified_complete" or
        (allow_assumed_coverage and coverage.get("status") == "research_assumed_complete" and coverage.get("research_assumption")))
    if not accepted:
        return {"status":"blocked","reason":"corporate_action_coverage_missing"}
    if (not endpoint_contract or require_cash) and coverage.get("same_day_order") != "split_then_dividend_postsplit":
        return {"status":"blocked","reason":"corporate_action_order_unverified"}
    if not coverage["start_date"] <= entry["trading_date"] <= exit_row["trading_date"] <= coverage["end_date"]:
        return {"status":"blocked","reason":"corporate_action_interval_not_covered"}
    if not all(valid_number(x) and x>0 for x in (entry.get("open"),exit_row.get("open"),initial_quantity)):
        return {"status":"invalid","reason":"invalid_open_or_quantity"}
    quantity=float(initial_quantity); paid=0.0; receivable=0.0; ledger=[]; used=[]
    lo,hi=entry["trading_date"],exit_row["trading_date"]
    in_interval=[]
    aliases={"split":"split","splits":"split","dividend":"dividend","dividends":"dividend"}
    for action in actions:
        kind=aliases.get(action["event_type"],action["event_type"])
        if endpoint_contract and not require_cash and action_classification(action, coverage) == "dividend":
            continue  # No ex-date, cash fields, ledger entry or availability dependency.
        if kind not in ("split","dividend"):
            event=action.get("effective_date") or action.get("ex_date")
            if event is None and action["event_date"]<=hi:
                return {"status":"blocked","reason":"unsupported_action_effective_date_unknown"}
        else:
            event=action.get("effective_date") if kind=="split" else action.get("ex_date")
        # A declaration before entry does not prove the action took effect before entry.
        # Without its effective/ex date we cannot establish that it is outside this interval.
        if not event:
            return {"status":"blocked","reason":"corporate_action_effective_or_ex_date_missing"}
        if event and lo < event <= hi: in_interval.append((event,0 if kind=="split" else 1,action))
    for event,_,action in sorted(in_interval,key=lambda a:(a[0],a[1],a[2]["version_id"])):
        used.append(action["version_id"])
        kind=aliases.get(action["event_type"],action["event_type"])
        if endpoint_contract and action_classification(action, coverage) is None:
            return {"status":"blocked","reason":"corporate_action_type_unverified_or_unsupported"}
        if kind=="split":
            a,b=action.get("numerator"),action.get("denominator")
            if not all(valid_number(x) and x>0 for x in (a,b)):
                return {"status":"blocked","reason":"split_ratio_unverified"}
            before=quantity; quantity*=a/b
            if not valid_number(quantity) or quantity<=0:
                return {"status":"invalid","reason":"nonfinite_or_underflow_holdings_result"}
            ledger.append({"date":event,"type":"split","quantity_before":before,"quantity_after":quantity,"version_id":action["version_id"]})
        elif kind=="dividend":
            if not require_cash:
                ledger.append({"date":event,"type":"dividend_excluded_from_price_return","version_id":action["version_id"]})
                continue
            if action.get("amount_semantics") != "as_declared_cash_per_share" or action.get("currency") != entry["currency"]:
                return {"status":"blocked","reason":"cash_dividend_meaning_or_currency_unverified"}
            amount=action.get("amount"); payment=action.get("payment_date")
            if not valid_number(amount) or amount<0 or not payment or payment<event:
                return {"status":"blocked","reason":"cash_dividend_payment_or_amount_unverified"}
            value=quantity*amount; is_paid=payment < hi
            if is_paid: paid+=value
            else: receivable+=value
            if not all(valid_number(v) for v in (value,paid,receivable)):
                return {"status":"invalid","reason":"nonfinite_or_underflow_holdings_result"}
            ledger.append({"date":event,"type":"dividend_right","quantity":quantity,"cash_entitlement":value,"payment_date":payment,
                           "paid_by_exit_open_conservatively":is_paid,"version_id":action["version_id"]})
        else:
            return {"status":"blocked","reason":"merger_delisting_or_action_type_unsupported"}
    investment=initial_quantity*entry["open"]; terminal=quantity*exit_row["open"]
    if not all(valid_number(v) and v>0 for v in (investment,terminal)):
        return {"status":"invalid","reason":"nonfinite_or_underflow_holdings_result"}
    price_return=terminal/investment-1
    total_return=(terminal+paid+(receivable if include_receivables else 0))/investment-1
    if not all(valid_number(v) for v in (price_return,total_return)):
        return {"status":"invalid","reason":"nonfinite_or_underflow_holdings_result"}
    return {"status":"ready","reason":None,"price_return":price_return,
            "cash_total_return":total_return,
            "initial_quantity":initial_quantity,"exit_quantity":quantity,"paid_cash":paid,"receivable_cash":receivable,
            "include_receivables":include_receivables,"reinvest_dividends":False,"ledger":ledger,"action_version_ids":used,
            "action_interpretation_policy":"split_dividend_singular_plural_aliases_v1; source fields unchanged",
            "coverage_state":("research_assumed_complete" if coverage.get("status") == "research_assumed_complete" else
                              "verified_no_actions_in_interval" if not in_interval else "verified_actions_in_interval")}
