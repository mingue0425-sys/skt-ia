"""Generate a small offline CLI demo outside market-data storage."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tests"))
from fixture_builder import append, RULE
from market_research.storage import write_json

p=argparse.ArgumentParser()
p.add_argument("--output",default="tests/generated/stage2")
a=p.parse_args(); root=Path(a.output)
append(root,label="initial")
append(root,label="correction",close=110,published="2024-01-02T21:05:00Z",revised="2024-01-05T21:05:00Z",
       received="2024-01-05T21:06:00Z",usable="2024-01-05T21:07:00Z")
append(root,label="date-only",symbol="DATED",published=None,publication_date="2024-01-03",proof=False)
write_json(root/"assumption_rule.json",RULE)
print("SYNTHETIC_TEST_ONLY: generated 3 manifests; no market downloads.")
