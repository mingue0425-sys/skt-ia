"""Flat feature/label/dataset commands. Outputs containing market rows stay local."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from ..pit import Database
from ..storage import write_json
from .builder import build_dataset, generate_feature, generate_labels
from .calendar import SessionCalendar
from .contracts import feature_contract, label_contract, DATASET_VERSION
from .store import DatasetStore, replay_dataset, training_selection, iter_dataset_rows

COMMANDS=("feature-contract","label-contract","dataset-contract","feature-build","label-build","label-update",
          "dataset-build","dataset-verify","dataset-replay","training-check","stage3-report")


def options(parser):
    parser.add_argument("--dataset-db",default="data/stage3/datasets.sqlite")
    parser.add_argument("--data-scope", choices=("strict_history","fixed_security_research","forward_observed"), default="strict_history")
    parser.add_argument("--dataset-id")
    parser.add_argument("--training-asof")
    parser.add_argument("--allow-research",action="store_true",help="Diagnostic selection only; never grants learning rights")
    parser.add_argument("--batch-size",type=int,default=1)
    parser.add_argument("--jsonl",action="store_true",help="Atomic streaming export for dataset-replay --output")


def export_rows(path,rows):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix=".partial-",mode="w",delete=False) as f:
            temporary=f.name
            for row in rows: f.write(json.dumps(row,ensure_ascii=False,sort_keys=True,allow_nan=False)+"\n")
            f.flush(); os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:
        if temporary and os.path.exists(temporary): os.unlink(temporary)


def run(args):
    try:
        if args.command in ("feature-contract","label-contract","dataset-contract"):
            result=feature_contract() if args.command=="feature-contract" else label_contract() if args.command=="label-contract" else {
                "version":DATASET_VERSION,"feature":feature_contract(),"label":label_contract(),
                "membership":"immutable feature/label IDs; separate SQLite migration 1; original PIT migration 1:2 unchanged",
                "training":"explicit training_asof and rights/PIT checks; no training performed"}
        else:
            with Database(args.db) as db, DatasetStore(args.dataset_db) as store:
                if args.command in ("dataset-verify","dataset-replay","training-check","stage3-report"):
                    if not args.dataset_id: raise ValueError("dataset_id_required")
                    result=replay_dataset(store,db,args.dataset_id)
                    if args.command=="training-check":
                        if not args.training_asof: raise ValueError("training_asof_required")
                        result=training_selection(store,db,args.dataset_id,training_asof=args.training_asof,allow_research=args.allow_research,scope=args.data_scope)
                    elif args.command=="dataset-replay":
                        if not args.output or not args.jsonl: raise ValueError("dataset_replay_requires_output_jsonl_for_bounded_export")
                        export_rows(args.output,iter_dataset_rows(store,db,args.dataset_id))
                    elif args.command=="stage3-report":
                        result={"status":result["status"],"snapshot_id":args.dataset_id,"items":result["items"],"dataset_audit":store.audit(),
                                "feature_status_counts":result["metadata"]["feature_status_counts"],"label_status_counts":result["metadata"]["label_status_counts"],
                                "reason_counts":result["metadata"]["reason_counts"],"scope":"dataset aggregate validation; not an independent test run or profitability assessment"}
                else:
                    config=json.loads(Path(args.config).read_text())
                    if args.command=="dataset-build": result=build_dataset(db,store,config,batch_size=args.batch_size)
                    else:
                        calendar=SessionCalendar(db,config["calendar_version"],input_domain=config.get("input_domain","market")); rows=[]
                        for sample in config["samples"]:
                            if args.command=="feature-build":
                                feature=generate_feature(db,calendar,sample,config)
                                rows.append({"version_id":store.add_feature(feature),"feature":feature})
                            else:
                                # Compute sample ID from the immutable sample contract, not from feature prices.
                                from ..storage import canonical,sha256
                                plan=calendar.plan(sample["decision_date"])
                                sid=sha256(canonical({"series":config["series"],"decision_at":plan["decision_at"],"calendar_version":calendar.version,"input_domain":config.get("input_domain","market")}))
                                for label in generate_labels(db,calendar,sample,config):
                                    label.update(sample_id=sid,learning_ready=False,learning_rights_status="unconfirmed_standalone_label")
                                    rows.append({"version_id":store.add_label(label),"label":label})
                        ready=sum((r.get("feature",{}).get("sample_status")=="ready" or r.get("label",{}).get("status")=="ready") for r in rows)
                        result={"status":"OK","rows":rows,"row_count":len(rows),"selected_count":ready}
        if args.output and args.command!="dataset-replay": write_json(args.output,result)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True,allow_nan=False))
        empty=args.require_data and (result.get("selected_count",result.get("items",result.get("row_count",1)))==0)
        return 1 if empty or result.get("status") in ("FAIL","ERROR") else 0
    except (ValueError,KeyError,TypeError,OSError,sqlite3.Error) as exc:
        result={"status":"ERROR","reason":str(exc) if isinstance(exc,ValueError) else "input_file_schema_or_database_error"}
        if args.output and not args.jsonl: write_json(args.output,result)
        print(json.dumps(result,ensure_ascii=False)); return 1
