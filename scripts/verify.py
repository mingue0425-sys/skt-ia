"""Reproducible offline suite with structured evidence, never network smoke."""
import io
import json
from pathlib import Path
import platform
import sys
import time
import unittest
from market_research.http import utc_now
from market_research.storage import write_json, code_version

ROOT = Path(__file__).resolve().parents[1]
started = utc_now()
clock = time.perf_counter()
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
capture = io.StringIO()
result = unittest.TextTestRunner(stream=capture, verbosity=2).run(suite)
print(capture.getvalue(), end="")
report = {"test_kind": "offline_synthetic_fixtures_only", "command": ".venv/bin/python scripts/verify.py",
          "started_at_utc": started, "finished_at_utc": utc_now(),
          "elapsed_seconds": time.perf_counter()-clock,
          "environment": {"python": sys.version, "platform": platform.platform(), "machine": platform.machine()},
          "code_sha256": code_version(ROOT), "tests_run": result.testsRun,
          "passed": result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
          "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
          "output": capture.getvalue(), "status": "PASS" if result.wasSuccessful() else "FAIL"}
write_json(ROOT / "reports/unit_test_results.json", report)
sys.exit(0 if result.wasSuccessful() else 1)
