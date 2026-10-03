"""A pinned calendar schedule, distinct from per-security trading state."""
from bisect import bisect_left
from datetime import datetime, timedelta
import json
from ..pit.time import timestamp, day


def plus_seconds(value, seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 <= seconds <= 86400:
        raise ValueError("invalid_processing_delay_seconds")
    return timestamp((datetime.fromisoformat(value.replace("Z", "+00:00")) + timedelta(seconds=seconds)).isoformat())


class SessionCalendar:
    def __init__(self, db, version, *, input_domain="market", exchange="XNYS"):
        if not version: raise ValueError("explicit_calendar_version_required")
        self.version = version
        self.rows = [dict(r) for r in db.conn.execute(
            "SELECT * FROM trading_session_versions WHERE calendar_version=? AND input_domain=? AND exchange=? ORDER BY trading_date",
            (version, input_domain, exchange))]
        if not self.rows: raise ValueError("calendar_version_not_found")
        self.dates = [r["trading_date"] for r in self.rows]
        self.index = {d: i for i, d in enumerate(self.dates)}
        self.by_date = {r["trading_date"]: r for r in self.rows}
        self.metadata = {"version": version, "exchange": exchange, "input_domain": input_domain,
                         "source": sorted({r["calendar_source"] for r in self.rows}),
                         "artifact_sha256": sorted({r["artifact_sha256"] for r in self.rows}),
                         "artifact_references": sorted([{"path":p,"sha256":h} for p,h in {(r["artifact_path"],r["artifact_sha256"]) for r in self.rows}],key=lambda v:v["path"]),
                         "historical_notice_verified": all(bool(json.loads(r["historical_evidence_json"])) for r in self.rows)}

    def window(self, anchor, n):
        day(anchor)
        if anchor not in self.index: raise ValueError("decision_date_not_a_session")
        i = self.index[anchor]
        return self.dates[max(0, i-n+1):i+1]

    def plan(self, decision_date, horizons=(1, 5, 20)):
        day(decision_date)
        if decision_date not in self.index: raise ValueError("decision_date_not_a_session")
        i = self.index[decision_date]; decision = plus_seconds(self.rows[i]["close_utc"], 3600)
        entry = self.rows[i+1] if i+1 < len(self.rows) else None
        exits = {h: self.rows[i+1+h] if i+1+h < len(self.rows) else None for h in horizons}
        return {"decision_at": decision, "intended_entry_at": entry["open_utc"] if entry else None,
                "entry_date": entry["trading_date"] if entry else None,
                "exits": exits, "calendar": self.metadata}

    def elapsed(self, last_date, anchor):
        return self.index[anchor] - bisect_left(self.dates, last_date)
