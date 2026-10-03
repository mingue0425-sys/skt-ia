"""Observed public schemas. Yahoo chart has no verified official API contract."""
from abc import ABC, abstractmethod
import csv
from datetime import date, datetime, timedelta, timezone
import io
import json
import math
import os
from urllib.parse import urlencode, quote, urlsplit, parse_qs
from zoneinfo import ZoneInfo


class ProviderError(Exception):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


def decode_json(body):
    try:
        return json.loads(body)
    except (ValueError, UnicodeError):
        raise ProviderError("schema_change") from None


def number(v, *, integer=False):
    if v is None:
        return None
    if isinstance(v, bool):
        raise ProviderError("schema_change")
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise ProviderError("schema_change") from None
    if not math.isfinite(n) or (integer and not n.is_integer()):
        raise ProviderError("schema_change")
    return int(n) if integer else n


def blank():
    return {"bars": [], "actions": [], "directory": [], "documents": [], "metadata": {}}


def bar(spec, trading_date, values, semantics, currency="USD"):
    return {"schema_version": "1.0.0", "source": spec["source"],
            "local_instrument_id": "temporary:" + spec["source"] + ":" + spec["symbol"],
            "requested_symbol": spec["symbol"], "trading_date": trading_date,
            "open": number(values[0]), "high": number(values[1]),
            "low": number(values[2]), "close": number(values[3]),
            "volume": number(values[4], integer=True), "adjusted_close": None,
            "price_semantics": semantics, "volume_semantics": "provider_reported_unverified",
            "currency": currency, "publication_time_utc": None,
            "revision_time_utc": None, "session_scope": "vendor_daily_unverified",
            "observation_status": "missing_values" if any(v is None for v in values)
                else "reported_zero_volume" if values[4] == 0 else "observed"}


class Provider(ABC):
    @abstractmethod
    def request(self, spec):
        """Return private URL, spacing, sensitive values. Never persist URL."""

    @abstractmethod
    def normalize(self, spec, body):
        """Return explicit data families, preserving semantic differences."""


class Yahoo(Provider):
    def request(self, spec):
        start = datetime.combine(date.fromisoformat(spec["start"]), datetime.min.time(), timezone.utc)
        end = datetime.combine(date.fromisoformat(spec["end"]) + timedelta(days=1),
                               datetime.min.time(), timezone.utc)
        params = {"period1": int(start.timestamp()), "period2": int(end.timestamp()),
                  "interval": "1d", "events": "div,splits", "includePrePost": "false"}
        return ("https://query1.finance.yahoo.com/v8/finance/chart/" +
                quote(spec["symbol"], safe="") + "?" + urlencode(params), 2, ())

    def normalize(self, spec, body):
        j = decode_json(body)
        try:
            chart = j["chart"]
            if chart.get("error"):
                code = chart["error"].get("code", "")
                raise ProviderError("not_found" if code == "Not Found" else "provider_error")
            results = chart["result"]
            out = blank()
            if not results:
                return out
            result = results[0]
            meta = result["meta"]
            out["metadata"] = {k: meta.get(k) for k in (
                "symbol", "currency", "instrumentType", "exchangeTimezoneName", "firstTradeDate")}
            # meta is a current snapshot, including firstTradeDate as a vendor claim.
            timestamps = result.get("timestamp", [])
            if not timestamps:
                return out
            tz = ZoneInfo(meta["exchangeTimezoneName"])
            quotes = result["indicators"]["quote"][0]
            for k in ("open", "high", "low", "close", "volume"):
                if not isinstance(quotes[k], list) or len(quotes[k]) != len(timestamps):
                    raise ProviderError("schema_change")
            adj_groups = result["indicators"].get("adjclose")
            adjusted = adj_groups[0]["adjclose"] if adj_groups else [None] * len(timestamps)
            if len(adjusted) != len(timestamps):
                raise ProviderError("schema_change")
            for i, ts in enumerate(timestamps):
                if not isinstance(ts, int) or isinstance(ts, bool):
                    raise ProviderError("schema_change")
                d = datetime.fromtimestamp(ts, tz).date().isoformat()
                if not spec["start"] <= d <= spec["end"]:
                    continue
                b = bar(spec, d, [quotes[k][i] for k in ("open", "high", "low", "close", "volume")],
                        "vendor_split_adjusted_ohlc_not_raw", meta.get("currency"))
                b["adjusted_close"] = number(adjusted[i])
                b["source_timestamp_utc"] = datetime.fromtimestamp(ts, timezone.utc).isoformat()
                b["adjusted_close_semantics"] = "split_and_distribution_adjusted_current_vintage"
                out["bars"].append(b)
            for kind, events in result.get("events", {}).items():
                if kind not in ("splits", "dividends"):
                    continue
                for event in events.values():
                    d = datetime.fromtimestamp(event["date"], tz).date().isoformat()
                    a = {"source": "yahoo", "requested_symbol": spec["symbol"],
                         "local_instrument_id": "temporary:yahoo:" + spec["symbol"],
                         "event_date": d, "event_type": kind,
                         "publication_time_utc": None, "revision_time_utc": None,
                         "declaration_date": None, "record_date": None, "payment_date": None}
                    if kind == "splits":
                        a.update(numerator=number(event["numerator"]),
                                 denominator=number(event["denominator"]))
                    else:
                        a.update(amount=number(event["amount"]), currency=meta.get("currency"),
                                 amount_semantics="provider_reported_split_basis_unverified")
                    out["actions"].append(a)
            return out
        except (KeyError, IndexError, TypeError, ValueError, OverflowError):
            raise ProviderError("schema_change") from None


