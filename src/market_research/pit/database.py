"""A single-process SQLite store with immutable evidence and version rows."""
import json
from pathlib import Path
import sqlite3
from ..http import utc_now
from ..storage import canonical, sha256
from .migrations import MIGRATIONS

IMPORTER_VERSION = "2.0.0"
SCHEMA_VERSION = 2
IMMUTABLE = ("raw_receipts", "manifest_links", "normalized_artifacts", "artifact_receipts",
             "security_records", "symbol_assertions", "daily_price_versions",
             "corporate_action_versions", "trading_session_versions", "query_snapshots", "snapshot_items")


def version_value_hash(row):
    non_value = {"artifact_id","temporal_json","evidence_json","version_id","value_sha256","previous_version_id","previous_relation"}
    return sha256(canonical({k:v for k,v in dict(row).items() if k not in non_value}))


def engine_hash():
    root = Path(__file__).parent
    return sha256(canonical({p.name: sha256(p.read_bytes()) for p in sorted(root.glob("*.py"))}))


class Database:
    def __init__(self, path, *, initialize=False):
        self.path = Path(path)
        if not initialize and not self.path.is_file():
            raise ValueError("database_not_initialized")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute("PRAGMA synchronous=FULL")
        if initialize:
            self.migrate()
        self._check_migrations()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def migrate(self):
        present = self.conn.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone()
        actual = [(r[0],r[1]) for r in self.conn.execute("SELECT version,checksum FROM schema_migrations ORDER BY version")] if present else []
        expected = [(n,sha256(sql.encode())) for n,sql in MIGRATIONS]
        if actual != expected[:len(actual)]:
            raise ValueError("unsupported_schema_or_migration_checksum")
        for number, sql in MIGRATIONS[len(actual):]:
            # executescript commits implicitly; BEGIN and COMMIT are inside the script.
            triggers = "\n".join(
                f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT,'immutable_version'); END;"
                for table in IMMUTABLE for operation in ("UPDATE", "DELETE")) if number == 1 else ""
            checksum = sha256(sql.encode())
            self.conn.executescript("BEGIN IMMEDIATE;\n" + sql + "\n" + triggers +
                f"\nINSERT INTO schema_migrations VALUES({number},'{checksum}','{utc_now()}');\nCOMMIT;")
        self._check_migrations()

    def _check_migrations(self):
        try:
            actual = [(r[0], r[1]) for r in self.conn.execute("SELECT version,checksum FROM schema_migrations ORDER BY version")]
        except sqlite3.Error:
            raise ValueError("schema_migration_required") from None
        expected = [(n, sha256(sql.encode())) for n, sql in MIGRATIONS]
        if actual != expected:
            raise ValueError("unsupported_schema_or_migration_checksum")

    def insert(self, table, values, *, ignore=False):
        # Only internally supplied table/column names reach this helper.
        keys = list(values)
        verb = "INSERT OR IGNORE" if ignore else "INSERT"
        self.conn.execute(f"{verb} INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                          [values[k] for k in keys])

    def counts(self):
        return {t: self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in IMMUTABLE+("version_edges",)}

    def audit(self, *, files=True):
        failures = []
        if not self.conn.execute("PRAGMA foreign_keys").fetchone()[0]: failures.append({"reason":"foreign_keys_disabled"})
        try: self._check_migrations()
        except ValueError: failures.append({"reason":"migration_checksum"})
        triggers = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        for table in IMMUTABLE+("version_edges",):
            for operation in ("update","delete"):
                if table+"_no_"+operation not in triggers: failures.append({"reason":"missing_immutable_guard","table":table})
        integrity = [r[0] for r in self.conn.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            failures.append({"reason": "sqlite_integrity", "details": integrity})
        for row in self.conn.execute("PRAGMA foreign_key_check"):
            failures.append({"reason": "foreign_key", "details": list(row)})
        links = self.conn.execute("""SELECT a.artifact_id FROM normalized_artifacts a
            LEFT JOIN artifact_receipts l ON l.artifact_id=a.artifact_id LEFT JOIN raw_receipts r ON r.receipt_id=l.receipt_id
            WHERE l.artifact_id IS NULL OR a.raw_sha256!=r.raw_sha256 OR r.status!='success' OR a.input_domain!=r.input_domain""").fetchall()
        failures.extend({"reason": "artifact_receipt_link", "id": r[0]} for r in links)
        for table in ("daily_price_versions","corporate_action_versions"):
            for r in self.conn.execute(f"SELECT * FROM {table}"):
                if version_value_hash(r)!=r["value_sha256"]: failures.append({"reason":"version_value_hash_mismatch","id":r["version_id"]})
        if files:
            checks = [(r["raw_path"], r["raw_sha256"]) for r in self.conn.execute("SELECT * FROM raw_receipts WHERE raw_path IS NOT NULL")]
            checks += [(r["normalized_path"], r["normalized_sha256"]) for r in self.conn.execute("SELECT * FROM normalized_artifacts")]
            checks += [(r[0], r[1]) for r in self.conn.execute("SELECT DISTINCT artifact_path,artifact_sha256 FROM trading_session_versions")]
            checks += [(r[0], r[1]) for r in self.conn.execute("SELECT manifest_path,manifest_sha256 FROM manifest_links")]
            for table in ("daily_price_versions", "corporate_action_versions", "symbol_assertions"):
                for r in self.conn.execute(f"SELECT DISTINCT evidence_json FROM {table} WHERE evidence_json!='{{}}'"):
                    proof = json.loads(r[0])
                    if proof.get("path") and proof.get("sha256"): checks.append((proof["path"],proof["sha256"]))
            for path, expected in sorted(set(checks)):
                f = Path(path)
                if not f.is_file():
                    failures.append({"reason": "missing_file", "path": path})
                elif sha256(f.read_bytes()) != expected:
                    failures.append({"reason": "file_hash_mismatch", "path": path})
        return {"status": "PASS" if not failures else "FAIL", "schema_version": SCHEMA_VERSION,
                "foreign_keys_enabled": bool(self.conn.execute("PRAGMA foreign_keys").fetchone()[0]),
                "counts": self.counts(), "failures": failures, "file_checks": files}
