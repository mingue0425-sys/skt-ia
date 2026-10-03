"""Fictional stage-1-compatible evidence. NEVER fetches market data."""
import copy
from pathlib import Path
from market_research.providers.public import bar, blank
from market_research.storage import canonical, sha256, write_json, atomic_write

RULE = {"id": "date_end_plus_delay", "version": "1", "timezone": "America/New_York", "delay_hours": 1}


def append(root, *, label, symbol="SYN", close=100, published="2024-01-02T21:05:00Z",
           received="2024-01-02T21:06:00Z", usable="2024-01-02T21:07:00Z", revised=None,
           publication_date=None, proof=True, network=True, normalizer="synthetic-normalizer-v1",
           date="2024-01-02", observation_end=True, local_label=None, meaning="as_traded",
           extra=None, mappings=None, actions=None, raw_body=None, legacy_hash=False, legacy_processing=False, source="synthetic"):
    root = Path(root)
    spec = {"source": source, "kind": "daily", "symbol": symbol, "start": date, "end": date}
    rid = sha256(canonical(spec))
    raw = raw_body or canonical({"SYNTHETIC_TEST_ONLY": True, "label": label, "close": close, "symbol": symbol})
    rh = sha256(raw); raw_path = f"raw/synthetic/{rid}/{rh}.bin"
    atomic_write(root/raw_path, raw)
    row = bar(spec, date, [close, close+1, close-1, close, 1000], meaning)
    row.update(publication_time_utc=published, revision_time_utc=revised, raw_sha256=rh, fetched_at_utc=received,
               volume_unit="synthetic_shares", volume_adjustment_basis="synthetic_unadjusted")
    if local_label: row["local_instrument_id"] = local_label
    if publication_date:
        row.update(publication_date=publication_date, publication_precision="date", publication_timezone="America/New_York")
    if observation_end:
        row.update(observation_end_utc=date+"T21:00:00Z", observation_end_evidence="synthetic_provider_daily_interval")
    if extra: row.update(copy.deepcopy(extra))
    if proof and (published or publication_date):
        evidence = {"kind": "synthetic_version_record", "version_raw_sha256": rh, "published_at": published,
                    "publication_date": publication_date, "revised_at": revised, "verified": True,
                    "verification_method": "fictional hand-written test oracle; NOT real publication evidence"}
        ep = f"evidence/{label}-{rh}.json"; write_json(root/ep,evidence)
        row["publication_evidence"] = {"path": ep, "sha256": sha256((root/ep).read_bytes())}
    data = blank(); data["bars"] = [row]
    if mappings:
        data["symbol_assertions"] = []
        for mapping in mappings:
            m = {k: copy.deepcopy(v) for k,v in row.items() if k in (
                "source","requested_symbol","local_instrument_id","publication_time_utc","revision_time_utc",
                "publication_evidence","publication_date","publication_precision","publication_timezone","security_identity")}
            m.update(mapping); data["symbol_assertions"].append(m)
    if actions:
        for action in actions:
            a = {k: copy.deepcopy(v) for k,v in row.items() if k in (
                "source","requested_symbol","local_instrument_id","publication_time_utc","revision_time_utc",
                "publication_evidence","raw_sha256","fetched_at_utc")}
            a.update(action); data["actions"].append(a)
    data["provenance"] = {"request_id": rid, "raw_sha256": rh, "schema_version": "1.0.0",
                          "config_sha256": "synthetic-config", "code_sha256": normalizer}
    nh = sha256(canonical(data)); np = f"normalized/synthetic/{rid}/{nh}.json"
    write_json(root/np, data)
    manifest = {"schema_version": "1.0.0", "source": source, "request_parameters": spec,
                "request_id": rid, "receipt_id": "synthetic-receipt-"+label, "fetched_at_utc": received,
                "recorded_at_utc": received, "network_download_performed": network, "status": "success",
                "http_status": 200, "raw_path": raw_path, "sha256": rh, "file_size_bytes": len(raw),
                "normalized_path": np, "normalized_sha256": sha256((root/np).read_bytes()),
                "code_sha256": normalizer, "collector_version": "synthetic-1", "config_sha256": "synthetic-config",
                "processing_completed_at_utc": usable, "input_domain": "synthetic", "empty_response": False}
    if legacy_hash: manifest.pop("normalized_sha256")
    if legacy_processing: manifest.pop("processing_completed_at_utc")
    mp = root/f"manifest/{label}.json"; write_json(mp,manifest)
    return {"manifest": manifest, "manifest_path": mp, "row": row, "raw": raw, "normalized": data}


def story(root, case):
    if case == "initial_and_correction":
        append(root,label="first")
        append(root,label="correction",close=110,published="2024-01-02T21:05:00Z",revised="2024-01-05T21:05:00Z",
               received="2024-01-05T21:06:00Z",usable="2024-01-05T21:07:00Z")
    elif case == "late_event_publication":
        append(root,label="late",published="2024-01-05T21:05:00Z",received="2024-01-05T21:06:00Z",usable="2024-01-05T21:07:00Z")
    elif case == "early_publication_late_receipt":
        append(root,label="late-receipt",received="2024-01-05T21:06:00Z",usable="2024-01-05T21:07:00Z")
    elif case == "null_publication": append(root,label="null",published=None,proof=False)
    elif case == "date_only_publication": append(root,label="date",published=None,publication_date="2024-01-03",proof=False)
    elif case == "unordered_conflict":
        append(root,label="conflict-a")
        append(root,label="conflict-b",close=120)
    else: raise ValueError("unknown_synthetic_case")
