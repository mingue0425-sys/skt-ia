"""Separate SQLite dataset metadata: immutable versions and frozen membership."""
import json
from pathlib import Path
import sqlite3
from ..http import utc_now
from ..pit import replay_snapshot
from ..storage import canonical, sha256
from ..pit.time import timestamp

MIGRATION = """
CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL);
CREATE TABLE samples(sample_id TEXT PRIMARY KEY);
CREATE TABLE feature_versions(
 feature_version_id TEXT PRIMARY KEY, sample_id TEXT NOT NULL REFERENCES samples,
 content_sha256 TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE label_versions(
 label_version_id TEXT PRIMARY KEY, sample_id TEXT NOT NULL REFERENCES samples, horizon_sessions INTEGER NOT NULL CHECK(horizon_sessions IN (1,5,20)),
 content_sha256 TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL, label_available_at TEXT, status TEXT NOT NULL CHECK(status IN ('ready','pending','blocked','invalid')),
 created_at TEXT NOT NULL);
CREATE INDEX label_availability ON label_versions(sample_id,horizon_sessions,label_available_at);
CREATE TABLE dataset_snapshots(
 snapshot_id TEXT PRIMARY KEY, content_sha256 TEXT NOT NULL UNIQUE, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE dataset_items(
 snapshot_id TEXT NOT NULL REFERENCES dataset_snapshots, ordinal INTEGER NOT NULL, sample_id TEXT NOT NULL REFERENCES samples,
 feature_version_id TEXT NOT NULL REFERENCES feature_versions, label_version_id TEXT NOT NULL REFERENCES label_versions,
 horizon_sessions INTEGER NOT NULL, PRIMARY KEY(snapshot_id,ordinal), UNIQUE(snapshot_id,sample_id,horizon_sessions));
"""
TABLES=("samples","feature_versions","label_versions","dataset_snapshots","dataset_items")


class DatasetStore:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(self.path,isolation_level=None,timeout=10); self.conn.row_factory=sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON"); self.conn.execute("PRAGMA synchronous=FULL")
        if not self.conn.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone():
            guards="\n".join(f"CREATE TRIGGER {table}_no_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable_dataset_version'); END;" for table in TABLES for op in ("UPDATE","DELETE"))
            self.conn.executescript("BEGIN IMMEDIATE;"+MIGRATION+guards+f"INSERT INTO schema_migrations VALUES(1,'{sha256(MIGRATION.encode())}');COMMIT;")
        if [tuple(r) for r in self.conn.execute("SELECT * FROM schema_migrations")]!=[(1,sha256(MIGRATION.encode()))]:
            raise ValueError("unsupported_dataset_schema_or_migration_checksum")

    def close(self): self.conn.close()
    def __enter__(self): return self
    def __exit__(self,*exc): self.close()

    def _add(self,table,payload):
        content=canonical(payload); digest=sha256(content); sid=payload["sample_id"]
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute("INSERT OR IGNORE INTO samples VALUES(?)",(sid,))
            if table=="feature_versions":
                self.conn.execute("INSERT OR IGNORE INTO feature_versions VALUES(?,?,?,?,?)",(digest,sid,digest,content.decode(),utc_now()))
            else:
                self.conn.execute("INSERT OR IGNORE INTO label_versions VALUES(?,?,?,?,?,?,?,?)",
                    (digest,sid,payload["horizon_sessions"],digest,content.decode(),payload["label_available_at"],payload["status"],utc_now()))
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK"); raise
        return digest

    def add_feature(self,payload): return self._add("feature_versions",payload)
    def add_label(self,payload): return self._add("label_versions",payload)

    def freeze(self,items,metadata):
        items=sorted(items,key=lambda i:(i["sample_id"],i["horizon_sessions"],i["feature_version_id"],i["label_version_id"]))
        digest=sha256(canonical({"metadata":metadata,"items":items}))
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            old=self.conn.execute("SELECT snapshot_id FROM dataset_snapshots WHERE snapshot_id=?",(digest,)).fetchone()
            if not old:
                self.conn.execute("INSERT INTO dataset_snapshots VALUES(?,?,?,?)",(digest,digest,canonical(metadata).decode(),utc_now()))
                for ordinal,item in enumerate(items):
                    self.conn.execute("INSERT INTO dataset_items VALUES(?,?,?,?,?,?)",
                        (digest,ordinal,item["sample_id"],item["feature_version_id"],item["label_version_id"],item["horizon_sessions"],))
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK"); raise
        return {"status":"PASS","snapshot_id":digest,"content_sha256":digest,"reused":bool(old),"items":len(items),
                "selected_count":metadata["ready_pair_count"],"learning_ready_pair_count":metadata["learning_ready_pair_count"],
                "feature_status_counts":metadata["feature_status_counts"],"label_status_counts":metadata["label_status_counts"],"reason_counts":metadata["reason_counts"]}

    def payload(self,kind,version_id):
        table="feature_versions" if kind=="feature" else "label_versions"
        column="feature_version_id" if kind=="feature" else "label_version_id"
        row=self.conn.execute(f"SELECT * FROM {table} WHERE {column}=?",(version_id,)).fetchone()
        if not row: raise ValueError("dataset_version_missing")
        value=json.loads(row["payload_json"])
        if sha256(canonical(value))!=row["content_sha256"] or row["content_sha256"]!=version_id:
            raise ValueError("dataset_version_content_hash_mismatch")
        if value["sample_id"]!=row["sample_id"]: raise ValueError("dataset_version_identity_mismatch")
        if kind=="label" and (row["horizon_sessions"]!=value["horizon_sessions"] or row["status"]!=value["status"] or row["label_available_at"]!=value["label_available_at"]):
            raise ValueError("label_version_index_metadata_mismatch")
        return value

    def audit(self):
        failures=[]
        if self.conn.execute("PRAGMA integrity_check").fetchone()[0]!="ok": failures.append("integrity_check")
        if self.conn.execute("PRAGMA foreign_key_check").fetchall(): failures.append("foreign_key_check")
        for table in TABLES:
            for op in ("update","delete"):
                if not self.conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",(f"{table}_no_{op}",)).fetchone(): failures.append("immutable_guard_missing")
        return {"status":"FAIL" if failures else "PASS","failures":failures,"schema_version":1}


