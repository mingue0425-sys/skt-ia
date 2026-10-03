"""Separate offline Stage4 fixtures; never replace Stage2 or Stage3 data."""
import sys
import argparse
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"tests"))
from market_research.pit import Database
from market_research.datasets.store import DatasetStore
from market_research.storage import write_json
from market_research.http import utc_now
from stage4_fixture_builder import story


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="tests/generated/stage4")
    args = parser.parse_args()
    root = ROOT/args.root
    if (root/"config.json").exists():
        print(f"Existing fixed Stage4 inputs preserved: {root}/config.json")
        return
    root.mkdir(parents=True, exist_ok=True)
    with Database(root/"pit.sqlite", initialize=True) as pit, DatasetStore(root/"datasets.sqlite") as store:
        config, predictions = story(pit, store, root/"source", utc_now())
    write_json(root/"config.json", config)
    write_json(root/"split.json", config["split"])
    write_json(root/"predictions.json", predictions)
    write_json(root/"input.json", {"dataset_snapshot_id": config["dataset_snapshot_id"], "dataset_content_sha256": config["dataset_content_sha256"]})
    print(f"SYNTHETIC_TEST_ONLY fixed dataset, split and external predictions written to {root}")


if __name__ == "__main__": main()
