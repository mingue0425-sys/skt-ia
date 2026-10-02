from pathlib import Path
from .http import HTTPClient, utc_now, Response
from .providers.public import get_provider, ProviderError
from .storage import Store, sha256, canonical, atomic_write, write_json, code_version
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode

SAFE_FIELDS = {"source", "kind", "symbol", "start", "end", "outputsize", "auth_mode", "document_url"}


def public_spec(spec):
    # Allow-list rather than relying on incomplete credential-key redaction.
    safe = {k: v for k, v in spec.items() if k in SAFE_FIELDS}
    if "document_url" in safe:
        parsed = urlsplit(safe["document_url"])
        query = {k: v for k, v in parse_qs(parsed.query).items() if k == "id"}
        safe["document_url"] = urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path,
                                          urlencode(query, doseq=True), ""))
    return safe


def http_category(response):
    if response.error:
        if response.error == "retry_after_exceeds_local_wait_limit":
            return "rate_limited" if response.status == 429 else "http_error"
        return response.error
    if response.status in (401,):
        return "authentication_failure"
    if response.status == 403:
        return "access_restricted"
    if response.status == 404:
        return "not_found"
    if response.status == 429:
        return "rate_limited"
    if response.status is None:
        return "network_error"
    if not 200 <= response.status < 300:
        return "http_error"
    return None


def collect_one(store, client, spec, *, project_root, config_hash, refresh=False):
    safe = public_spec(spec)
    request_id = sha256(canonical(safe))
    cached = None if refresh else store.cached(request_id)
    current_code = code_version(project_root)
    if cached and cached["code_sha256"] == current_code:
        return {"request_id": request_id, "status": "cached", "data_status": "success",
                "manifest_record": cached}
    provider = get_provider(safe["source"])
    if cached:
        response = Response(cached["http_status"], (store.root / cached["raw_path"]).read_bytes(),
                            fetched_at_utc=cached["fetched_at_utc"], attempts=cached.get("attempts", []))
    else:
        try:
            if safe.get("document_url") != spec.get("document_url"):
                raise ProviderError("unsafe_document_url")
            url, spacing, secrets = provider.request(safe)
            response = client.get(url, spacing=spacing, secrets=secrets)
        except ProviderError as e:
            response = Response(None, b"", error=e.category)
    digest = sha256(response.body) if response.body else None
    raw_path = None
    if response.body:
        raw_path = str(Path("raw") / safe["source"] / request_id / (digest + ".bin"))
        path = store.root / raw_path
        if not path.exists():
            atomic_write(path, response.body)
        elif sha256(path.read_bytes()) != digest:
            raise RuntimeError("raw_integrity_error")
    record = {"schema_version": "1.0.0", "source": safe["source"],
              "request_parameters": safe, "request_id": request_id,
              "fetched_at_utc": response.fetched_at_utc, "http_status": response.status,
              "recorded_at_utc": utc_now(), "network_download_performed": not bool(cached),
              "attempts": response.attempts, "status": http_category(response),
              "raw_path": raw_path, "sha256": digest, "file_size_bytes": len(response.body),
              "row_count": None, "document_count": None, "actual_data_period": None,
              "empty_response": None, "body_empty": not bool(response.body),
              "normalized_path": None, "config_sha256": config_hash,
              "code_sha256": current_code, "collector_version": "0.1.0"}
    if record["status"] is None:
        if not response.body.strip():
            record.update(status="empty_response", empty_response=True, row_count=0, document_count=0)
        else:
            try:
                out = provider.normalize(safe, response.body)
                count = sum(len(out[k]) for k in ("bars", "actions", "directory"))
                docs = len(out["documents"])
                dates = [x["trading_date"] for x in out["bars"]]
                record.update(status="success" if count or docs else "empty_response",
                              row_count=count, document_count=docs, empty_response=not (count or docs),
                              counts={k: len(out[k]) for k in ("bars", "actions", "directory", "documents")},
                              actual_data_period={"start": min(dates), "end": max(dates)} if dates else None)
                for family in ("bars", "actions", "directory", "documents"):
                    for row in out[family]:
                        row.update(raw_sha256=digest, fetched_at_utc=response.fetched_at_utc)
                out["provenance"] = {"request_id": request_id, "raw_sha256": digest,
                                     "schema_version": "1.0.0", "config_sha256": config_hash,
                                     "code_sha256": record["code_sha256"]}
                norm_hash = sha256(canonical(out))
                norm_path = str(Path("normalized") / safe["source"] / request_id / (norm_hash + ".json"))
                write_json(store.root / norm_path, out)
                record["normalized_path"] = norm_path
                record["normalized_sha256"] = sha256((store.root / norm_path).read_bytes())
            except ProviderError as e:
                record["status"] = e.category
    store.save_record(record)
    return {"request_id": request_id, "status": "reprocessed" if cached and record["status"] == "success" else record["status"],
            "manifest_record": record}


def collect_batch(specs, data_root, project_root, config_bytes, refresh=False, client=None):
    store = Store(data_root)
    client = client or HTTPClient(budget=store.daily_budget)
    results = []
    for spec in specs:
        try:
            results.append(collect_one(store, client, spec, project_root=project_root,
                                      config_hash=sha256(config_bytes), refresh=refresh))
        except (ValueError, KeyError, RuntimeError, OSError, ProviderError) as e:
            # Unexpected failures don't expose exceptions (which may embed URLs).
            # Continue batch, preserving any originals already atomically written.
            safe = public_spec(spec)
            record = {"schema_version": "1.0.0", "request_id": sha256(canonical(safe)),
                      "source": safe.get("source"), "request_parameters": safe,
                      "fetched_at_utc": utc_now(), "status": "internal_failure",
                      "error_type": type(e).__name__, "raw_path": None, "normalized_path": None,
                      "http_status": None, "sha256": None, "file_size_bytes": None,
                      "row_count": None, "document_count": None, "actual_data_period": None,
                      "empty_response": None, "code_sha256": code_version(project_root),
                      "config_sha256": sha256(config_bytes)}
            store.save_record(record)
            results.append({"status": "internal_failure", "manifest_record": record})
    return results
