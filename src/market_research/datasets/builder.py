"""Feature query is completed and sealed before independent future label queries."""
from collections import Counter
import copy
from ..pit import query, create_snapshot, replay_snapshot
from ..pit.time import timestamp
from ..storage import canonical, sha256
from .calendar import SessionCalendar, plus_seconds
from .contracts import FEATURE_VERSION, LABEL_VERSION, DATASET_VERSION, code_hash, label_version
from .evidence import load_assertion, assertion_availability
from .features import compute_features
from .labels import compute_labels


def request_contract(config, policy_key):
    policy=config.get(policy_key)
    if policy not in ("observed","historical_verified","research_assumed"): raise ValueError("explicit_feature_and_label_policies_required")
    series=config["series"]
    if not series.get("source") or not series.get("symbol") or not series.get("price_semantics") or not series.get("price_kind"):
        raise ValueError("explicit_single_series_contract_required")
    return {"policy":policy,"symbols":[series["symbol"]],"sources":[series["source"]],
            "price_semantics":(series["price_semantics"],),"identifier_requirement":config.get("identifier_requirement","verified"),
            "symbol_mode":config.get("symbol_mode","effective_mapping"),"calendar_version":config["calendar_version"],
            "input_domain":config.get("input_domain","market"),"session_requirement":config.get("session_requirement"),
            "assumption_rule":config.get("assumption_rule") if policy=="research_assumed" else None}


