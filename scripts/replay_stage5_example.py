"""Exercise saved-model CLI -> Stage4 evaluation -> existing simulation engine."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from market_research.storage import write_json
from market_research.stage4.runs import read_run

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',default='tests/generated/stage5/signal')
    parser.add_argument('--task',choices=('regression','classification'),default='regression')
    args=parser.parse_args();root=ROOT/args.root
    result=json.loads((root/(args.task+'-release')/'result.json').read_text())
    wanted='ridge' if args.task=='regression' else 'logistic'
    model=next(m for m in result['folds'][0]['models'] if m['model_name']==wanted)
    artifact=model['artifact'];c=result['config'];out=root/'standalone'/args.task
    out.mkdir(parents=True,exist_ok=True)
    old=read_run(model['stage4']['path'])
    write_json(out/'simulation-config.json',old['manifest']['config'])
    common=['--db',str(root/'pit.sqlite'),'--dataset-db',str(root/'datasets.sqlite'),'--mode',c['mode']]
    predictions=out/'predictions.json'
    commands=[['model-predict',*common,'--model-file',artifact['path'],'--model-root',str(Path(artifact['path']).parent),'--model-id',artifact['model_id'],
               '--dataset-id',c['dataset_snapshot_id'],'--dataset-hash',c['dataset_content_sha256'],'--partition','test','--output',str(predictions)],
              ['prediction-evaluate',*common,'--dataset-id',c['dataset_snapshot_id'],'--dataset-hash',c['dataset_content_sha256'],
               '--predictions',str(predictions),'--evaluation-asof',c['evaluation_asof'],'--horizon',str(c['horizon']),'--output',str(out/'evaluation.json')],
              ['simulation-run',*common,'--config',str(out/'simulation-config.json'),'--predictions',str(predictions),
               '--run-root',str(out/'runs'),'--output',str(out/'run.json')]]
    evidence=[]
    for command in commands:
        p=subprocess.run([sys.executable,'-m','market_research.cli',*command],cwd=ROOT,text=True,capture_output=True)
        (out/(command[0]+'.log')).write_text(p.stdout+'\n'+p.stderr)
        if p.returncode!=0:raise RuntimeError(command[0]+': '+p.stdout[-800:]+' '+p.stderr[-800:])
        evidence.append({'command':[sys.executable,'-m','market_research.cli',*command],'exit':p.returncode})
    new=read_run(json.loads((out/'run.json').read_text())['path'])
    evaluated=json.loads((out/'evaluation.json').read_text())
    for field in ('count', 'metrics_row_weighted', 'calibration'):
        if evaluated[field]!=old['result']['metrics'][field]:raise AssertionError('standalone_evaluation_numerical_result_changed')
    if new['result']['metrics']!=old['result']['metrics'] or new['result']['simulation']!=old['result']['simulation']:
        raise AssertionError('saved_model_cli_numerical_stage4_results_changed')
    write_json(out/'verification.json',{'status':'PASS','task':args.task,'model_id':artifact['model_id'],
                                       'stage4_metrics_and_simulation_equal':True,'commands':evidence})
    print(json.dumps({'status':'PASS','task':args.task,'output':str(out/'verification.json')}))


if __name__=='__main__':main()
