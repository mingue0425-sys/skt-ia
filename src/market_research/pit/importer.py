"""Import existing manifests, preserving receipts and quarantining broken evidence."""
from collections import Counter
import json
import math
from pathlib import Path
import re
import sqlite3
import uuid
from importlib.metadata import version
from ..collect import public_spec
from ..http import utc_now
from ..storage import canonical, sha256
from ..validation.quality import validate_bars
from .database import IMPORTER_VERSION, engine_hash
from .time import day, timestamp

SEMANTICS = {"raw_as_traded": "as_traded", "as_traded": "as_traded",
             "vendor_split_adjusted_ohlc_not_raw": "split_adjusted", "split_adjusted": "split_adjusted",
             "total_return_adjusted": "total_return_adjusted", "unknown": "unknown"}
BAD_BAR = {"missing_column", "wrong_type", "invalid_date", "negative_volume", "inconsistent_ohlc",
           "duplicate_key", "conflicting_duplicate", "negative_price", "nonfinite_price"}


class IngestError(ValueError):
    pass


def decode(body):
    def reject(_):
        raise IngestError("nonfinite_json")
    try:
        return json.loads(body, parse_constant=reject)
    except (ValueError, UnicodeError):
        raise IngestError("invalid_json") from None


def file_at(root, relative):
    if not isinstance(relative, str):
        raise IngestError("missing_file_reference")
    p = (root / relative).resolve()
    if not p.is_relative_to(root):
        raise IngestError("path_outside_artifact_root")
    if not p.is_file():
        raise IngestError("missing_file")
    return p


def identity(db, row, domain):
    local = row.get("local_instrument_id") or "temporary:" + row["source"] + ":" + row["requested_symbol"]
    claim = row.get("security_identity", {})
    level = claim.get("verification", "temporary")
    if level == "verified" and (not claim.get("external_security_id") or not claim.get("evidence")):
        raise IngestError("unproven_security_identity")
    if level == "verified" and local.startswith("temporary:") and claim.get("external_security_id", "").startswith("temporary:"):
        raise IngestError("temporary_id_not_permanent")
    if level == "verified" and re.match(r"(?i)^cik[:\s]", claim.get("external_security_id", "")):
        raise IngestError("issuer_cik_not_security_id")
    vals = {"source": row["source"], "local_label": local,
            "external_security_id": claim.get("external_security_id"), "issuer_id": claim.get("issuer_id"),
            "share_class": claim.get("share_class"), "identifier_verification": level,
            "identity_evidence": json.dumps(claim.get("evidence")) if claim.get("evidence") else None,
            "input_domain": domain}
    sid = sha256(canonical(vals))
    db.insert("security_records", {"security_record_id": sid, **vals}, ignore=True)
    return sid


def temporal(row):
    pub = row.get("publication_time_utc")
    pub_date = row.get("publication_date")
    precision = row.get("publication_precision") or ("timestamp" if pub else "date" if pub_date else "unknown")
    if precision not in ("timestamp", "date", "unknown") or (pub and precision != "timestamp") or (pub_date and pub):
        raise IngestError("publication_precision_mismatch")
    return {"published_at": timestamp(pub), "published_original": pub,
            "publication_date": day(pub_date) if pub_date else None, "publication_precision": precision,
            "publication_timezone": row.get("publication_timezone"),
            "revised_at": timestamp(row.get("revision_time_utc")), "revision_original": row.get("revision_time_utc"),
            "effective_from": row.get("effective_from"), "effective_to": row.get("effective_to"),
            "source_timestamp_utc": row.get("source_timestamp_utc")}


def publication_evidence(row, root, raw_hash, domain):
    claim = row.get("publication_evidence")
    if not claim:
        return {}
    path = file_at(root, claim.get("path"))
    body = path.read_bytes()
    if sha256(body) != claim.get("sha256"):
        raise IngestError("publication_evidence_hash_mismatch")
    proof = decode(body)
    if proof.get("kind") not in ("synthetic_version_record", "vendor_version_archive", "verified_timestamped_version"):
        raise IngestError("unsupported_publication_evidence")
    if proof.get("kind") == "synthetic_version_record" and domain != "synthetic":
        raise IngestError("synthetic_evidence_in_market_input")
    if proof.get("version_raw_sha256") != raw_hash:
        raise IngestError("publication_evidence_wrong_version")
    t = temporal(row)
    if timestamp(proof.get("published_at")) != t["published_at"] or proof.get("publication_date") != t["publication_date"]:
        raise IngestError("publication_evidence_time_mismatch")
    if timestamp(proof.get("revised_at")) != t["revised_at"]:
        raise IngestError("revision_evidence_time_mismatch")
    if not proof.get("verification_method") or proof.get("verified") is not True:
        raise IngestError("unverified_publication_evidence")
    return {**proof, "path": str(path), "sha256": sha256(body),
            "validation_scope": "hash and version/time binding checked; authenticity is an upstream evidence obligation"}