def generate_feature(db, calendar, sample, config):
    plan=calendar.plan(sample["decision_date"])
    decision=timestamp(sample.get("decision_at",plan["decision_at"]))
    cutoff=timestamp(sample.get("feature_cutoff",decision))
    if cutoff>decision or decision!=plan["decision_at"]: raise ValueError("feature_cutoff_or_decision_contract_invalid")
    expected=calendar.window(sample["decision_date"],61)
    result=query(db,cutoff=cutoff,start=expected[0],end=sample["decision_date"],**request_contract(config,"feature_policy"))
    frozen=create_snapshot(db,result)
    computed=compute_features(result,calendar,anchor_date=sample["decision_date"],price_kind=config["series"]["price_kind"],
                              as_traded_mode=config.get("as_traded_mode"),expected_semantics=config["series"]["price_semantics"],
                              expected_currency=config["series"].get("currency"))
    # Numeric field lists are transient. Snapshots, not duplicated raw rows, are stored in feature versions.
    inputs=computed.pop("selected_inputs")
    if sample.get("feature_ready_at"):
        ready=timestamp(sample["feature_ready_at"])
        basis=sample.get("feature_ready_basis")
        if basis not in ("actual_operational_record","assumed"): raise ValueError("explicit_feature_ready_basis_required")
        if ready<cutoff or (inputs and ready<max(r["availability"]["available_at"] for r in inputs)):
            raise ValueError("feature_ready_before_input_availability")
    else:
        ready=plus_seconds(cutoff,config.get("feature_processing_delay_seconds",0)) if inputs else None
        basis="assumed_dependency_processing_schedule"
    sample_id=sha256(canonical({"series":config["series"],"decision_at":decision,"calendar_version":calendar.version,
                              "input_domain":config.get("input_domain","market")}))
    reasons=list(computed["reasons"])
    if not plan["intended_entry_at"]: reasons.append("entry_outside_calendar_range")
    if ready and plan["intended_entry_at"] and ready>plan["intended_entry_at"]: reasons.append("feature_ready_after_intended_entry")
    if not result["data"]: reasons.append("no_eligible_feature_inputs")
    identity=sorted({r["security_record_id"] for r in inputs})
    levels=sorted({r["identifier_verification"] for r in inputs})
    strict=(bool(inputs) and config["feature_policy"]=="historical_verified" and levels==["verified"] and
            calendar.metadata["historical_notice_verified"] and all(r["session_scope"]!="vendor_daily_unverified" and
            r["provenance"]["publication_evidence"] and r["volume_adjustment_basis"]!="unknown" for r in inputs))
    payload = computed|{"sample_id":sample_id,"security_record_ids":identity,"identifier_verification":levels,
        "decision_at":decision,"feature_cutoff":cutoff,"feature_ready_at":ready,"feature_ready_basis":basis,
        "intended_entry_at":plan["intended_entry_at"],"feature_snapshot_id":frozen["snapshot_id"],
        "feature_snapshot_sha256":frozen["content_sha256"],"query_policy":config["feature_policy"],
        "assumption_policy_id":config.get("assumption_rule",{}).get("id") if config["feature_policy"]=="research_assumed" else None,
        "strictness":result["strictness"],"query_exclusion_counts":result["exclusion_counts"],"query_conflicts":result["conflicts"],
        "latest_access_failures":result["latest_access_failures"],
        "sample_status":"invalid" if computed["status"]=="invalid" else "blocked" if reasons else "ready","sample_reasons":sorted(set(reasons)),
        "research_calculable":bool(inputs) and computed["status"]!="invalid","historical_pit_eligible":strict and not reasons,
        "execution_status":"invalid" if computed["status"]=="invalid" else "blocked" if reasons else "conditional" if basis!="actual_operational_record" else "ready",
        "calendar":calendar.metadata,"input_domain":config.get("input_domain","market"),"builder_code_sha256":code_hash(),
        "definition_version":FEATURE_VERSION,"assumptions":[{"id":"feature_processing_schedule_v1","basis":basis,"delay_seconds":config.get("feature_processing_delay_seconds",0)}],
        "price_contract":config["series"]}
    if config.get("scope_contract"):
        from ..observed.scope import validate_scope
        payload["scope_contract"] = validate_scope(config["scope_contract"], config["series"])
        required = config.get("required_features", [])
        if not required: raise ValueError("scoped_required_features_must_be_fixed")
        payload["required_features"] = list(required)
        if config.get("price_contract_evidence"):
            review = load_assertion(config["price_contract_evidence"],kind="price_contract_review",domain=config.get("input_domain","market"))
            _, review_reason = assertion_availability(review,policy=config["feature_policy"],cutoff=cutoff,rule=config.get("assumption_rule"))
            if review_reason: raise ValueError("feature_price_review_"+review_reason)
            payload.setdefault("evidence_dependencies",[]).append(review["evidence"])
        payload["scoped_input_eligible"] = bool(inputs and not reasons and levels == ["verified"] and
            all(r["session_scope"] != "vendor_daily_unverified" for r in inputs) and
            all(payload["features"].get(name, {}).get("status") == "ready" for name in required) and
            ("volume_to_prior20_mean" not in required or all(r["volume_adjustment_basis"] != "unknown" for r in inputs)))
    return payload


