"""Explicit time policies, conservative conflict handling, evidence-rich results."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
from zoneinfo import ZoneInfo
from ..storage import canonical
from .database import SCHEMA_VERSION, engine_hash
from .time import POLICY_VERSION, assumed_available, timestamp, day

POLICIES = ("observed", "historical_verified", "research_assumed")
MEANINGS = ("as_traded", "split_adjusted", "total_return_adjusted", "unknown")


def provenance(db, row):
    artifact = dict(db.conn.execute("SELECT * FROM normalized_artifacts WHERE artifact_id=?", (row["artifact_id"],)).fetchone())
    links = [dict(r) for r in db.conn.execute("""SELECT l.*,r.source,r.request_id,r.received_at,r.network_performed,
                r.receipt_evidence,r.status,r.raw_sha256,r.raw_path,r.input_domain
                FROM artifact_receipts l JOIN raw_receipts r USING(receipt_id) WHERE l.artifact_id=?
                ORDER BY r.received_at,l.usable_at,l.receipt_id,l.manifest_sha256""", (row["artifact_id"],))]
    return artifact, links


def eligible(row, links, cutoff, policy, rule):
    temporal = json.loads(row["temporal_json"])
    evidence = json.loads(row["evidence_json"])
    if policy == "observed":
        actual = [l for l in links if l["network_performed"] == 1 and l["received_at"] and l["status"] == "success"]
        if not actual: return None, "actual_receipt_unproven"
        received = [l for l in actual if l["received_at"] <= cutoff]
        if not received: return None, "received_after_cutoff"
        usable = [l for l in received if l["usable_at"] <= cutoff]
        if not usable: return None, "processing_after_cutoff"
        # Order differing contents by actual receipts, never by DB ingest time.
        chosen = max(usable, key=lambda l: (l["received_at"], l["usable_at"], l["receipt_id"]))
        pub=temporal["published_at"]; rev=temporal["revised_at"]
        bound=bool(evidence and evidence.get("version_raw_sha256")==chosen["raw_sha256"])
        return {"order_at": chosen["received_at"], "available_at": max(chosen["received_at"], chosen["usable_at"]),
                "source_version_at": (max(pub,rev) if rev else pub) if bound and pub else None,
                "availability_evidence": chosen["usable_evidence"], "receipt": chosen, "assumption": None}, None
    pub = temporal["published_at"]
    revision = temporal["revised_at"]
    bound = bool(evidence and evidence.get("version_raw_sha256") == links[0]["raw_sha256"])
    if policy == "historical_verified":
        if not pub:
            return None, "publication_date_only" if temporal["publication_date"] else "publication_time_unknown"
        if not bound: return None, "publication_version_unproven"
        available = max(pub, revision) if revision else pub
        if available > cutoff: return None, "publication_after_cutoff"
        return {"order_at": available, "available_at": available, "availability_evidence": "version_bound_publication",
                "receipt": links[0], "assumption": None}, None
    # No observed -> assumed or verified -> assumed automatic transition exists.
    assumption = {"rule": rule, "version_content_verified": bound,
                  "limitation": "NOT_STRICT_PIT: a date/time assumption cannot reconstruct a missing historical content version"}
    if pub:
        available = max(pub, revision) if revision else pub
    elif temporal["publication_date"]:
        if temporal.get("publication_timezone") is None:
            return None, "assumption_source_timezone_unknown"
        if temporal.get("publication_timezone") != rule["timezone"]:
            return None, "assumption_timezone_mismatch"
        available = assumed_available(temporal["publication_date"], rule)
        if revision: available = max(available, revision)
    elif rule.get("allow_observation_date_for_unknown_publication") is True:
        # Explicit current-vintage research only. Keep source publication fields null.
        if row.get("observation_end") and row.get("observation_end_evidence"):
            delay = rule.get("bar_close_delay_seconds")
            if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 0 <= delay <= 86400:
                raise ValueError("explicit_research_bar_delay_required")
            available = timestamp((datetime.fromisoformat(row["observation_end"].replace("Z", "+00:00")) + timedelta(seconds=delay)).isoformat())
            assumption["basis"] = "reference_session_close_plus_declared_delay_not_historical_publication"
        elif row.get("event_date"):
            available = assumed_available(row["event_date"], rule)
            assumption["basis"] = "event_date_not_historical_publication"
        else:
            return None, "publication_time_unknown_no_assumption_input"
        if revision:
            available = max(available, revision)
    else:
        return None, "publication_time_unknown_no_assumption_input"
    if available > cutoff: return None, "assumed_publication_after_cutoff"
    return {"order_at": available, "available_at": available, "availability_evidence": "research_rule",
            "receipt": links[0], "assumption": assumption}, None


def calendar_bound(db, row, cutoff, policy, calendar_version, rule, domain):
    if row["trading_date"] > datetime.fromisoformat(cutoff.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).date().isoformat():
        return None, "future_trading_date"
    if row["observation_end"]:
        if not row["observation_end_evidence"]: return None, "observation_end_unproven"
        if row["observation_end"] > cutoff: return None, "observation_period_not_finished"
        return {"observation_end": row["observation_end"], "bound_evidence": row["observation_end_evidence"],
                "calendar_version": None}, None
    sql = "SELECT * FROM trading_session_versions WHERE exchange='XNYS' AND trading_date=? AND input_domain=?"
    params = [row["trading_date"], domain]
    if calendar_version:
        sql += " AND calendar_version=?"; params.append(calendar_version)
    sessions = [dict(s) for s in db.conn.execute(sql, params)]
    if not sessions: return None, "calendar_date_unavailable"
    if policy == "historical_verified":
        sessions = [s for s in sessions if s["publication_at"] and s["publication_at"] <= cutoff and json.loads(s["historical_evidence_json"])]
        if not sessions: return None, "calendar_not_historically_verified"
    elif policy == "observed" or not rule.get("allow_reference_calendar", False):
        sessions = [s for s in sessions if s["known_at"] <= cutoff]
        if not sessions: return None, "calendar_not_known_at_cutoff"
    if len({s["calendar_version"] for s in sessions}) != 1:
        return None, "calendar_version_required"
    session = sessions[0]
    if session["close_utc"] > cutoff: return None, "observation_period_not_finished"
    return {"observation_end": session["close_utc"], "bound_evidence": "reference_regular_session_bound_vendor_scope_unverified",
            "calendar_version": session["calendar_version"], "session_version_id": session["session_version_id"],
            "calendar_artifact_sha256": session["artifact_sha256"], "calendar_source": session["calendar_source"],
            "calendar_historical_notice_verified": bool(json.loads(session["historical_evidence_json"]))}, None


def symbol_resolver(db, symbols, cutoff, policy, rule, domain):
    candidates = defaultdict(list)
    params = [domain]
    sql = """SELECT s.* FROM symbol_assertions s JOIN normalized_artifacts a USING(artifact_id) WHERE a.input_domain=?"""
    if symbols:
        sql += " AND s.symbol IN (" + ",".join("?" for _ in symbols) + ")"; params.extend(symbols)
    for r in db.conn.execute(sql, params):
        r = dict(r)
        _, links = provenance(db, r)
        chosen, reason = eligible(r, links, cutoff, policy, rule)
        if not reason:
            candidates[r["symbol"]].append((r, chosen))

    def resolve(row):
        date = row.get("trading_date", row.get("event_date"))
        matches = []
        for symbol, entries in candidates.items():
            effective = [(r, c) for r, c in entries if r["effective_from"] and r["effective_from"] <= date and
                         (r["effective_to"] is None or date < r["effective_to"])]
            # A future withdrawal cannot remove an earlier eligible mapping. A known withdrawal can.
            groups = defaultdict(list)
            for r, c in effective:
                groups[(r["security_record_id"], r["exchange"], r["effective_from"], r["effective_to"])].append((r, c))
            active = []
            for group in groups.values():
                latest = max(c["order_at"] for _, c in group)
                heads = [(r, c) for r, c in group if c["order_at"] == latest]
                if len({r["record_state"] for r, _ in heads}) > 1:
                    return None, "symbol_mapping_conflict"
                active.extend(r for r, _ in heads if r["record_state"] == "active")
            if len({(r["security_record_id"], r["exchange"]) for r in active}) > 1:
                if any(r["security_record_id"] == row["security_record_id"] for r in active):
                    return None, "symbol_mapping_conflict"
            for r in active:
                if r["security_record_id"] == row["security_record_id"]:
                    matches.append({"symbol": symbol, "assertion_id": r["assertion_id"], "exchange": r["exchange"]})
        return (sorted(matches, key=lambda m: (m["symbol"], m["assertion_id"])), None) if matches else (None, "no_eligible_effective_symbol_mapping")
    return resolve


def query(db, *, cutoff, policy, symbols=None, sources=None, start=None, end=None,
          identifier_requirement="verified", price_semantics=("as_traded",), family="prices",
          symbol_mode="effective_mapping", calendar_version=None, assumption_rule=None,
          input_domain="market", session_requirement=None, stale_after_hours=48):
    cutoff = timestamp(cutoff)
    if cutoff is None: raise ValueError("cutoff_required")
    if start: day(start)
    if end: day(end)
    if start and end and start>end: raise ValueError("invalid_date_range")
    if policy not in POLICIES: raise ValueError("explicit_valid_policy_required")
    if identifier_requirement not in ("verified", "temporary_allowed"): raise ValueError("invalid_identifier_requirement")
    if symbol_mode not in ("effective_mapping", "provider_label"): raise ValueError("invalid_symbol_mode")
    if family not in ("prices", "actions"): raise ValueError("invalid_query_family")
    if input_domain not in ("market", "synthetic"): raise ValueError("invalid_input_domain")
    if not price_semantics or any(m not in MEANINGS for m in price_semantics): raise ValueError("invalid_price_semantics")
    if policy == "research_assumed":
        if not assumption_rule: raise ValueError("explicit_assumption_rule_required")
        assumed_available("2000-01-01", assumption_rule)  # validate rule even when no rows exist
    elif assumption_rule is not None:
        raise ValueError("assumption_rule_for_assumed_policy_only")
    if stale_after_hours < 0: raise ValueError("invalid_staleness_threshold")
    symbols, sources = sorted(set(symbols or [])), sorted(set(sources or []))
    request = {"cutoff": cutoff, "policy": policy, "policy_version": POLICY_VERSION, "symbols": symbols,
               "sources": sources, "start": start, "end": end, "identifier_requirement": identifier_requirement,
               "price_semantics": sorted(set(price_semantics)), "family": family, "symbol_mode": symbol_mode,
               "calendar_version": calendar_version, "assumption_rule": assumption_rule, "input_domain": input_domain,
               "session_requirement": session_requirement, "stale_after_hours": stale_after_hours}
    table = "daily_price_versions" if family == "prices" else "corporate_action_versions"
    date_key = "trading_date" if family == "prices" else "event_date"
    sql = f"""SELECT v.*,s.identifier_verification,s.external_security_id,s.issuer_id,s.share_class
              FROM {table} v JOIN normalized_artifacts a USING(artifact_id)
              JOIN security_records s USING(security_record_id) WHERE a.input_domain=?"""
    params = [input_domain]
    if sources:
        sql += " AND v.source IN (" + ",".join("?" for _ in sources) + ")"; params.extend(sources)
    if symbol_mode == "provider_label" and symbols:
        sql += " AND v.requested_symbol IN (" + ",".join("?" for _ in symbols) + ")"; params.extend(symbols)
    if start: sql += f" AND v.{date_key}>=?"; params.append(start)
    if end: sql += f" AND v.{date_key}<=?"; params.append(end)
    sql += " ORDER BY v.version_id"
    exclusions, excluded_ids, conflicts, groups = Counter(), [], [], defaultdict(list)
    resolver = symbol_resolver(db, symbols, cutoff, policy, assumption_rule, input_domain) if symbol_mode == "effective_mapping" else None
    artifact_cache = {}
    for raw in db.conn.execute(sql, params):
        row = dict(raw)
        reason = None
        if identifier_requirement == "verified" and row["identifier_verification"] != "verified": reason = "security_identity_unverified"
        if family == "prices" and row["price_semantics"] not in price_semantics: reason = reason or "price_semantics_not_requested"
        if session_requirement and family == "prices" and row["session_scope"] != session_requirement: reason = reason or "session_scope_unverified_or_mismatch"
        if row["artifact_id"] not in artifact_cache: artifact_cache[row["artifact_id"]] = provenance(db, row)
        artifact, links = artifact_cache[row["artifact_id"]]
        chosen, time_reason = eligible(row, links, cutoff, policy, assumption_rule)
        reason = reason or time_reason
        bound, mappings = None, None
        if not reason and family == "prices":
            bound, reason = calendar_bound(db, row, cutoff, policy, calendar_version, assumption_rule or {}, input_domain)
        if not reason and resolver:
            mappings, reason = resolver(row)
        if reason:
            exclusions[reason] += 1; excluded_ids.append({"version_id": row["version_id"], "reason": reason})
            if reason in ("symbol_mapping_conflict", "calendar_version_required"):
                conflicts.append({"kind": reason, "version_ids": [row["version_id"]],
                                  "date": row[date_key], "symbols": symbols})
            continue
        row["provenance"] = {"artifact_id": artifact["artifact_id"], "normalized_sha256": artifact["normalized_sha256"],
                             "schema_version": artifact["schema_version"], "normalizer_version": artifact["normalizer_version"],
                             "normalizer_code_sha256": artifact["normalizer_code_sha256"],
                             "normalization_hash_verification": artifact["hash_verification"],
                             "raw_sha256": chosen["receipt"]["raw_sha256"], "receipt_id": chosen["receipt"]["receipt_id"],
                             "manifest_sha256": chosen["receipt"]["manifest_sha256"], "received_at": chosen["receipt"]["received_at"],
                             "parsing_completed_at": chosen["receipt"]["parsing_completed_at"],
                             "validation_completed_at": chosen["receipt"]["validation_completed_at"],
                             "usable_at": chosen["receipt"]["usable_at"], "publication_evidence": json.loads(row["evidence_json"])}
        row["availability"] = {k: v for k, v in chosen.items() if k != "receipt"}
        row["observation_bound"] = bound
        row["symbol_mapping"] = mappings
        row["temporal"] = json.loads(row.pop("temporal_json")); row.pop("evidence_json")
        row["validation"] = {"identifier": row["identifier_verification"], "time_policy": policy,
                             "price_semantics": row.get("price_semantics"), "session_scope": row.get("session_scope"),
                             "historical_content_verified": bool(row["provenance"]["publication_evidence"])}
        key = (row["source"], row["security_record_id"], row[date_key])
        key += (row["session_scope"], row["price_semantics"], row["price_kind"]) if family == "prices" else (row["event_key"],)
        groups[key].append(row)
    selected = []
    for key, versions in sorted(groups.items()):
        # When all candidates have bound source-version evidence, a late initial archive
        # cannot undo a known correction merely because it was downloaded later.
        source_order=policy=="observed" and all(r["availability"].get("source_version_at") for r in versions)
        order_field="source_version_at" if source_order else "order_at"
        latest = max(r["availability"][order_field] for r in versions)
        heads = [r for r in versions if r["availability"][order_field] == latest]
        if len({r["value_sha256"] for r in heads}) > 1:
            conflicts.append({"kind": "unordered_same_source_versions", "key": list(key),
                              "version_ids": sorted(r["version_id"] for r in heads), "order_at": latest})
            exclusions["unresolved_version_conflict"] += len(heads)
            excluded_ids.extend({"version_id": r["version_id"], "reason": "unresolved_version_conflict"} for r in heads)
            continue
        # Deterministic representative ONLY for equal values. All equal-version evidence remains visible.
        if policy=="observed":
            latest_receipt=max(r["provenance"]["received_at"] for r in heads)
            representative=[r for r in heads if r["provenance"]["received_at"]==latest_receipt]
        else: representative=heads
        head = min(representative, key=lambda r: r["version_id"])
        head["version_ordering_evidence"]="bound_source_publication" if source_order else "policy_available_time"
        head["equivalent_version_ids"] = sorted(r["version_id"] for r in heads)
        head["superseded_eligible_version_ids"] = sorted(r["version_id"] for r in versions if r not in heads)
        eligible_ids={r["version_id"] for r in versions}
        if head["previous_version_id"] not in eligible_ids:
            head["previous_version_id"]=None; head["previous_relation"]=None
        later_key,earlier_key=("later_price_id","earlier_price_id") if family=="prices" else ("later_action_id","earlier_action_id")
        head["version_lineage"]=[{"edge_id":e["edge_id"],"earlier_version_id":e[earlier_key],"relation":e["relation"]}
                                 for e in db.conn.execute(f"SELECT * FROM version_edges WHERE {later_key}=? ORDER BY edge_id",(head["version_id"],))
                                 if e[earlier_key] in eligible_ids]
        if head["record_state"] == "withdrawn":
            exclusions["known_withdrawal"] += 1
            excluded_ids.append({"version_id": head["version_id"], "reason": "known_withdrawal"})
            continue
        received = head["provenance"]["received_at"]
        age = (datetime.fromisoformat(cutoff.replace("Z", "+00:00")) - datetime.fromisoformat(received.replace("Z", "+00:00"))).total_seconds()/3600 if received else None
        head["receipt_after_cutoff"] = received is not None and received > cutoff
        head["receipt_age_hours_at_cutoff"] = age if age is not None and age >= 0 else None
        head["stale_receipt"] = age is not None and age > stale_after_hours
        selected.append(head)
    # Cross-provider disagreement is reported; each provider remains a separate series.
    comparison = defaultdict(list)
    for row in selected:
        key = (row["external_security_id"] or row["requested_symbol"], row[date_key])
        key += (row["price_semantics"], row["price_kind"], row["session_scope"]) if family == "prices" else (row["event_key"],)
        comparison[key].append(row)
    for key, rows in sorted(comparison.items()):
        if len({r["source"] for r in rows}) < 2: continue
        fields = ("open", "high", "low", "close", "volume") if family == "prices" else ("amount", "numerator", "denominator")
        if len({canonical({f: r[f] for f in fields}) for r in rows}) > 1:
            conflicts.append({"kind": "cross_provider_disagreement", "key": list(key),
                              "version_ids": sorted(r["version_id"] for r in rows),
                              "identity_alignment": "verified_external_id" if all(r["external_security_id"] for r in rows) else "provider_symbol_only_unverified"})
    failures = []
    attempts = defaultdict(list)
    for raw in db.conn.execute("SELECT * FROM raw_receipts WHERE input_domain=? AND received_at<=? ORDER BY received_at,receipt_id", (input_domain, cutoff)):
        r = dict(raw); spec = json.loads(r["request_json"])
        if sources and r["source"] not in sources: continue
        if symbols and spec.get("symbol") not in symbols: continue
        attempts[r["request_id"]].append(r)
    for rid, receipts in sorted(attempts.items()):
        latest_time = max(r["received_at"] for r in receipts)
        for r in receipts:
            if r["received_at"] == latest_time and r["status"] != "success":
                failures.append({"request_id": rid, "receipt_id": r["receipt_id"], "source": r["source"],
                                 "status": r["status"], "received_at": r["received_at"],
                                 "previous_success_retained": any(x["status"] == "success" for x in receipts)})
    selected.sort(key=lambda r: (r["source"], r["security_record_id"], r[date_key], r.get("price_semantics", ""), r["version_id"]))
    limits = ["No source licensing, historical universe completeness, profitability or identity guarantees are implied."]
    if identifier_requirement == "temporary_allowed": limits.append("Temporary internal records are not verified permanent security IDs; cross-source linkage is unverified.")
    if symbol_mode == "provider_label": limits.append("Provider request symbols are labels, not historically verified effective ticker mappings.")
    if any(r["observation_bound"] and r["observation_bound"].get("calendar_source") for r in selected):
        limits.append("Library reference sessions bound regular-session observations; vendor session coverage and historical notice versions remain unverified.")
    if policy == "observed": limits.append("Bound source-version order is used when complete; otherwise receipt order describes observed responses, not the provider's historical revision chronology.")
    if policy == "research_assumed": limits.append("NOT_STRICT_PIT: assumed availability does not prove a historical content version or actual historical receipt.")
    if input_domain == "synthetic": limits.append("SYNTHETIC_TEST_ONLY: these records and evidence are fictional, not market data.")
    return {"status": "CONFLICT" if conflicts else "OK" if selected else "EMPTY", "request": request,
            "strictness": "NOT_STRICT_PIT" if policy == "research_assumed" else "OBSERVED_RECEIPTS" if policy == "observed" else "HISTORICAL_VERSION_EVIDENCE",
            "schema_version": SCHEMA_VERSION, "engine_code_sha256": engine_hash(),
            "selection_rule": "eligible_version_time_v2; complete bound source order else policy time; equal-values-only representative; no source priority",
            "data": selected, "selected_count": len(selected), "excluded_count": sum(exclusions.values()),
            "exclusion_counts": dict(sorted(exclusions.items())), "excluded_versions": sorted(excluded_ids, key=lambda r: (r["reason"], r["version_id"])),
            "conflicts": conflicts, "conflict_count": len(conflicts), "latest_access_failures": failures,
            "calendar_versions": sorted({r["observation_bound"]["calendar_version"] for r in selected if r["observation_bound"] and r["observation_bound"]["calendar_version"]}),
            "assumptions": assumption_rule, "limitations": limits,
            "sort_rule": "source,security_record_id,event_or_trading_date,price_semantics,version_id"}
