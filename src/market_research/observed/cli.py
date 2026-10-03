"""Observed diagnostics and one-shot operation commands."""
import json
import sqlite3
from pathlib import Path
from ..http import utc_now
from ..storage import write_json
from ..pit import Database
from ..datasets.store import DatasetStore
from .diagnostics import diagnose
from .operation import cycle, check_config, MINIMUM

COMMANDS=('data-blockers','observed-config-check','observed-run')


def run(args):
    try:
        if args.command=='data-blockers':
            with Database(args.db) as pit, DatasetStore(args.dataset_db) as store:
                result=diagnose(pit,store,args.training_asof or utc_now())
        else:
            config=json.loads(Path(args.config).read_text())
            if args.command=='observed-config-check':
                result={'status':'OK','permissions':check_config(config,utc_now()),'minimum_training':MINIMUM}
            else: result=cycle(config,args.data_dir,args.project_root,refresh=args.refresh)
        if args.output:write_json(args.output,result)
        if args.command=='data-blockers':
            print(json.dumps({k:v for k,v in result.items() if k not in ('rows','snapshot_results','pending_versions','category_overlap_counts')},ensure_ascii=False))
        else:
            print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 2 if result.get('status')=='COLLECTION_FAILED' else 3 if result.get('status') in ('MISSED_DECISION','NO_ELIGIBLE_INPUTS','REQUIRED_FIELDS_MISSING','STALE_DATA','FEATURE_CONTRACT_UNMET') else 0
    except (ValueError,KeyError,TypeError,OSError,sqlite3.Error) as exc:
        result={'status':'ERROR','category':str(exc) if isinstance(exc,ValueError) else type(exc).__name__}
        if args.output:write_json(args.output,result)
        print(json.dumps(result));return 1