def generate_labels(db, calendar, sample, config):
    # Only identifiers and schedule enter here: no feature numeric data/result argument.
    version = label_version(config.get("label_definition_version", LABEL_VERSION))
    plan=calendar.plan(sample["decision_date"])
    evaluation=timestamp(config["label_asof"]); simulation=timestamp(config["simulation_asof"])
    target_dates=[r["trading_date"] for r in plan["exits"].values() if r]
    start=plan["entry_date"] or sample["decision_date"]; end=max(target_dates) if target_dates else start
    label_config=copy.deepcopy(config)
    label_config["series"]=config.get("label_series",config["series"])
    if label_config["series"]["source"]!=config["series"]["source"] or label_config["series"]["symbol"]!=config["series"]["symbol"]:
        raise ValueError("cross_source_or_security_labels_require_verified_linkage")
    prices=query(db,cutoff=evaluation,start=start,end=end,**request_contract(label_config,"label_policy"))
    action_request=request_contract(label_config,"label_policy")
    # event_date may be an earlier declaration, while effective/ex dates fall inside the holding
    # interval. Select eligible action versions independently, then filter on their proper dates.
    actions=query(db,cutoff=evaluation,family="actions",**action_request)
    pmeta=create_snapshot(db,prices); ameta=create_snapshot(db,actions)
    domain=config.get("input_domain","market")
    coverage=load_assertion(config.get("action_coverage"),kind="corporate_action_coverage",domain=domain)
    state=load_assertion(config.get("trading_state"),kind="security_trading_state",domain=domain)
    labels=compute_labels(prices,actions,calendar,plan=plan,evaluation_at=evaluation,simulation_asof=simulation,
                          price_kind=label_config["series"]["price_kind"],coverage=coverage,trading_state=state,policy=config["label_policy"],
                          assumption_rule=config.get("assumption_rule"),include_receivables=config.get("include_dividend_receivables",True),
                          processing_delay_seconds=config.get("label_processing_delay_seconds",0),normal_delay_hours=config.get("normal_label_delay_hours",48),
                          expected_currency=label_config["series"].get("currency"), label_definition_version=version)
    for row in labels:
        row.update(label_snapshot_id=pmeta["snapshot_id"],label_snapshot_sha256=pmeta["content_sha256"],
                   action_snapshot_id=ameta["snapshot_id"],action_snapshot_sha256=ameta["content_sha256"],
                   price_contract=label_config["series"],query_exclusion_counts=prices["exclusion_counts"],
                   latest_access_failures=prices["latest_access_failures"],
                   action_exclusion_counts=actions["exclusion_counts"],definition_version=version,
                   builder_code_sha256=code_hash(),calendar=calendar.metadata,input_domain=domain,
                   assumption_policy_id=config.get("assumption_rule",{}).get("id") if config["label_policy"]=="research_assumed" else None)
    return labels


def invalid_sample(spec, config, calendar, reason):
    """Preserve a bad per-sample contract without querying or inventing time values."""
    sid=sha256(canonical({"invalid_sample_spec":spec,"series":config["series"],"input_domain":config.get("input_domain","market")}))
    feature={"sample_id":sid,"security_record_ids":[],"identifier_verification":[],"features":{},"used_version_ids":[],
        "decision_at":spec.get("decision_at"),"feature_cutoff":spec.get("feature_cutoff"),"feature_ready_at":spec.get("feature_ready_at"),
        "feature_ready_basis":"invalid_uninterpreted_input","intended_entry_at":None,"feature_snapshot_id":None,"feature_snapshot_sha256":None,
        "query_policy":config["feature_policy"],"assumption_policy_id":None,"sample_status":"invalid","sample_reasons":[reason],
        "research_calculable":False,"historical_pit_eligible":False,"execution_status":"invalid","calendar":calendar.metadata,
        "definition_version":FEATURE_VERSION,"input_domain":config.get("input_domain","market"),"price_contract":config["series"],
        "builder_code_sha256":code_hash(),"strictness":"INVALID_NO_QUERY_PERFORMED","query_exclusion_counts":{},"status":"invalid"}
    labels=[{"sample_id":sid,"horizon_sessions":h,"intended_entry_at":None,"intended_exit_at":None,"status":"invalid","reason":reason,
        "label_available_at":None,"price_return":None,"up_5":None,"down_5pct_5":None,"cash_total_return":None,
        "cash_total_return_status":"invalid","cash_total_return_reason":reason,"label_snapshot_id":None,"action_snapshot_id":None,
        "definition_version":label_version(config.get("label_definition_version", LABEL_VERSION)),"query_policy":config["label_policy"],"input_domain":config.get("input_domain","market"),
        "calendar":calendar.metadata,"builder_code_sha256":code_hash(),"price_contract":config.get("label_series",config["series"])} for h in (1,5,20)]
    return feature,labels