def dataset_metadata(store,snapshot_id):
    row=store.conn.execute("SELECT * FROM dataset_snapshots WHERE snapshot_id=?",(snapshot_id,)).fetchone()
    if not row: raise ValueError("dataset_snapshot_not_found")
    metadata=json.loads(row["metadata_json"])
    items=[{k:r[k] for k in ("sample_id","feature_version_id","label_version_id","horizon_sessions")}
           for r in store.conn.execute("SELECT * FROM dataset_items WHERE snapshot_id=? ORDER BY ordinal",(snapshot_id,))]
    if sha256(canonical({"metadata":metadata,"items":items}))!=row["content_sha256"]:
        raise ValueError("dataset_snapshot_content_hash_mismatch")
    return metadata,items


def iter_dataset_rows(store,pit_db,snapshot_id,*,verify_files=True):
    _,items=dataset_metadata(store,snapshot_id); checked=set()
    for item in items:
        feature=store.payload("feature",item["feature_version_id"]); label=store.payload("label",item["label_version_id"])
        if feature["sample_id"]!=item["sample_id"] or label["sample_id"]!=item["sample_id"] or label["horizon_sessions"]!=item["horizon_sessions"]:
            raise ValueError("dataset_item_link_mismatch")
        for payload,keys in ((feature,("feature_snapshot_id",)),(label,("label_snapshot_id","action_snapshot_id"))):
            for key in keys:
                sid=payload[key]
                if sid is None: continue  # An invalid sample was rejected before any query.
                if sid not in checked:
                    replay_snapshot(pit_db,sid,verify_files=verify_files); checked.add(sid)
            if verify_files:
                dependencies=list(payload.get("evidence_dependencies",[]))+payload.get("calendar",{}).get("artifact_references",[])
                for evidence in dependencies:
                    path=Path(evidence["path"])
                    if not path.is_file() or sha256(path.read_bytes())!=evidence["sha256"]:
                        raise ValueError("dataset_assertion_file_missing_or_corrupt")
        yield item|{"feature":feature,"label":label}


def replay_dataset(store,pit_db,snapshot_id,*,verify_files=True):
    metadata,items=dataset_metadata(store,snapshot_id)
    count=ready=learning=0
    for row in iter_dataset_rows(store,pit_db,snapshot_id,verify_files=verify_files):
        count+=1
        ready+=int(row["feature"]["sample_status"]=="ready" and row["label"]["status"]=="ready")
        learning+=int(row["label"].get("learning_ready",False))
    if metadata.get("ready_pair_count",ready)!=ready or metadata.get("learning_ready_pair_count",learning)!=learning:
        raise ValueError("dataset_readiness_count_mismatch")
    return {"status":"PASS","snapshot_id":snapshot_id,"content_sha256":snapshot_id,"items":count,"metadata":metadata,
            "selected_count":ready,"learning_ready_pair_count":learning,
            "file_evidence_verified":verify_files,"scope":"fixed versions; never reruns feature/label queries"}


def training_selection(store,pit_db,snapshot_id,*,training_asof,allow_research=False,scope="strict_history"):
    from ..observed.scope import SCOPES, learning_reasons
    if scope not in SCOPES or (allow_research and scope != "strict_history"):
        raise ValueError("invalid_or_ambiguous_training_scope")
    asof=timestamp(training_asof); selected=[]; reasons={}
    if asof is None: raise ValueError("training_asof_required")
    for row in iter_dataset_rows(store,pit_db,snapshot_id):
        feature,label=row["feature"],row["label"]; reason=None
        if scope != "strict_history":
            blocked = learning_reasons(feature, label, scope, asof)
            if blocked:
                for why in blocked: reasons[why] = reasons.get(why, 0) + 1
            else: selected.append({k:row[k] for k in ("sample_id","horizon_sessions","feature_version_id","label_version_id")})
            continue
        if feature["sample_status"]!="ready": reason="feature_sample_not_ready"
        elif feature["feature_ready_at"] is None or feature["feature_ready_at"]>feature["intended_entry_at"]: reason="feature_not_ready_before_entry"
        elif feature["decision_at"]>asof or feature["feature_ready_at"]>asof: reason="feature_after_training_asof"
        elif label["status"]!="ready": reason="label_not_ready"
        elif not label["label_available_at"] or label["label_available_at"]>asof: reason="label_after_training_asof"
        elif not allow_research and label.get("learning_rights_available_at") and label["learning_rights_available_at"]>asof: reason="learning_rights_after_training_asof"
        elif not allow_research and not label["learning_ready"]: reason="learning_rights_or_strict_pit_requirements_unmet"
        if reason: reasons[reason]=reasons.get(reason,0)+1
        else: selected.append({k:row[k] for k in ("sample_id","horizon_sessions","feature_version_id","label_version_id")})
    return {"status":"OK" if selected else "EMPTY","training_asof":asof,"snapshot_id":snapshot_id,"selected":selected,
            "selected_count":len(selected),"exclusion_counts":reasons,"scope":"RESEARCH_DIAGNOSTIC_NOT_TRAINING" if allow_research else "training_eligible_only" if scope=="strict_history" else scope, "reason_counts_overlap":scope!="strict_history"}