def receipt_id(m, manifest_hash):
    return m.get("receipt_id") or sha256(canonical(["legacy-receipt", manifest_hash]))


def matching_origins(records):
    origins = {}
    for path, body_hash, m in records:
        if m.get("network_download_performed") is False:
            continue
        key = (m.get("request_id"), m.get("sha256"), m.get("fetched_at_utc"))
        origins.setdefault(key,[]).append((receipt_id(m,body_hash),m))
    return origins


def checked_manifest(m):
    if not isinstance(m, dict) or m.get("schema_version") != "1.0.0":
        raise IngestError("unsupported_manifest_schema")
    for key in ("source", "request_id", "status", "fetched_at_utc", "request_parameters"):
        if key not in m:
            raise IngestError("missing_manifest_column")
    if public_spec(m["request_parameters"]) != m["request_parameters"]:
        raise IngestError("unsafe_manifest_parameters")
    if m["source"] != m["request_parameters"].get("source"):
        raise IngestError("manifest_source_mismatch")
    if re.search(r"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+", canonical(m).decode()):
        raise IngestError("unsafe_manifest_credentials")
    timestamp(m["fetched_at_utc"])
    if m.get("network_download_performed") is not None and not isinstance(m["network_download_performed"],bool):
        raise IngestError("invalid_network_flag")


def raw_values(m, rid, root, domain, now):
    if m.get("raw_path"):
        p = file_at(root, m["raw_path"])
        body = p.read_bytes()
        if sha256(body) != m.get("sha256") or len(body) != m.get("file_size_bytes"):
            raise IngestError("raw_hash_or_size_mismatch")
        raw_path, raw_hash, size = str(p), sha256(body), len(body)
    else:
        if m.get("sha256") or m.get("status") == "success":
            raise IngestError("missing_raw_file")
        raw_path, raw_hash, size = None, None, None
    flag = m.get("network_download_performed")
    return {"receipt_id": rid, "source": m["source"], "request_id": m["request_id"],
            "request_json": canonical(m["request_parameters"]).decode(),
            "received_at": timestamp(m["fetched_at_utc"]), "received_original": m["fetched_at_utc"],
            "network_performed": None if flag is None else int(flag),
            "receipt_evidence": "explicit_network_flag" if flag is True else "legacy_network_unknown",
            "http_status": m.get("http_status"), "status": m["status"], "raw_path": raw_path,
            "raw_sha256": raw_hash, "file_size": size, "input_domain": domain, "ingested_at": timestamp(now)}


def normalize_checked(m, root):
    p = file_at(root, m.get("normalized_path"))
    body = p.read_bytes()
    digest = sha256(body)
    data = decode(body)
    if m.get("normalized_sha256"):
        if digest != m["normalized_sha256"]:
            raise IngestError("normalized_hash_mismatch")
        verification = "manifest_byte_hash"
    else:
        if sha256(canonical(data)) != p.stem:
            raise IngestError("legacy_normalized_content_hash_mismatch")
        verification = "legacy_filename_canonical_hash_no_manifest_byte_hash"
    if not isinstance(data, dict) or any(not isinstance(data.get(k), list) for k in ("bars", "actions", "directory", "documents")):
        raise IngestError("normalized_schema_mismatch")
    prov = data.get("provenance", {})
    for key, value in (("raw_sha256", m["sha256"]), ("request_id", m["request_id"]),
                       ("code_sha256", m.get("code_sha256")), ("schema_version", "1.0.0")):
        if prov.get(key) != value:
            raise IngestError("normalized_provenance_mismatch")
    issues = validate_bars(data["bars"])
    if any(i["code"] in BAD_BAR for i in issues):
        raise IngestError("normalized_price_validation_failure")
    for row in data["bars"] + data["actions"]:
        if row.get("raw_sha256") != m["sha256"] or row.get("source") != m["source"]:
            raise IngestError("row_provenance_mismatch")
        if row.get("fetched_at_utc") != m["fetched_at_utc"]:
            raise IngestError("row_receipt_time_mismatch")
    return p, digest, data, verification


