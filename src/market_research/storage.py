import hashlib
import json
import os
from pathlib import Path
import tempfile


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".partial-", delete=False) as f:
            name = f.name
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                                  allow_nan=False).encode() + b"\n")


def code_version(root):
    root = Path(root)
    files = sorted((root / "src").rglob("*.py")) + [root / "pyproject.toml"]
    return sha256(canonical({str(p.relative_to(root)): sha256(p.read_bytes()) for p in files}))


class Store:
    def __init__(self, root):
        self.root = Path(root)

    def records(self):
        return [json.loads(p.read_text()) for p in sorted((self.root / "manifest").glob("*.json"))]

    def cached(self, request_id):
        for record in reversed(sorted(self.records(), key=lambda x: x.get("recorded_at_utc", x["fetched_at_utc"]))):
            if record["request_id"] != request_id or record["status"] != "success":
                continue
            raw = self.root / record["raw_path"]
            normalized = self.root / record["normalized_path"]
            if (raw.is_file() and normalized.is_file() and sha256(raw.read_bytes()) == record["sha256"] and
                (not record.get("normalized_sha256") or sha256(normalized.read_bytes()) == record["normalized_sha256"])):
                return record
        return None

    def daily_budget(self, host):
        if host != "www.alphavantage.co":
            return True
        from .http import utc_now
        path = self.root / "rate_budget.json"
        ledger = json.loads(path.read_text()) if path.exists() else {}
        day = utc_now()[:10]
        key = host + ":" + day
        if ledger.get(key, 0) >= 25:
            return False
        ledger[key] = ledger.get(key, 0) + 1
        write_json(path, ledger)
        return True

    def save_record(self, record):
        name = record["request_id"] + "-" + sha256(canonical(record)) + ".json"
        write_json(self.root / "manifest" / name, record)
