"""Bounded, sequential HTTP with status-only diagnostics and no URL logging."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Response:
    status: int | None
    body: bytes
    fetched_at_utc: str = field(default_factory=utc_now)
    attempts: list = field(default_factory=list)
    error: str | None = None


class HTTPClient:
    def __init__(self, timeout=20, max_attempts=3, opener=None, sleep=time.sleep,
                 monotonic=time.monotonic, budget=None):
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.opener = opener or urllib.request.urlopen
        self.sleep, self.clock = sleep, monotonic
        self.last = {}
        self.budget = budget

    def get(self, url, *, spacing=2, secrets=(), headers=None):
        host = urlsplit(url).hostname
        attempts = []
        for number in range(self.max_attempts):
            wait = max(0, spacing - (self.clock() - self.last.get(host, -1e12)))
            if wait:
                self.sleep(wait)
            if self.budget and not self.budget(host):
                return Response(None, b"", attempts=attempts, error="daily_budget_exhausted")
            self.last[host] = self.clock()
            response_headers = {}
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "market-research-stage1/0.1 (small feasibility sample)",
                    **(headers or {})})
                with self.opener(req, timeout=self.timeout) as r:
                    status, body = r.status, r.read()
                    response_headers = r.headers
            except urllib.error.HTTPError as e:
                status, body, response_headers = e.code, e.read(), e.headers or {}
            except (urllib.error.URLError, TimeoutError, OSError):
                status, body = None, b""
            # Never store a response echoing a credential. This is an explicit
            # original-preservation exception; record why no raw file was saved.
            if any(s and s.encode() in body for s in secrets):
                return Response(status, b"", attempts=attempts + [{"status": status}],
                                error="secret_echo_raw_withheld")
            attempts.append({"attempt": number + 1, "status": status})
            result = Response(status, body, attempts=attempts,
                              error="network_error" if status is None else None)
            if status not in (None, 429, 500, 502, 503, 504) or number + 1 == self.max_attempts:
                return result
            backoff = 2 ** number
            retry_after = response_headers.get("Retry-After")
            if retry_after:
                try:
                    backoff = max(backoff, float(retry_after))
                except ValueError:
                    try:
                        backoff = max(backoff, (parsedate_to_datetime(retry_after) -
                            datetime.now(timezone.utc)).total_seconds())
                    except (ValueError, TypeError):
                        pass
            if backoff > 30:
                # Do not retry early or block for an unbounded duration.
                result.error = "retry_after_exceeds_local_wait_limit"
                return result
            self.sleep(max(0, backoff))
        raise AssertionError("unreachable")
