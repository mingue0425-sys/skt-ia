"""Create two independent fixed synthetic roots; preserve existing inputs."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tests'))
from market_research.pit import Database
from market_research.datasets.store import DatasetStore
from market_research.storage import write_json
from stage5_fixture_builder import story


def main():
    p = argparse.ArgumentParser(); p.add_argument('--root', default='tests/generated/stage5'); args = p.parse_args()
    for name in ('signal', 'null'):
        root = ROOT/args.root/name
        if (root/'generator.json').exists(): print('Preserved '+str(root)); continue
        if (root/'pit.sqlite').exists(): raise ValueError('incomplete_existing_root_choose_new_root')
        with Database(root/'pit.sqlite', initialize=True) as pit, DatasetStore(root/'datasets.sqlite') as store:
            base, generator = story(pit, store, root/'source', signal=name == 'signal')
        for task, models in (('regression', ['zero', 'mean', 'ridge', 'tree']), ('classification', ['frequency', 'logistic', 'tree'])):
            write_json(root/(task+'.json'), base | {'task': task, 'models': models})
        write_json(root/'generator.json', generator)
        print(json.dumps({'synthetic': name, 'dataset': base['dataset_snapshot_id'], 'seed': generator['seed']}))


if __name__ == '__main__': main()
