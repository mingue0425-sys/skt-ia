"""Offline audit of stored artifacts and a cached CLI replay with network disabled."""
import contextlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import sys
from unittest.mock import patch

from market_research.cli import main
from market_research.storage import Store, sha256, write_json, code_version
from market_research.http import utc_now

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
store = Store(ROOT / "data")
failures = []
records = store.records()
for r in records:
    if r.get("raw_path"):
        p = store.root / r["raw_path"]
        if not p.is_file() or sha256(p.read_bytes()) != r["sha256"]:
            failures.append({"check": "raw_sha256", "request_id": r["request_id"]})
    if r.get("normalized_sha256"):
        p = store.root / r["normalized_path"]
        if not p.is_file() or sha256(p.read_bytes()) != r["normalized_sha256"]:
            failures.append({"check": "normalized_sha256", "request_id": r["request_id"]})
partial = list(store.root.rglob(".partial-*"))
if partial:
    failures.append({"check": "partial_files_present", "count": len(partial)})
secrets = [v.encode() for k, v in os.environ.items() if k in {"ALPHAVANTAGE_API_KEY", "TIINGO_API_KEY", "MASSIVE_API_KEY", "NASDAQ_DATA_LINK_API_KEY"} and v]
secrets.append(b"TEST-SECRET")
scanned = 0
for folder in (ROOT / "data", ROOT / "reports"):
    for p in folder.rglob("*"):
        if p.is_file():
            scanned += 1
            if any(s in p.read_bytes() for s in secrets):
                failures.append({"check": "credential_leak", "path": str(p.relative_to(ROOT))})
before = {str(p): (sha256(p.read_bytes()), p.stat().st_mtime_ns) for p in store.root.rglob("*.bin")}
manifest_before = len(store.records())
output = io.StringIO()
with patch("market_research.http.HTTPClient.get", side_effect=RuntimeError("offline audit: network forbidden")), contextlib.redirect_stdout(output):
    exit_code = main(["smoke", "--output", "reports/execution-cached-replay.json"])
after = {str(p): (sha256(p.read_bytes()), p.stat().st_mtime_ns) for p in store.root.rglob("*.bin")}
if exit_code or before != after or len(store.records()) != manifest_before:
    failures.append({"check": "cached_replay_mutated_original_or_called_network", "exit_code": exit_code})
dependencies = {k: importlib.metadata.version(k) for k in ["exchange_calendars", "numpy", "pandas", "tzdata"]}
report = {"generated_at_utc": utc_now(), "command": ".venv/bin/python scripts/audit_artifacts.py",
          "status": "PASS" if not failures else "FAIL", "manifest_records": len(records),
          "raw_files": len(before), "raw_bytes": sum(Path(p).stat().st_size for p in before),
          "partial_files": len(partial), "files_scanned_for_known_secrets": scanned,
          "secret_audit_scope": "named environment keys and synthetic canary; no real personal key supplied during collection",
          "cached_replay": output.getvalue(), "cached_replay_no_network": exit_code == 0,
          "failures": failures, "code_sha256": code_version(ROOT), "dependencies": dependencies,
          "environment": {"python": sys.version, "platform": platform.platform(), "machine": platform.machine()}}
write_json(ROOT / "reports/artifact_audit.json", report)
print(json.dumps({k: v for k, v in report.items() if k not in {"cached_replay", "dependencies", "environment"}}, ensure_ascii=False))
sys.exit(0 if not failures else 1)