def previous(db, table, key, temporal_value, value_hash):
    clauses = " AND ".join(k + "=?" for k in key)
    rows = db.conn.execute(f"SELECT * FROM {table} WHERE {clauses}", list(key.values())).fetchall()
    pub = temporal_value["revised_at"] or temporal_value["published_at"]
    ordered = []
    if pub:
        for r in rows:
            t = json.loads(r["temporal_json"])
            old = t["revised_at"] or t["published_at"]
            if old and old < pub and r["value_sha256"] != value_hash and json.loads(r["evidence_json"]):
                ordered.append((old, r["version_id"]))
    if ordered:
        latest_time = max(t for t, _ in ordered)
        ids = {v for t, v in ordered if t == latest_time}
        if len(ids) == 1:
            return ids.pop(), "version_bound_publication_order"
    return None, None


def link_versions(db, table, vid, key, current_time, evidence):
    """Append lineage even if the initial version arrives AFTER its correction."""
    available = current_time["revised_at"] or current_time["published_at"]
    if not available or not evidence: return
    clauses = " AND ".join(k+"=?" for k in key)
    for old in db.conn.execute(f"SELECT * FROM {table} WHERE {clauses} AND version_id!=?",list(key.values())+[vid]):
        old_evidence = json.loads(old["evidence_json"])
        t = json.loads(old["temporal_json"]); old_time = t["revised_at"] or t["published_at"]
        if not old_evidence or not old_time or old_time==available: continue
        later, earlier = (vid,old["version_id"]) if available>old_time else (old["version_id"],vid)
        price = table=="daily_price_versions"
        proof = {"publication_evidence_sha256": sorted([evidence["sha256"],old_evidence["sha256"]]),
                 "available_times": sorted([available,old_time])}
        db.insert("version_edges", {"edge_id": sha256(canonical([later,earlier,proof])),
                  "later_price_id": later if price else None, "earlier_price_id": earlier if price else None,
                  "later_action_id": None if price else later, "earlier_action_id": None if price else earlier,
                  "relation": "version_bound_publication_order", "evidence_json": canonical(proof).decode(),
                  "recorded_at": timestamp(utc_now())},ignore=True)


def add_prices(db, data, artifact, root, m, domain):
    for b in data["bars"]:
        sid = identity(db, b, domain)
        t = temporal(b)
        evidence = publication_evidence(b, root, m["sha256"], domain)
        semantics = SEMANTICS.get(b.get("price_semantics"), "unknown")
        series = [(semantics, "ohlcv", {k: b.get(k) for k in ("open", "high", "low", "close", "volume")},
                   b.get("price_semantics", "unknown"))]
        if b.get("adjusted_close") is not None:
            # A total-return adjusted close is NOT a total-return OHLCV bar or a realized total return.
            meaning = "total_return_adjusted" if b.get("adjusted_close_semantics") == "split_and_distribution_adjusted_current_vintage" else "unknown"
            series.append((meaning, "close_only", {"open": None, "high": None, "low": None,
                           "close": b["adjusted_close"], "volume": None}, b.get("adjusted_close_semantics", "unknown")))
        for meaning, kind, values, semantics_evidence in series:
            # SQLite REAL affinity returns 100 as 100.0. Canonicalize BEFORE hashing so
            # valid integer JSON prices/ratios are replayable with the same value hash.
            values={k:float(v) if k in ("open","high","low","close") and v is not None else v for k,v in values.items()}
            base = {"artifact_id": artifact, "security_record_id": sid, "source": b["source"],
                    "requested_symbol": b["requested_symbol"], "trading_date": day(b["trading_date"]),
                    "session_scope": b.get("session_scope", "unknown"), "price_semantics": meaning,
                    "price_kind": kind, "currency": b.get("currency"), **values,
                    "volume_unit": b.get("volume_unit", "provider_reported_unverified") if kind == "ohlcv" else "not_applicable",
                    "volume_adjustment_basis": b.get("volume_adjustment_basis", "unknown") if kind == "ohlcv" else "not_applicable",
                    "semantics_evidence": semantics_evidence,
                    "observation_end": timestamp(b.get("observation_end_utc")),
                    "observation_end_evidence": b.get("observation_end_evidence"),
                    "temporal_json": canonical(t).decode(), "evidence_json": canonical(evidence).decode(),
                    "record_state": b.get("record_state", "active")}
            value_hash = sha256(canonical({k: v for k, v in base.items() if k not in ("artifact_id", "temporal_json", "evidence_json")}))
            key = {k: base[k] for k in ("security_record_id", "source", "trading_date", "session_scope", "price_semantics", "price_kind")}
            prior, relation = previous(db, "daily_price_versions", key, t, value_hash) if evidence else (None, None)
            vid = sha256(canonical([artifact, key, value_hash]))
            db.insert("daily_price_versions", {"version_id": vid, **base, "value_sha256": value_hash,
                      "previous_version_id": prior, "previous_relation": relation}, ignore=True)
            link_versions(db,"daily_price_versions",vid,key,t,evidence)