class AlphaVantage(Provider):
    def request(self, spec):
        key = os.environ.get("ALPHAVANTAGE_API_KEY") if spec.get("auth_mode") == "environment" else "demo"
        if not key:
            raise ProviderError("authentication_missing")
        params = {"function": "TIME_SERIES_DAILY", "symbol": spec["symbol"], "apikey": key}
        # Demo entitlement depends on the exact documented example URL.
        # The implicit default compact request was actually accessible while
        # explicitly outputsize=compact was rejected in this environment.
        if spec.get("outputsize") == "full":
            params["outputsize"] = "full"
        return "https://www.alphavantage.co/query?" + urlencode(params), 12, (() if key == "demo" else (key,))

    def normalize(self, spec, body):
        j = decode_json(body)
        if not isinstance(j, dict):
            raise ProviderError("schema_change")
        if "Error Message" in j:
            message = str(j["Error Message"]).lower()
            raise ProviderError("authentication_failure" if "apikey" in message or "api key" in message
                                else "provider_error")
        if "Note" in j or "Information" in j:
            message = str(j.get("Note", j.get("Information"))).lower()
            category = ("rate_limited" if any(x in message for x in ("rate limit", "call frequency", "25 requests", "higher api call"))
                        else "authentication_failure" if "invalid" in message and ("apikey" in message or "api key" in message)
                        else "entitlement_restricted")
            raise ProviderError(category)
        try:
            series = j["Time Series (Daily)"]
            out = blank()
            out["metadata"] = j["Meta Data"]
            for d, row in sorted(series.items()):
                date.fromisoformat(d)
                out["bars"].append(bar(spec, d, [row[k] for k in (
                    "1. open", "2. high", "3. low", "4. close", "5. volume")], "raw_as_traded"))
            return out
        except (KeyError, TypeError, ValueError):
            raise ProviderError("schema_change") from None


class EODHD(Provider):
    """Official EOD/demo endpoints; raw OHLC and adjusted close stay separate."""
    def request(self, spec):
        endpoint = {"daily": "eod", "dividends": "div", "splits": "splits", "identity": "fundamentals"}.get(spec["kind"])
        if not endpoint:
            raise ProviderError("unsupported_request")
        key = os.environ.get("EODHD_API_TOKEN") if spec.get("auth_mode") == "environment" else "demo"
        if not key:
            raise ProviderError("authentication_missing")
        params = {"api_token": key, "fmt": "json"}
        if spec["kind"] == "identity":
            params["filter"] = "General"
        else:
            params.update({"from": spec["start"], "to": spec["end"]})
        return ("https://eodhd.com/api/" + endpoint + "/" + quote(spec["symbol"] + ".US", safe="") + "?" + urlencode(params),
                2, () if key == "demo" else (key,))

    def normalize(self, spec, body):
        data = decode_json(body)
        out = blank()
        if spec["kind"] == "identity":
            if not isinstance(data, dict) or data.get("Code") != spec["symbol"]:
                raise ProviderError("schema_change")
            out["metadata"] = data
            out["documents"] = [{"document_id": "security_general", "source_fields": data,
                                  "note": "Current provider identity, not historical security master"}]
            return out
        if not isinstance(data, list):
            raise ProviderError("schema_change")
        try:
            for row in data:
                d = row["date"]
                date.fromisoformat(d)
                if not spec["start"] <= d <= spec["end"]:
                    raise ProviderError("response_outside_requested_period")
                if spec["kind"] == "daily":
                    b = bar(spec, d, [row[k] for k in ("open", "high", "low", "close", "volume")], "raw_as_traded")
                    b.update(adjusted_close=number(row["adjusted_close"]),
                             adjusted_close_semantics="split_and_distribution_adjusted_current_vintage",
                             volume_unit="shares", volume_adjustment_basis="split_adjusted_current_vintage",
                             session_scope="vendor_eod_indicative_not_official_auction")
                    out["bars"].append(b)
                else:
                    a = {"source": spec["source"], "requested_symbol": spec["symbol"],
                         "local_instrument_id": "temporary:" + spec["source"] + ":" + spec["symbol"],
                         "event_date": d, "event_type": spec["kind"],
                         "publication_time_utc": None, "revision_time_utc": None}
                    if spec["kind"] == "splits":
                        n, denominator = row["split"].split("/")
                        a.update(numerator=number(n), denominator=number(denominator), effective_date=d)
                        if a["numerator"] <= 0 or a["denominator"] <= 0:
                            raise ProviderError("schema_change")
                    else:
                        a.update(amount=number(row["unadjustedValue"]), currency=row["currency"],
                                 amount_semantics="as_declared_cash_per_share", ex_date=d,
                                 declaration_date=row.get("declarationDate"), record_date=row.get("recordDate"),
                                 payment_date=row.get("paymentDate"))
                    out["actions"].append(a)
            return out
        except (KeyError, TypeError, ValueError):
            raise ProviderError("schema_change") from None


