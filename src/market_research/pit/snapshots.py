"""Fixed results, separate from rerunning a cutoff against a changed database."""
import json
from pathlib import Path
from ..http import utc_now
from ..storage import canonical, sha256
from .time import timestamp
from .database import version_value_hash


def create_snapshot(db, result):
    content = canonical(result)
    digest = sha256(content)
    existing = db.conn.execute("SELECT snapshot_id FROM query_snapshots WHERE content_sha256=?", (digest,)).fetchone()
    if existing: return {"status": "PASS", "snapshot_id": existing[0], "content_sha256": digest, "reused": True,
                         "data_status":result["status"],"selected_count":result["selected_count"]}
    sid = digest
    db.conn.execute("BEGIN IMMEDIATE")
    try:
        db.insert("query_snapshots", {"snapshot_id": sid, "created_at": timestamp(utc_now()),
                  "cutoff": result["request"]["cutoff"], "policy": result["request"]["policy"],
                  "content_sha256": digest, "payload_json": content.decode(), "schema_version": result["schema_version"],
                  "policy_version": result["request"]["policy_version"], "code_sha256": result["engine_code_sha256"]})
        for i, row in enumerate(result["data"]):
            prices = result["request"]["family"] == "prices"
            db.insert("snapshot_items", {"snapshot_id": sid, "ordinal": i,
                      "price_version_id": row["version_id"] if prices else None,
                      "action_version_id": None if prices else row["version_id"],
                      "artifact_id": row["artifact_id"], "receipt_id": row["provenance"]["receipt_id"]})
        db.conn.execute("COMMIT")
    except BaseException:
        db.conn.execute("ROLLBACK"); raise
    return {"status": "PASS", "snapshot_id": sid, "content_sha256": digest, "reused": False,
            "data_status": result["status"], "selected_count": result["selected_count"]}


def replay_snapshot(db, snapshot_id, *, verify_files=True):
    stored = db.conn.execute("SELECT * FROM query_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    if not stored: raise ValueError("snapshot_not_found")
    payload = json.loads(stored["payload_json"])
    if sha256(canonical(payload)) != stored["content_sha256"]: raise ValueError("snapshot_content_hash_mismatch")
    items = db.conn.execute("SELECT * FROM snapshot_items WHERE snapshot_id=? ORDER BY ordinal", (snapshot_id,)).fetchall()
    if len(items) != payload["selected_count"]: raise ValueError("snapshot_item_count_mismatch")
    checked_files = set()
    for item, row in zip(items, payload["data"]):
        vid = item["price_version_id"] or item["action_version_id"]
        if vid != row["version_id"] or item["artifact_id"] != row["artifact_id"] or item["receipt_id"] != row["provenance"]["receipt_id"]:
            raise ValueError("snapshot_link_mismatch")
        table = "daily_price_versions" if item["price_version_id"] else "corporate_action_versions"
        current = db.conn.execute(f"SELECT * FROM {table} WHERE version_id=?", (vid,)).fetchone()
        if not current or current["value_sha256"] != row["value_sha256"] or version_value_hash(current)!=row["value_sha256"]:
            raise ValueError("snapshot_version_mismatch")
        artifact = db.conn.execute("SELECT * FROM normalized_artifacts WHERE artifact_id=?", (item["artifact_id"],)).fetchone()
        receipt = db.conn.execute("SELECT * FROM raw_receipts WHERE receipt_id=?", (item["receipt_id"],)).fetchone()
        if artifact["normalized_sha256"] != row["provenance"]["normalized_sha256"] or receipt["raw_sha256"] != row["provenance"]["raw_sha256"]:
            raise ValueError("snapshot_artifact_mismatch")
        paths = [(artifact["normalized_path"], artifact["normalized_sha256"]), (receipt["raw_path"], receipt["raw_sha256"])]
        proof = row["provenance"]["publication_evidence"]
        if proof: paths.append((proof["path"], proof["sha256"]))
        if row["observation_bound"] and row["observation_bound"].get("session_version_id"):
            session = db.conn.execute("SELECT * FROM trading_session_versions WHERE session_version_id=?", (row["observation_bound"]["session_version_id"],)).fetchone()
            if not session or session["artifact_sha256"] != row["observation_bound"]["calendar_artifact_sha256"]:
                raise ValueError("snapshot_calendar_mismatch")
            paths.append((session["artifact_path"], session["artifact_sha256"]))
        if verify_files:
            for path, digest in paths:
                if (path, digest) in checked_files:
                    continue
                if not path or not Path(path).is_file() or sha256(Path(path).read_bytes()) != digest:
                    raise ValueError("snapshot_evidence_file_missing_or_corrupt")
                checked_files.add((path, digest))
    # No query() call: later receipts, mappings, corrections and code do not alter the saved payload.
    return {"status": "PASS", "snapshot_id": snapshot_id, "content_sha256": stored["content_sha256"],
            "file_evidence_verified": verify_files, "result": payload}