def build_dataset(db, store, config, *, batch_size=1):
    version = label_version(config.get("label_definition_version", LABEL_VERSION))
    if not isinstance(batch_size,int) or isinstance(batch_size,bool) or not 1<=batch_size<=256: raise ValueError("invalid_batch_size")
    request_contract(config,"feature_policy"); request_contract(config,"label_policy")
    calendar=SessionCalendar(db,config["calendar_version"],input_domain=config.get("input_domain","market"))
    simulation=timestamp(config["simulation_asof"])
    items=[]; feature_counts=Counter(); label_counts=Counter(); reasons=Counter(); ready_pairs=0; learning_pairs=0
    # At most one 61-session feature window and one 21-session outcome window per sample.
    for spec in config["samples"]:
        try:
            if calendar.plan(spec["decision_date"])["decision_at"]>simulation:
                raise ValueError("decision_after_simulation_asof")
            feature=generate_feature(db,calendar,spec,config)
        except ValueError as exc:
            known=("decision_after_simulation_asof","decision_date_not_a_session","date_required","timestamp_required",
                   "feature_cutoff_or_decision_contract_invalid","feature_ready_before_input_availability","explicit_feature_ready_basis_required")
            if str(exc) not in known: raise
            feature,labels=invalid_sample(spec,config,calendar,str(exc))
        else:
            labels=None
        feature_id=store.add_feature(feature)
        if labels is None: labels=generate_labels(db,calendar,spec,config)
        rights=load_assertion(config.get("learning_rights"),kind="learning_rights",domain=config.get("input_domain","market"))
        rights_policy = "observed" if config.get("scope_contract", {}).get("scope") == "fixed_security_research" else config["label_policy"]
        rights_at,rights_reason=assertion_availability(rights,policy=rights_policy,cutoff=timestamp(config["label_asof"]),rule=config.get("assumption_rule"))
        learning_allowed=bool(rights and not rights_reason and rights.get("model_training_allowed") is True and feature["input_domain"]=="market")
        for label in labels:
            label["sample_id"]=feature["sample_id"]
            label["learning_ready"]=bool(learning_allowed and feature["historical_pit_eligible"] and feature["sample_status"]=="ready" and
                                        label["status"]=="ready" and label["query_policy"]=="historical_verified" and
                                        label["price_contract"]["price_semantics"]=="as_traded" and feature["execution_status"]=="ready")
            label["learning_rights_status"]="verified_assertion" if rights else "unconfirmed"
            label["learning_rights_available_at"]=rights_at
            label["learning_rights_reason"]=rights_reason
            if rights: label.setdefault("evidence_dependencies",[]).append(rights["evidence"])
            if config.get("scope_contract"):
                from ..observed.scope import learning_reasons
                label["scope_contract"] = config["scope_contract"]
                label["scoped_learning_reasons"] = learning_reasons(feature, label, config["scope_contract"]["scope"], config["label_asof"])
                label["scoped_learning_ready"] = not label["scoped_learning_reasons"]
            label_id=store.add_label(label)
            ready_pairs+=int(feature["sample_status"]=="ready" and label["status"]=="ready")
            learning_pairs+=int(label["learning_ready"])
            items.append({"sample_id":feature["sample_id"],"feature_version_id":feature_id,"label_version_id":label_id,"horizon_sessions":label["horizon_sessions"]})
            label_counts[label["status"]]+=1
            if label["reason"]: reasons[label["reason"]]+=1
        feature_counts[feature["sample_status"]]+=1
        reasons.update(feature["sample_reasons"])
    metadata={"dataset_definition_version":DATASET_VERSION,"config":config,"code_sha256":code_hash(),"batch_size":batch_size,
              "feature_definition_version":FEATURE_VERSION,"label_definition_version":version,"calendar":calendar.metadata,
              "feature_status_counts":dict(feature_counts),"label_status_counts":dict(label_counts),"reason_counts":dict(reasons),
              "ready_pair_count":ready_pairs,"learning_ready_pair_count":learning_pairs,
              "input_scope":"explicit collector-test samples; NOT a historical investment universe",
              "learning_rights_status":"unconfirmed" if not config.get("learning_rights") else "external_assertion_requires_upstream_review",
              "strictness":"NOT_STRICT_PIT" if "research_assumed" in (config["feature_policy"],config["label_policy"]) else "policy_bound_inputs_not_automatic_learning_readiness"}
    return store.freeze(items,metadata)