class Stooq(Provider):
    def request(self, spec):
        params = {"s": spec["symbol"].lower() + ".us", "i": "d",
                  "d1": spec["start"].replace("-", ""), "d2": spec["end"].replace("-", "")}
        return "https://stooq.com/q/d/l/?" + urlencode(params), 2, ()

    def normalize(self, spec, body):
        if b"<html" in body.lower() or b"<script" in body.lower():
            raise ProviderError("access_challenge")
        if body.strip() in (b"No data", b"", b"No data."):
            return blank()
        try:
            reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
            if not {"Date", "Open", "High", "Low", "Close", "Volume"}.issubset(reader.fieldnames or []):
                raise ProviderError("schema_change")
            out = blank()
            for row in reader:
                date.fromisoformat(row["Date"])
                out["bars"].append(bar(spec, row["Date"], [row[k] for k in (
                    "Open", "High", "Low", "Close", "Volume")], "adjustment_definition_unverified"))
            return out
        except (UnicodeError, ValueError, KeyError):
            raise ProviderError("schema_change") from None


class NasdaqDirectory(Provider):
    def request(self, spec):
        kind = spec["kind"]
        if kind not in ("nasdaqlisted", "otherlisted"):
            raise ProviderError("unsupported_request")
        return "https://www.nasdaqtrader.com/dynamic/SymDir/" + kind + ".txt", 2, ()

    def normalize(self, spec, body):
        try:
            reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")), delimiter="|")
            required = {"Symbol", "Security Name", "ETF", "Test Issue"} if spec["kind"] == "nasdaqlisted" else {
                "ACT Symbol", "Security Name", "ETF", "Test Issue"}
            if not required.issubset(reader.fieldnames or []):
                raise ProviderError("schema_change")
            out = blank()
            for row in reader:
                first = row.get("Symbol", row.get("ACT Symbol", ""))
                if first.startswith("File Creation Time"):
                    out["metadata"]["file_creation_marker"] = first
                    continue
                if first:
                    out["directory"].append({"source": "nasdaq_directory", "source_fields": row,
                        "ticker": first, "history_semantics": "current_snapshot_only",
                        "common_stock_classification": "unverified", "listing_date": None,
                        "delisting_date": None, "permanent_security_id": None})
            return out
        except (UnicodeError, TypeError):
            raise ProviderError("schema_change") from None


class Document(Provider):
    def request(self, spec):
        url = spec["document_url"]
        parsed = urlsplit(url)
        if (not url.startswith("https://") or parsed.username or
            any(k != "id" for k in parse_qs(parsed.query))):
            raise ProviderError("unsafe_document_url")
        return url, 2, ()

    def normalize(self, spec, body):
        if not body.strip():
            return blank()
        out = blank()
        out["documents"] = [{"document_id": spec["kind"], "url": spec["document_url"],
                             "publication_time_utc": None, "content_verified": False,
                             "note": "HTTP receipt only; evidence content review is separate"}]
        return out


def get_provider(name):
    providers = {"yahoo": Yahoo, "alpha_vantage": AlphaVantage, "stooq": Stooq,
                 "nasdaq_directory": NasdaqDirectory, "official_document": Document, "eodhd": EODHD}
    if name not in providers:
        raise ProviderError("unsupported_source")
    return providers[name]()