def add_actions(db, data, artifact, root, m, domain):
    seen = set()
    for b in data["actions"]:
        t = temporal(b)
        ev = publication_evidence(b, root, m["sha256"], domain)
        typ = b["event_type"]
        event_date = day(b["event_date"])
        action_key = (b["local_instrument_id"], b.get("event_key") or typ + ":" + event_date)
        if action_key in seen: raise IngestError("duplicate_corporate_action_key")
        seen.add(action_key)
        for name in ("amount", "numerator", "denominator"):
            v = b.get(name)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v)):
                raise IngestError("invalid_corporate_action_type")
        if typ == "splits" and (not b.get("numerator") or not b.get("denominator") or b["numerator"]<=0 or b["denominator"]<=0):
            raise IngestError("invalid_split_ratio")
        vals = {"artifact_id": artifact, "security_record_id": identity(db, b, domain), "source": b["source"],
                "requested_symbol": b["requested_symbol"], "event_type": typ, "event_date": event_date,
                "event_key": b.get("event_key") or typ + ":" + event_date,
                "declaration_date": b.get("declaration_date"), "effective_date": b.get("effective_date"),
                "ex_date": b.get("ex_date", event_date if typ == "dividends" else None),
                "record_date": b.get("record_date"), "payment_date": b.get("payment_date"),
                "amount": b.get("amount"), "currency": b.get("currency"), "numerator": b.get("numerator"),
                "denominator": b.get("denominator"), "amount_semantics": b.get("amount_semantics", "unknown"),
                "temporal_json": canonical(t).decode(), "evidence_json": canonical(ev).decode(),
                "record_state": b.get("record_state", "active")}
        for k in ("declaration_date", "effective_date", "ex_date", "record_date", "payment_date"):
            if vals[k]: day(vals[k])
        for k in ("amount","numerator","denominator"):
            if vals[k] is not None: vals[k]=float(vals[k])
        value_hash = sha256(canonical({k: v for k, v in vals.items() if k not in ("artifact_id", "temporal_json", "evidence_json")}))
        key = {k: vals[k] for k in ("security_record_id", "source", "event_key")}
        prior, relation = previous(db, "corporate_action_versions", key, t, value_hash) if ev else (None, None)
        vid=sha256(canonical([artifact,key,value_hash]))
        db.insert("corporate_action_versions", {"version_id": vid,
                  **vals, "value_sha256": value_hash, "previous_version_id": prior, "previous_relation": relation}, ignore=True)
        link_versions(db,"corporate_action_versions",vid,key,t,ev)


def add_symbols(db, data, artifact, root, m, domain):
    explicit = data.get("symbol_assertions", [])
    # Directory listings are present-time assertions; no invented effective/listing dates.
    inferred = [{"source": m["source"], "requested_symbol": b["ticker"], "symbol": b["ticker"],
                 "exchange": b.get("source_fields", {}).get("Exchange", "unknown"),
                 "local_instrument_id": "temporary:" + m["source"] + ":" + b["ticker"]}
                for b in data["directory"]]
    for b in explicit + inferred:
        vals = {"artifact_id": artifact, "security_record_id": identity(db, b, domain), "source": b["source"],
                "symbol": b["symbol"], "exchange": b.get("exchange", "unknown"),
                "effective_from": day(b["effective_from"]) if b.get("effective_from") else None,
                "effective_to": day(b["effective_to"]) if b.get("effective_to") else None,
                "temporal_json": canonical(temporal(b)).decode(),
                "evidence_json": canonical(publication_evidence(b, root, m["sha256"], domain)).decode(),
                "record_state": b.get("record_state", "active")}
        db.insert("symbol_assertions", {"assertion_id": sha256(canonical(vals)), **vals}, ignore=True)


