"""UTC comparison without inventing timestamps for date-only evidence."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

POLICY_VERSION = "2.1.0"
ASSUMPTION_ID = "date_end_plus_delay"


def timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("timestamp_required")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.utcoffset() is None:
        raise ValueError("timezone_required")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("date_required")
    return value


def assumed_available(publication_date, rule):
    if rule.get("id") != ASSUMPTION_ID or rule.get("version") != "1":
        raise ValueError("unsupported_assumption_rule")
    hours = rule.get("delay_hours")
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 <= hours <= 168:
        raise ValueError("invalid_delay_hours")
    tz = ZoneInfo(rule["timezone"])
    # Start of the following LOCAL date, then an elapsed UTC delay. DST is explicit.
    next_day = date.fromisoformat(day(publication_date)) + timedelta(days=1)
    boundary = datetime.combine(next_day, time(), tz).astimezone(timezone.utc)
    return timestamp((boundary + timedelta(hours=hours)).isoformat())
