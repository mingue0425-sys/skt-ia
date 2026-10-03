"""Generate reference rules, never claim historical official announcements."""
import argparse
from market_research.storage import write_json
from market_research.validation.quality import calendar_rows

p=argparse.ArgumentParser()
p.add_argument("--start",default="2024-01-01")
p.add_argument("--end",default="2026-12-31")
p.add_argument("--output",default="data/reference_calendar.json")
a=p.parse_args()
rows=calendar_rows(a.start,a.end); write_json(a.output,rows)
print({"source":"exchange_calendars_derived_reference","rows":len(rows),"historical_notices_verified":False,"output":a.output})