def import_artifacts(db, root, *, domain="market", now=None, interrupt_after=None):
    root = Path(root).resolve()
    if domain not in ("market", "synthetic"):
        raise ValueError("invalid_input_domain")
    now = now or utc_now
    run = uuid.uuid4().hex
    db.insert("ingest_runs", {"run_id": run, "started_at": timestamp(now()), "status": "RUNNING",
              "input_root": str(root), "input_domain": domain, "importer_version": IMPORTER_VERSION,
              "code_sha256": engine_hash()})
    records, errors = [], []
    for p in sorted((root / "manifest").glob("*.json")):
        try:
            body = p.read_bytes(); m = decode(body); checked_manifest(m)
            records.append((p, sha256(body), m))
        except (IngestError, ValueError, TypeError, KeyError, OSError) as exc:
            reason=str(exc) if isinstance(exc,IngestError) else "manifest_io_failure" if isinstance(exc,OSError) else "manifest_schema_error"
            errors.append((p,None,reason))
    origins = matching_origins(records)
    # Original receipts precede reprocessing, independent of directory enumeration order.
    records.sort(key=lambda item: (item[2].get("network_download_performed") is False,
                                  timestamp(item[2]["fetched_at_utc"]), item[1]))
    counts = Counter()
    try:
        for p, mh, m in records:
            try:
                if m.get("input_domain", "market") != domain:
                    raise IngestError("input_domain_mismatch")
                if db.conn.execute("SELECT 1 FROM manifest_links WHERE manifest_sha256=?", (mh,)).fetchone():
                    # Revalidate bytes even when an import is already known.
                    raw_values(m, "unused", root, domain, now())
                    if m["status"] == "success": normalize_checked(m, root)
                    counts["idempotent"] += 1
                    continue
                db.conn.execute("BEGIN IMMEDIATE")
                if m.get("network_download_performed") is False:
                    key = (m["request_id"], m.get("sha256"), m["fetched_at_utc"])
                    candidates=origins.get(key,[])
                    if m.get("origin_receipt_id"):
                        candidates=[c for c in candidates if c[0]==m["origin_receipt_id"]]
                    else:
                        explicit=[c for c in candidates if c[1].get("network_download_performed") is True]
                        candidates=explicit or candidates
                    if not candidates:
                        raise IngestError("reprocess_without_origin_receipt")
                    if len({c[0] for c in candidates})!=1:
                        raise IngestError("ambiguous_reprocess_origin_receipt")
                    rid = candidates[0][0]
                    if not db.conn.execute("SELECT 1 FROM raw_receipts WHERE receipt_id=?", (rid,)).fetchone():
                        raise IngestError("origin_receipt_quarantined")
                    raw_values(m,rid,root,domain,now())
                else:
                    rid = receipt_id(m, mh)
                    vals = raw_values(m, rid, root, domain, now())
                    existing = db.conn.execute("SELECT * FROM raw_receipts WHERE receipt_id=?", (rid,)).fetchone()
                    if existing and any(existing[k] != v for k, v in vals.items() if k != "ingested_at"):
                        raise IngestError("receipt_id_reused_with_different_event")
                    db.insert("raw_receipts", vals, ignore=True)
                db.insert("manifest_links", {"manifest_sha256": mh, "manifest_path": str(p), "receipt_id": rid,
                          "manifest_json": canonical(m).decode(), "network_performed": m.get("network_download_performed"), "run_id": run})
                if m["status"] == "success":
                    if m.get("http_status") is None or not 200 <= m["http_status"] < 300:
                        raise IngestError("successful_artifact_with_failed_http")
                    path, digest, data, verification = normalize_checked(m, root)
                    artifact = sha256(canonical([digest, m["code_sha256"], m["schema_version"], domain]))
                    db.insert("normalized_artifacts", {"artifact_id": artifact, "raw_sha256": m["sha256"],
                              "normalized_sha256": digest, "normalized_path": str(path), "schema_version": m["schema_version"],
                              "normalizer_version": m.get("collector_version", "unknown"), "normalizer_code_sha256": m["code_sha256"],
                              "importer_version": IMPORTER_VERSION, "hash_verification": verification,
                              "input_domain": domain, "ingested_at": timestamp(now())}, ignore=True)
                    completed = timestamp(m.get("processing_completed_at_utc"))
                    validation_completed = timestamp(now())
                    received = timestamp(m["fetched_at_utc"])
                    if completed and completed < received:
                        raise IngestError("processing_before_receipt")
                    db.insert("artifact_receipts", {"artifact_id": artifact, "receipt_id": rid, "manifest_sha256": mh,
                              "parsing_completed_at": completed, "validation_completed_at": validation_completed,
                              "usable_at": completed or validation_completed,
                              "usable_evidence": "collector_processing_completion" if completed else "actual_import_validation_legacy_processing_unknown"}, ignore=True)
                    add_prices(db, data, artifact, root, m, domain)
                    add_actions(db, data, artifact, root, m, domain)
                    add_symbols(db, data, artifact, root, m, domain)
                    counts["successful_artifacts"] += 1
                else:
                    counts["source_failure_receipts"] += 1
                if interrupt_after is not None and counts["imported_manifests"] >= interrupt_after:
                    raise KeyboardInterrupt("synthetic interrupted import")
                db.conn.execute("COMMIT")
                counts["imported_manifests"] += 1
            except (IngestError, ValueError, KeyError, TypeError, sqlite3.IntegrityError, OverflowError, OSError) as exc:
                if db.conn.in_transaction: db.conn.execute("ROLLBACK")
                reason=str(exc) if isinstance(exc,IngestError) else "artifact_io_failure" if isinstance(exc,OSError) else "schema_or_constraint_failure"
                errors.append((p,mh,reason))
        for p, mh, reason in errors:
            db.insert("ingest_issues", {"run_id": run, "manifest_path": str(p), "manifest_sha256": mh, "reason": reason})
        summary = {"status": "PARTIAL" if errors else "PASS" if records else "EMPTY", "run_id": run,
                   "input_domain": domain, "counts": dict(counts), "quarantined": len(errors),
                   "quarantine_reasons": dict(Counter(r for _, _, r in errors)), "database_counts": db.counts(),
                   "source_failure_note": "Source HTTP failures are receipts only, never price data; import PASS is not source access PASS."}
        db.conn.execute("UPDATE ingest_runs SET finished_at=?,status=?,summary_json=? WHERE run_id=?",
                        (timestamp(now()), "PARTIAL" if errors else "PASS", canonical(summary).decode(), run))
        return summary
    except BaseException:
        if db.conn.in_transaction: db.conn.execute("ROLLBACK")
        db.conn.execute("UPDATE ingest_runs SET finished_at=?,status='INTERRUPTED' WHERE run_id=?", (timestamp(now()), run))
        raise


