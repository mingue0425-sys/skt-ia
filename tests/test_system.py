"""Offline failure-path tests. All values/identities here are SYNTHETIC TEST ONLY."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from market_research.collect import collect_one, collect_batch, http_category, public_spec
from market_research.http import Response, HTTPClient
from market_research.providers.public import Yahoo, AlphaVantage, Stooq, ProviderError
from market_research.storage import Store, atomic_write, sha256
from market_research.validation import validate_bars
from market_research.validation.quality import action_checks, calendar_rows

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / "tests/fixtures/yahoo_synthetic.json").read_bytes()
SPEC = {"source": "yahoo", "kind": "daily", "symbol": "TEST", "start": "2024-01-01", "end": "2024-01-05"}


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class SystemTests(unittest.TestCase):
    def collect(self, store, client, spec=SPEC, refresh=False):
        return collect_one(store, client, spec, project_root=ROOT, config_hash="synthetic-config",
                           refresh=refresh)

    def test_http_auth_not_empty(self):
        with tempfile.TemporaryDirectory() as t:
            store = Store(t)
            auth = self.collect(store, FakeClient([Response(401, b'{"error":"unauthorized"}')]))
            empty = self.collect(store, FakeClient([Response(200, b'{"chart":{"result":[],"error":null}}')]))
            self.assertEqual(auth["status"], "authentication_failure")
            self.assertIsNone(auth["manifest_record"]["empty_response"])
            self.assertEqual(empty["status"], "empty_response")
            self.assertTrue(empty["manifest_record"]["empty_response"])

    def test_alpha_json_auth_vs_rate_vs_entitlement(self):
        provider = AlphaVantage()
        for payload, expected in [({"Error Message":"Invalid API key"}, "authentication_failure"),
            ({"Information":"Our rate limit is 25 requests per day"}, "rate_limited"),
            ({"Information":"The demo API key is for demo purposes only"}, "entitlement_restricted")]:
            with self.subTest(expected=expected), self.assertRaises(ProviderError) as c:
                provider.normalize({"symbol":"TEST","source":"alpha_vantage"}, json.dumps(payload).encode())
            self.assertEqual(c.exception.category, expected)

    def test_missing_provider_column_detected(self):
        fixture = json.loads(FIXTURE)
        del fixture["chart"]["result"][0]["indicators"]["quote"][0]["high"]
        with self.assertRaises(ProviderError) as c:
            Yahoo().normalize(SPEC, json.dumps(fixture).encode())
        self.assertEqual(c.exception.category, "schema_change")

    def test_bad_provider_numeric_type(self):
        fixture = json.loads(FIXTURE)
        fixture["chart"]["result"][0]["indicators"]["quote"][0]["volume"][0] = "twelve"
        with self.assertRaises(ProviderError):
            Yahoo().normalize(SPEC, json.dumps(fixture).encode())

    def test_misaligned_provider_arrays(self):
        fixture = json.loads(FIXTURE)
        fixture["chart"]["result"][0]["indicators"]["adjclose"][0]["adjclose"] = [1]
        with self.assertRaises(ProviderError):
            Yahoo().normalize(SPEC, json.dumps(fixture).encode())

    def test_bad_normalized_type_missing_and_invalid_date(self):
        b = Yahoo().normalize(SPEC, FIXTURE)["bars"][0]
        b["volume"] = "1"
        del b["close"]
        codes = {i["code"] for i in validate_bars([b])}
        self.assertEqual(codes, {"missing_column", "wrong_type"})
        b = Yahoo().normalize(SPEC, FIXTURE)["bars"][0]
        b["trading_date"] = "2024-02-30"
        self.assertIn("invalid_date", {i["code"] for i in validate_bars([b])})

    def test_duplicate_and_conflict_both_detected(self):
        b = Yahoo().normalize(SPEC, FIXTURE)["bars"][0]
        conflict = copy.deepcopy(b); conflict["volume"] += 1
        codes = {i["code"] for i in validate_bars([b, copy.deepcopy(b), conflict])}
        self.assertTrue({"duplicate_key", "conflicting_duplicate"}.issubset(codes))

    def test_negative_volume_and_inconsistent_ohlc(self):
        b = Yahoo().normalize(SPEC, FIXTURE)["bars"][0]
        b.update(volume=-1, high=1)
        codes = {i["code"] for i in validate_bars([b])}
        self.assertTrue({"negative_volume", "inconsistent_ohlc"}.issubset(codes))

    def test_null_zero_and_missing_trading_are_distinct(self):
        b = Yahoo().normalize(SPEC, FIXTURE)["bars"][0]
        b.update(volume=0, close=0, low=0)
        codes = {i["code"] for i in validate_bars([b])}
        self.assertIn("reported_zero_volume_not_proof_of_no_trading", codes)
        self.assertIn("zero_price", codes)
        b.update(volume=None, close=None)
        codes = {i["code"] for i in validate_bars([b])}
        self.assertIn("missing_volume", codes)
        self.assertIn("missing_price", codes)
        self.assertNotIn("negative_volume", codes)

    def test_calendar_and_lifecycle_boundaries(self):
        b = Yahoo().normalize(SPEC, FIXTURE)["bars"][0]
        lifecycle = {b["local_instrument_id"]: {"first_trading_date":"2024-01-03", "last_trading_date":"2024-01-01"}}
        codes = {i["code"] for i in validate_bars([b], set(), lifecycle)}
        self.assertTrue({"observation_outside_reference_calendar", "pre_listing_observation", "post_delisting_observation"}.issubset(codes))

    def test_large_move_flagged_without_deletion(self):
        bars = Yahoo().normalize(SPEC, FIXTURE)["bars"]
        bars[1].update(open=200, high=202, low=199, close=201)
        self.assertIn("large_price_move_review_action_or_error", {i["code"] for i in validate_bars(bars)})
        self.assertEqual(len(bars), 2)

    def test_rerun_no_network_no_overwrite_no_duplicate(self):
        with tempfile.TemporaryDirectory() as t:
            store = Store(t); client = FakeClient([Response(200, FIXTURE)])
            first = self.collect(store, client)
            raw = Path(t) / first["manifest_record"]["raw_path"]
            before = raw.stat().st_mtime_ns
            second = self.collect(store, client)
            self.assertEqual(second["status"], "cached")
            self.assertEqual(client.calls, 1)
            self.assertEqual(raw.stat().st_mtime_ns, before)
            self.assertEqual(len(store.records()), 1)

    def test_revised_response_saved_as_new_version(self):
        changed = json.loads(FIXTURE)
        changed["chart"]["result"][0]["indicators"]["quote"][0]["volume"][0] += 10
        with tempfile.TemporaryDirectory() as t:
            store = Store(t)
            a = self.collect(store, FakeClient([Response(200, FIXTURE)]))
            b = self.collect(store, FakeClient([Response(200, json.dumps(changed).encode())]), refresh=True)
            self.assertNotEqual(a["manifest_record"]["raw_path"], b["manifest_record"]["raw_path"])
            self.assertEqual(len(list((Path(t)/"raw").rglob("*.bin"))), 2)
            self.assertEqual((Path(t)/a["manifest_record"]["raw_path"]).read_bytes(), FIXTURE)

    def test_identical_refresh_reuses_raw_but_records_receipt(self):
        with tempfile.TemporaryDirectory() as t:
            store = Store(t)
            for refresh in (False, True):
                self.collect(store, FakeClient([Response(200, FIXTURE)]), refresh=refresh)
            self.assertEqual(len(list((Path(t)/"raw").rglob("*.bin"))), 1)
            self.assertEqual(len(store.records()), 2)

    def test_partial_failure_safe_resume(self):
        specs = [SPEC, {**SPEC, "symbol":"TEST2"}]
        with tempfile.TemporaryDirectory() as t:
            first = collect_batch(specs, t, ROOT, b"synthetic", client=FakeClient([
                Response(200, FIXTURE), Response(503, b"unavailable")]))
            self.assertEqual([r["status"] for r in first], ["success", "http_error"])
            client = FakeClient([Response(200, FIXTURE)])
            second = collect_batch(specs, t, ROOT, b"synthetic", client=client)
            self.assertEqual([r["status"] for r in second], ["cached", "success"])
            self.assertEqual(client.calls, 1)

    def test_parse_failure_preserves_original_and_reason(self):
        with tempfile.TemporaryDirectory() as t:
            result = self.collect(Store(t), FakeClient([Response(200, b'{"new_schema":42}')]))
            self.assertEqual(result["status"], "schema_change")
            self.assertTrue((Path(t) / result["manifest_record"]["raw_path"]).is_file())

    def test_code_change_reprocesses_without_network(self):
        with tempfile.TemporaryDirectory() as t:
            store = Store(t)
            first = self.collect(store, FakeClient([Response(200, FIXTURE)]))
            raw_path = first["manifest_record"]["raw_path"]
            with patch("market_research.collect.code_version", return_value="synthetic-new-code"):
                client = FakeClient([])
                second = self.collect(store, client)
            self.assertEqual(second["status"], "reprocessed")
            self.assertEqual(client.calls, 0)
            self.assertEqual(second["manifest_record"]["raw_path"], raw_path)
            self.assertFalse(second["manifest_record"]["network_download_performed"])
            self.assertEqual(second["manifest_record"]["fetched_at_utc"], first["manifest_record"]["fetched_at_utc"])

    def test_normalized_cache_hash_checked(self):
        with tempfile.TemporaryDirectory() as t:
            store = Store(t)
            result = self.collect(store, FakeClient([Response(200, FIXTURE)]))
            record = result["manifest_record"]
            (Path(t)/record["normalized_path"]).write_text('{"tampered":true}')
            self.assertIsNone(store.cached(record["request_id"]))

    def test_gaps_do_not_become_daily_large_moves(self):
        bars = Yahoo().normalize(SPEC, FIXTURE)["bars"]
        bars[1].update(trading_date="2024-01-05", open=200, high=202, low=199, close=201)
        sessions = {"2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"}
        self.assertNotIn("large_price_move_review_action_or_error", {i["code"] for i in validate_bars(bars, sessions)})

    def test_atomic_interruption_never_exposes_complete_file(self):
        with tempfile.TemporaryDirectory() as t:
            target = Path(t)/"raw.bin"
            with patch("market_research.storage.os.replace", side_effect=OSError("synthetic crash")):
                with self.assertRaises(OSError):
                    atomic_write(target, FIXTURE)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(t).iterdir()), [])

    def test_corrupt_cache_redownloaded(self):
        with tempfile.TemporaryDirectory() as t:
            store = Store(t)
            r = self.collect(store, FakeClient([Response(200, FIXTURE)]))
            raw = Path(t)/r["manifest_record"]["raw_path"]
            raw.unlink()  # interrupted/lost storage, never trust metadata alone
            client = FakeClient([Response(200, FIXTURE)])
            self.assertEqual(self.collect(store, client)["status"], "success")
            self.assertEqual(client.calls, 1)

    def test_adjustment_semantics_never_claim_raw(self):
        out = Yahoo().normalize(SPEC, FIXTURE)
        self.assertEqual(out["bars"][0]["price_semantics"], "vendor_split_adjusted_ohlc_not_raw")
        self.assertEqual(out["bars"][0]["adjusted_close_semantics"], "split_and_distribution_adjusted_current_vintage")
        check = action_checks(out["bars"], out["actions"])[0]
        self.assertEqual(check["raw_adjusted_reconciliation"], "skipped_no_independent_raw_split_window")
        self.assertIsNone(out["bars"][0]["publication_time_utc"])

    def test_secret_allowlist_and_private_request(self):
        spec = {"source":"alpha_vantage","kind":"daily","symbol":"TEST","auth_mode":"environment", "apikey":"TEST-SECRET"}
        with patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY":"TEST-SECRET"}):
            url, _, secrets = AlphaVantage().request(spec)
            self.assertIn("TEST-SECRET", url)
            self.assertEqual(secrets, ("TEST-SECRET",))
        self.assertNotIn("TEST-SECRET", json.dumps(public_spec(spec)))
        with tempfile.TemporaryDirectory() as t, patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY":"TEST-SECRET"}):
            collect_batch([spec], t, ROOT, b"synthetic", client=FakeClient([Response(401, b"unauthorized")]))
            self.assertFalse(any(b"TEST-SECRET" in p.read_bytes() for p in Path(t).rglob("*") if p.is_file()))

    def test_unsafe_document_url_secret_not_persisted(self):
        spec = {"source":"official_document","kind":"test","document_url":"https://example.test/doc?token=TEST-SECRET"}
        with tempfile.TemporaryDirectory() as t:
            results = collect_batch([spec], t, ROOT, b"synthetic", client=FakeClient([]))
            self.assertEqual(results[0]["status"], "unsafe_document_url")
            self.assertFalse(any(b"TEST-SECRET" in p.read_bytes() for p in Path(t).rglob("*") if p.is_file()))

    def test_http_credential_echo_withheld(self):
        class FakeResponse:
            status = 401; headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b"Echo TEST-SECRET"
        r = HTTPClient(opener=lambda *a, **k: FakeResponse()).get("https://example.test", secrets=("TEST-SECRET",))
        self.assertEqual(r.error, "secret_echo_raw_withheld")
        self.assertEqual(r.body, b"")

    def test_timeout_bounded_retries_backoff(self):
        calls, sleeps = [], []
        def fail(*args, **kwargs):
            calls.append(kwargs["timeout"])
            raise TimeoutError("https://example.test?token=TEST-SECRET")
        r = HTTPClient(opener=fail, sleep=sleeps.append, monotonic=lambda: 100).get("https://example.test", spacing=0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [1, 2])
        self.assertEqual(r.error, "network_error")
        self.assertNotIn("TEST-SECRET", repr(r))

    def test_retry_after_long_delay_no_early_retry(self):
        def fail(req, **kwargs):
            raise urllib.error.HTTPError(req.full_url, 429, "rate", {"Retry-After":"120"}, io.BytesIO(b"rate"))
        sleeps = []
        r = HTTPClient(opener=fail, sleep=sleeps.append).get("https://example.test")
        self.assertEqual(len(r.attempts), 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(http_category(r), "rate_limited")

    def test_http_401_not_retried(self):
        calls = []
        def fail(req, **kwargs):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 401, "auth", {}, io.BytesIO(b"auth"))
        r = HTTPClient(opener=fail).get("https://example.test")
        self.assertEqual(len(calls), 1)
        self.assertEqual(http_category(r), "authentication_failure")

    def test_stooq_html_is_access_failure_not_empty(self):
        with self.assertRaises(ProviderError) as c:
            Stooq().normalize(SPEC, b"<html><script>verify</script></html>")
        self.assertEqual(c.exception.category, "access_challenge")

    def test_persisted_daily_budget(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertTrue(all(Store(t).daily_budget("www.alphavantage.co") for _ in range(25)))
            self.assertFalse(Store(t).daily_budget("www.alphavantage.co"))

    def test_calendar_dst_early_close_and_extraordinary_closure(self):
        rows = {r["trading_date"]: r for r in calendar_rows("2024-01-01", "2025-12-31")}
        self.assertEqual(rows["2024-03-08"]["close_utc"][11:16], "21:00")
        self.assertEqual(rows["2024-03-11"]["close_utc"][11:16], "20:00")
        self.assertEqual(rows["2024-11-29"]["close_utc"][11:16], "18:00")
        self.assertNotIn("2025-01-09", rows)  # Carter national day of mourning


if __name__ == "__main__":
    unittest.main()
