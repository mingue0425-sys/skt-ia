"""Generate explicitly fictional CLI inputs, separate from market-data roots."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from stage3_fixture_builder import market_story
from market_research.pit import Database
from market_research.storage import write_json


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--root',default='tests/generated/stage3'); args=parser.parse_args()
    root=Path(args.root).resolve(); root.mkdir(parents=True,exist_ok=True)
    with Database(root/'pit.sqlite',initialize=True) as db:
        _,config,_=market_story(db,root/'synthetic_artifacts')
        write_json(root/'config.json',config)
    print(json.dumps({'status':'PASS','input_domain':'synthetic','scope':'SYNTHETIC_TEST_ONLY','db':str(root/'pit.sqlite'),'config':str(root/'config.json')}))


if __name__=='__main__': main()