def import_calendar(db, path, *, domain="market", known_at=None):
    path = Path(path).resolve()
    body = path.read_bytes(); rows = decode(body)
    if not isinstance(rows, list): raise IngestError("calendar_schema_mismatch")
    digest = sha256(body)
    library = version("exchange_calendars")
    cv = sha256(canonical([digest, library, domain]))
    known = timestamp(known_at or utc_now())
    db.conn.execute("BEGIN IMMEDIATE")
    try:
        for row in rows:
            vals = {"calendar_version": cv, "exchange": row["exchange"], "trading_date": day(row["trading_date"]),
                    "open_utc": timestamp(row["open_utc"]), "close_utc": timestamp(row["close_utc"]),
                    "timezone": row["timezone"], "calendar_source": row["source"], "library_version": library,
                    "artifact_path": str(path), "artifact_sha256": digest, "known_at": known,
                    "publication_at": timestamp(row.get("publication_time_utc")), "historical_evidence_json": "{}",
                    "input_domain": domain}
            db.insert("trading_session_versions", {"session_version_id": sha256(canonical([cv, row["exchange"], row["trading_date"]])), **vals}, ignore=True)
        db.conn.execute("COMMIT")
    except BaseException:
        db.conn.execute("ROLLBACK"); raise
    return {"status": "PASS" if rows else "EMPTY", "calendar_version": cv, "rows": len(rows),
            "calendar_source": sorted({r["source"] for r in rows}), "historical_notice_verification": "unverified"}
