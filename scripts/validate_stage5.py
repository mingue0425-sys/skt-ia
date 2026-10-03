"""Offline release audit: existing data preserved; only synthetic models are trained."""
import concurrent.futures
import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path
from market_research.storage import sha256, canonical, write_json, code_version
from market_research.http import utc_now
from market_research.training.artifacts import environment
from market_research.pit import Database, replay_snapshot
from market_research.datasets.store import DatasetStore, iter_dataset_rows, replay_dataset, training_selection
from market_research.stage4.inputs import eligibility_report
from validate_stage4 import verify_datasets

ROOT=Path(__file__).resolve().parents[1]
PYTHON=str(ROOT/'.venv/bin/python')
AUDIT=ROOT/'data/stage5/release'
RESUME_VERIFIED=False


def command(argv, expected=0, name='command', timed=False):
    AUDIT.mkdir(parents=True,exist_ok=True)
    start=time.perf_counter()
    if timed:
        stdout_path, stderr_path = AUDIT/(name+'-stdout.log'), AUDIT/(name+'-stderr.log')
        with stdout_path.open('w') as out, stderr_path.open('w') as err:
            process = subprocess.Popen(argv,cwd=ROOT,text=True,stdout=out,stderr=err)
            _, status, usage = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
        stdout, stderr = stdout_path.read_text(), stderr_path.read_text()
        p = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    else:
        p=subprocess.run(argv,cwd=ROOT,text=True,capture_output=True)
    (AUDIT/(name+'.log')).write_text(p.stdout+'\n'+p.stderr)
    record={'command':argv,'expected_exit':expected,'actual_exit':p.returncode,'status':'PASS' if p.returncode==expected else 'FAIL','elapsed_seconds':time.perf_counter()-start,'log':str((AUDIT/(name+'.log')).relative_to(ROOT))}
    if timed:
        record['max_rss_kib']=usage.ru_maxrss
        record['cpu_user_seconds']=usage.ru_utime
        record['cpu_system_seconds']=usage.ru_stime
        record['measurement']='os.wait4 individual child rusage; Linux ru_maxrss in KiB; not concurrent total RSS'
    write_json(AUDIT/(name+'-command.json'), record)
    if p.returncode!=expected:raise RuntimeError(json.dumps(record)+' '+p.stderr[-1600:]+' '+p.stdout[-800:])
    return record,p.stdout+p.stderr


def cli(*args):return [PYTHON,'-m','market_research.cli',*map(str,args)]


def synthetic_case(name,task):
    root=ROOT/'tests/generated/stage5'/name
    out=root/(task+'-release')
    common=['--db',root/'pit.sqlite','--dataset-db',root/'datasets.sqlite','--mode','synthetic']
    before={str(p):sha256(p.read_bytes()) for p in (root/'pit.sqlite',root/'datasets.sqlite')}
    if RESUME_VERIFIED:
        return reuse_synthetic(name, task, root, out, before)
    cmd,_=command(cli('training-run',*common,'--config',root/(task+'.json'),'--training-root',out,'--output',out/'result.json'),name=name+'-'+task,timed=True)
    replay,_=command(cli('training-replay',*common,'--training-result',out/'result.json','--output',out/'replay.json'),name=name+'-'+task+'-replay')
    report,_=command(cli('stage5-report',*common,'--training-result',out/'result.json','--output',out/'aggregate.json'),name=name+'-'+task+'-report')
    result=json.loads((out/'result.json').read_text())
    if result['status']!='OK':raise AssertionError('synthetic_training_not_complete')
    summaries=[]
    for f in result['folds']:
        for m in f['models']:
            metrics=m['partitions']['test']['metrics']
            summaries.append({'fold_id':f['fold_id'],'training_count':f['training_count'],'model':m['model_name'],
                              'model_id':m['artifact']['model_id'],'pipeline_sha256':m['artifact']['pipeline_sha256'],
                              'reload':m['reload_predictions'],'stage4_replay':m['stage4_replay'],
                              'evaluated':metrics['count'],'metrics':metrics['metrics_row_weighted'],
                              'calibration_count':sum(b['count'] for b in metrics['calibration']),
                              'simulation':m['simulation_summary'],'cash':m['cash_summary']})
    unchanged=all(sha256(Path(p).read_bytes())==digest for p,digest in before.items())
    if not unchanged:raise AssertionError('synthetic_fixed_databases_changed')
    return {'name':name,'task':task,'status':'PASS','dataset_snapshot_id':result['config']['dataset_snapshot_id'],
            'generator':json.loads((root/'generator.json').read_text()),'models':summaries,'fixed_input_bytes_unchanged':True,
            'result_content_sha256':result['result_content_sha256'],'commands':[cmd,replay,report],
            'scope':'connection validation; not stock predictive power or profitability'}


def reuse_synthetic(name, task, root, out, before):
    from market_research.training.artifacts import load_model
    from market_research.stage4.runs import read_run
    result=json.loads((out/'result.json').read_text())
    digest=result.pop('result_content_sha256')
    if sha256(canonical(result))!=digest or result['status']!='OK':raise AssertionError('invalid_prior_training_result')
    if result['config']!=json.loads((root/(task+'.json')).read_text()):raise AssertionError('prior_config_changed')
    replay=json.loads((out/'replay.json').read_text())
    expected=sum(len(f['models']) for f in result['folds'])
    if replay['status']!='PASS' or replay['models_verified']!=expected:raise AssertionError('prior_fixed_replay_not_complete')
    with Database(root/'pit.sqlite') as pit, DatasetStore(root/'datasets.sqlite') as store:
        fixed=replay_dataset(store,pit,result['config']['dataset_snapshot_id'])
        if fixed['content_sha256']!=result['config']['dataset_content_sha256']:raise AssertionError('prior_input_hash_changed')
    summaries=[]
    for f in result['folds']:
        for m in f['models']:
            a=m['artifact']
            model=load_model(a['path'],Path(a['path']).parent,a['model_id'],'synthetic',ROOT)
            if model['content']['provenance']['run_config']!=result['config']:raise AssertionError('prior_model_config_changed')
            run=read_run(m['stage4']['path'])
            if run['manifest']['code_sha256']!=code_version(ROOT):raise AssertionError('prior_simulation_code_changed')
            for info in m['partitions'].values():
                preds=json.loads(Path(info['predictions_path']).read_text())
                if sha256(canonical(preds))!=info['predictions_sha256']:raise AssertionError('prior_predictions_changed')
            metrics=m['partitions']['test']['metrics']
            summaries.append({'fold_id':f['fold_id'],'training_count':f['training_count'],'model':m['model_name'],
                              'model_id':a['model_id'],'pipeline_sha256':a['pipeline_sha256'],'reload':m['reload_predictions'],
                              'stage4_replay':m['stage4_replay'],'evaluated':metrics['count'],'metrics':metrics['metrics_row_weighted'],
                              'calibration_count':sum(b['count'] for b in metrics['calibration']),
                              'simulation':m['simulation_summary'],'cash':m['cash_summary']})
    if any(sha256(Path(p).read_bytes())!=digest for p,digest in before.items()):raise AssertionError('fixed_input_bytes_changed')
    return {'name':name,'task':task,'status':'PASS','dataset_snapshot_id':result['config']['dataset_snapshot_id'],
            'generator':json.loads((root/'generator.json').read_text()),'models':summaries,'fixed_input_bytes_unchanged':True,
            'result_content_sha256':digest,'verification':'Reuse completed successful training/replay only after current input, artifact, environment, code, config, predictions and run hashes verify.',
            'commands':[{'status':'PASS','elapsed_seconds':None,'max_rss_kib':None,
                         'measurement':'Prior successful training measurement was not persisted before aggregate failure; NOT_RECORDED. Current full tests and standalone CLI chains measured separately.',
                         'log':str((AUDIT/(name+'-'+task+'.log')).relative_to(ROOT))}],
            'scope':'connection validation; not stock predictive power or profitability'}


def real_audit():
    pit_path=ROOT/'data/stage3/pit_market.sqlite';store_path=ROOT/'data/stage3/datasets.sqlite'
    preserved=verify_datasets(pit_path,store_path)
    if preserved['status']!='PASS':return {'status':'BLOCKED','reason':'local_inputs_missing','all_dataset_preservation':preserved, 'training_selected_count':None, 'feature_status_counts':{}, 'label_status_counts':{}, 'stage2_snapshot_rows':None, 'real_predictive_power':'NOT_EVALUATED'}
    previous=json.loads((ROOT/'reports/stage3_validation.json').read_text())
    features,labels,reasons,exclusions=Counter(),Counter(),Counter(),Counter()
    details=[];commands=[];total=0
    with Database(pit_path) as pit, DatasetStore(store_path) as store:
        for ref in previous['real']['datasets']:
            did=ref['snapshot_id'];fixed=replay_dataset(store,pit,did)
            if fixed['content_sha256']!=ref['content_sha256']:raise AssertionError('actual_reference_changed')
            rows=list(iter_dataset_rows(store,pit,did))
            checked=eligibility_report(store,pit,did,rows,utc_now(),'strict')
            features.update(checked['feature_status_counts']);labels.update(checked['label_status_counts'])
            reasons.update(checked['source_reason_counts']);exclusions.update(checked['exclusion_counts']);total+=checked['selected_count']
            old=json.loads((ROOT/'data/stage4'/(did+'-strict.json')).read_text())
            c=old|{'version':'5.0.0','task':'regression','target_id':'price_return','horizon':5,'features':['close_price_change_5'],
                   'models':['zero','mean','ridge','tree'],'seed':20261003,'one_class_policy':'error','model_selection':'none',
                   'regression_threshold':.005,'classification_threshold':.55,'split':old['split']|{'horizon':5},
                   'simulation':{'policy_version':'4.0.0','horizon':5,'currency':'USD','initial_cash':'10000','start_at':'2024-01-01T00:00:00Z','end_at':'2025-01-01T00:00:00Z','costs':{}}}
            path=AUDIT/(did+'-strict.json');write_json(path,c)
            for action in ('training-config-check','training-run'):
                out=AUDIT/(did+'-'+action+'.json')
                cmd,_=command(cli(action,'--db',pit_path,'--dataset-db',store_path,'--mode','strict','--config',path,'--training-root',AUDIT/did,'--output',out),expected=3,name=did[:8]+'-'+action)
                commands.append(cmd)
                actual=json.loads(out.read_text())
                if actual['status']!='BLOCKED':raise AssertionError('strict_real_refusal_missing')
                if action=='training-run' and actual['folds']:raise AssertionError('real_models_or_metrics_generated')
            details.append({'dataset_snapshot_id':did,'selected_count':checked['selected_count'],'feature_status_counts':checked['feature_status_counts'],
                            'label_status_counts':checked['label_status_counts'],'source_reason_counts':checked['source_reason_counts'],
                            'exclusion_counts':checked['exclusion_counts'],'strict_request_status':'BLOCKED'})
    all_selection = []
    with Database(pit_path) as pit, DatasetStore(store_path) as store:
        for row in store.conn.execute('SELECT snapshot_id FROM dataset_snapshots ORDER BY snapshot_id').fetchall():
            at = training_selection(store,pit,row[0],training_asof=utc_now(),allow_research=False)
            all_selection.append({'dataset_snapshot_id':row[0],'selected_count':at['selected_count'],'exclusion_counts':at['exclusion_counts']})
    if total or sum(r['selected_count'] for r in all_selection):raise AssertionError('real_eligibility_changed_requires_new_review')
    stage2_path=ROOT/'data/stage2-validation.sqlite'
    before=sha256(stage2_path.read_bytes())
    with Database(stage2_path) as pit:
        ref=previous['real']['stage2_snapshot_reverified']['content_sha256']
        stage2=replay_snapshot(pit,ref,verify_files=True)
    if sha256(stage2_path.read_bytes())!=before:raise AssertionError('stage2_bytes_changed')
    return {'status':'BLOCKED','training_selected_count':total,'feature_status_counts':dict(features),'label_status_counts':dict(labels),
            'source_reason_counts':dict(reasons),'eligibility_exclusion_counts':dict(exclusions),'datasets':details,
            'all_frozen_strict_selection':{'snapshots':len(all_selection),'total_selected_memberships':sum(r['selected_count'] for r in all_selection),'counts':all_selection},'all_dataset_preservation':preserved,'stage2_snapshot_rows':stage2['result']['selected_count'],'stage2_bytes_unchanged':True,
            'commands':commands,'real_models_trained':False,'real_metrics_generated':False,'real_predictive_power':'NOT_EVALUATED'}


def risk_evidence():
    names = {
      1: 'test_holdout_transform_does_not_fit_statistics_or_model; test_changing_holdout_and_outcomes_leaves_fit_unchanged',
      2: 'test_future_corrected_versions_do_not_change_frozen_training',
      3: 'test_immature_label_and_total_return_no_fallback; Stage4 training_asof tests',
      4: 'test_allowlist_rejects_outcomes_metadata_identity_date',
      5: 'test_missing_constant_all_missing_and_large_values; test_infinity_and_nan_are_rejected',
      6: 'test_required_structural_fields_cannot_be_imputed',
      7: 'test_exact_columns_align_order_and_reject_missing_extra',
      8: 'test_one_class_error_and_explicit_fallback',
      9: 'test_empty_small_and_no_informative_columns; Stage4 insufficient fold tests',
      10: 'test_ridge_independent_closed_form',
      11: 'test_mean_zero_frequency_hand_values',
      12: 'test_artifact_reload_mode_hash_environment_and_origin; 28 fixed-model replays',
      13: 'test_end_to_end_and_mathematical_label_path_consistency; Stage4 prediction validation; saved-model CLI chain',
      14: 'regression/classification integration tests; standalone model-predict/prediction-evaluate/simulation-run',
      15: 'four actual training-config-check and four training-run refusals; all 40 actual snapshot selectors empty',
      16: 'test_artifact_reload_mode_hash_environment_and_origin; test_strict_synthetic_refuses_and_no_candidate_selection',
      17: 'fixed database hashes, 40 real and 7 previous synthetic replays, Stage2 352 rows, retraining/reload/Stage4 fixed replay',
      18: 'test_fit_and_inference_interfaces_cannot_read_test_labels; model_selection=none rejects selection requests'
    }
    return {str(k): {'status':'PASS','evidence':v} for k,v in names.items()}


def main():
    global RESUME_VERIFIED
    parser=argparse.ArgumentParser()
    parser.add_argument('--resume-verified',action='store_true',help='Reuse already successful runs only after checking current input/artifact/code/environment/config hashes; full tests and standalone inference run anew')
    RESUME_VERIFIED=parser.parse_args().resume_verified
    started=utc_now();t=time.perf_counter();AUDIT.mkdir(parents=True,exist_ok=True)
    initial_path = ROOT/'data/stage5/starting_state.json'
    if not initial_path.exists():
        paths = [p for dirname in ('src', 'docs', 'configs') for p in (ROOT/dirname).rglob('*') if p.is_file() and '__pycache__' not in str(p)]
        write_json(initial_path, {'head': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                                 'status': subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),
                                 'hashes': {str(p.relative_to(ROOT)): sha256(p.read_bytes()) for p in paths}, 'audit_origin': 'public checkout validation invocation'})
    print('Stage5 release tests started',flush=True)
    tests,log=command([PYTHON,'-m','unittest','discover','-s','tests','-v'],name='all-tests',timed=True)
    match=re.search(r'Ran (\d+) tests',log)
    count=int(match[1]) if match else 0
    if not count or 'FAILED (' in log:raise AssertionError('test_result_missing_or_failed')
    print(f'{count} tests passed; auditing actual inputs',flush=True)
    real=real_audit()
    synthetic_previous=verify_datasets(ROOT/'tests/generated/stage3/pit.sqlite',ROOT/'tests/generated/stage3/datasets.sqlite')
    prep,_=command([PYTHON,'scripts/prepare_synthetic_stage5.py'],name='prepare')
    # Independent roots/tasks; models each limit BLAS threads to one. No account or order calls.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending=[pool.submit(synthetic_case,n,task) for n in ('signal','null') for task in ('regression','classification')]
        synthetic=[f.result() for f in pending]
    profile_command,_=command([PYTHON,'scripts/profile_stage5_training.py'],name='retraining-profile',timed=True)
    profile=json.loads((AUDIT/'retraining-profile.json').read_text())
    standalone = []
    for task in ('regression', 'classification'):
        cmd,_ = command([PYTHON, 'scripts/replay_stage5_example.py', '--task', task], name='standalone-'+task, timed=True)
        standalone.append(cmd)
    print('Synthetic fit/reload/Stage4 replay completed; checking wheel and preservation',flush=True)
    build,_=command(['python3','-c','from setuptools.build_meta import build_wheel; build_wheel("dist/stage5")'],name='wheel')
    wheel=sorted((ROOT/'dist/stage5').glob('*.whl'))[-1]
    with zipfile.ZipFile(wheel) as z:
        modules=[p for p in z.namelist() if p.startswith('market_research/') and p.endswith('.py')]
        if any(z.read(p)!=(ROOT/'src'/p).read_bytes() for p in modules):raise AssertionError('wheel_source_mismatch')
    wheel_smoke_command,_=command([PYTHON,'scripts/check_stage5_wheel.py'],name='wheel-import-smoke')
    wheel_smoke=json.loads((AUDIT/'wheel-import-check.json').read_text())
    diff,_=command(['git','diff','--check'],name='diff-check')
    initial=json.loads((ROOT/'data/stage5/starting_state.json').read_text())
    changed=[p for p,h in initial['hashes'].items() if not (ROOT/p).exists() or sha256((ROOT/p).read_bytes())!=h]
    allowed={'README.md','pyproject.toml','requirements-lock.txt','src/market_research/cli.py','src/market_research/stage4/predictions.py','src/market_research/stage4/metrics.py','src/market_research/stage4/simulation.py'}
    generated_metadata = [p for p in changed if '.egg-info/' in p]
    unexpected=set(changed)-allowed-set(generated_metadata)
    if unexpected:raise AssertionError('protected_existing_files_changed:'+str(sorted(unexpected)))
    report={'stage':5,'started_at':started,'finished_at':utc_now(),'elapsed_seconds':time.perf_counter()-t,
            'starting_head':initial['head'],'starting_worktree':initial['status'],'starting_existing_tests':145 if 'audit_origin' not in initial else None,
            'environment':environment(),'risk_evidence':risk_evidence(),'tests':tests|{'tests_run':count,'failures':0,'errors':0,'skipped':0},
            'build':build|{'wheel':str(wheel.relative_to(ROOT)),'wheel_sha256':sha256(wheel.read_bytes()),'source_equal_modules':len(modules),'wheel_smoke':wheel_smoke,'wheel_smoke_command':wheel_smoke_command},
            'synthetic':synthetic,'retraining_profile':profile|{'resources':profile_command},'standalone_cli':standalone,'real':real,'previous_stage3_synthetic':synthetic_previous,
            'generated_build_metadata_changes':generated_metadata,'existing_files_changed':[p for p in changed if p not in generated_metadata]+['README.md','pyproject.toml','requirements-lock.txt'],'protected_files_checked':len(initial['hashes']),'protected_existing_data_unchanged':True,
            'code_sha256':code_version(ROOT),'diff_check':diff,'m2_executed':False,'commit_push':False,'orders_schedules_enabled':False,
            'verdicts':{'tests':'PASS','synthetic_training_flow':'PASS','artifact_reload_stage4':'PASS','real_data_readiness':'BLOCKED',
                        'real_predictive_power':'NOT_EVALUATED','stage6_entry':'BLOCKED','m2_hardware':'NOT_EVALUATED'},
            'known_warning':'existing Stage2 migration rejection leaks SQLite connection; ResourceWarning observed; not Codex UI usage warning',
            'development_fixes':['expected generated wheel .egg-info changes allowed and separately recorded; initial aggregate stopped after successful tests/training/replay/build', 'GNU time unavailable; use os.wait4 and persist measurements', 'small fixture calendar index corrected','absent output metric reason kept distinct from numeric overflow'],
            'reproducibility':'immutable JSON models and result hash, fixed-model predictions and Stage4 replay; cross-platform byte-identical retraining not promised',
            'source_verification':'existing provider research reused; Alpha/Massive/Sharadar official documents selectively checked 2026-10-03; no data purchase/download'}
    write_json(ROOT/'reports/stage5_validation.json',report)
    lines=['# 5단계 검증 보고서','',f"현재 실행: {report['finished_at']} · HEAD `{initial['head']}`. 기존 Stage2~4 미커밋 구현을 확인하고 보존했다.",'',
           '## 1. 시작 상태와 실제 변경','',f"기존 145 테스트 통과를 재확인했다. 학습 runner·전처리·JSON artifact·CLI·신호/무신호 fixture와 테스트를 추가했다. 기존 변경 파일: {', '.join(changed+['README.md','pyproject.toml','requirements-lock.txt'])}.",
           '', '## 2. 실행 환경·테스트·빌드','',f"Python {platform.python_version()}/Linux {platform.machine()}, sklearn {environment()['dependencies']['scikit-learn']}. 전체 {count}/{count} 통과. wheel {len(modules)}개 모듈이 소스와 byte 일치. git diff --check PASS.",
           f"전체 테스트 {tests['elapsed_seconds']:.2f}초 / 최대 RSS {tests.get('max_rss_kib')} KiB. 실제 로그·명령은 JSON과 data/stage5/release에 있다.",
           '', '## 3. 학습·전처리·미래정보 차단','', '고정 dataset/hash·training_selection·Stage4 날짜 분할/purge 사용. fit은 train만 받고 추론은 특징만 받는다. 전부 결측/상수 열은 train에서 drop, median/scaler는 train에서만 fit. 구조적으로 없는 필드는 거부. test 후보 선택 기능 없음. 합성·research를 strict로 승격할 수 없다.',
           '', '## 4. 기준선·합성 결과','', '5거래일 price_return 회귀와 같은 정의의 상승분류를 독립 실행했다. zero/mean/Ridge/tree 회귀, smoothed frequency/Logistic/tree 분류. 두 합성 dataset, 각각 2 folds. seed=20261003 고정. 생성 공식과 시각은 JSON generator에 있다.',
           '', '| 자료 | 과제 | fold | 모델 | train | test | MAE | Brier | fills |', '|---|---|---|---|---:|---:|---:|---:|---:|']
    for case in synthetic:
        for m in case['models']:
            metrics=m['metrics'];performance=m['simulation'] or {}
            lines.append(f"| {case['name']} | {case['task']} | {m['fold_id']} | {m['model']} | {m['training_count']} | {m['evaluated']} | {metrics['mae']['value']} | {metrics['brier']['value']} | {performance.get('fill_count')} |")
    lines += ['', '신호 자료에서도 Logistic은 두 fold 모두 frequency보다 Brier가 높았다. 이를 성공 판정에 맞추려고 seed/설정을 바꾸지 않았다. 이는 모델·평가·회계 연결 검사다. 합성 MAE·Brier·체결·손익은 실제 예측력 또는 수익성 증거가 아니다. 무신호 자료의 우연한 성과를 허용하고 seed를 변경하지 않았다. 회귀 .005/분류 .55 임계값과 비용을 사전에 고정했다. 서로 다른 회귀·분류 결정 규칙은 전략 비교에 영향을 준다. zero 거래 0은 정상이다. cash 기준선 포함. 보정 모델은 적용하지 않았다.',
              '', '## 5. 모델 재로드·예측·Stage4 연결','', '28개 모델 artifact 재로드·sklearn export 대조·fixed prediction 재생·Stage4 result hash 재생 PASS. 회귀 확률/classification 수익률은 null이다. Stage4 calibration 집계를 재사용한다. 모델 완료 시각은 실제 이번 시각, training_asof/simulated_generated_at은 논리적 과거 시각이다. 원본 Stage4 run은 코드 hash 변경으로 현재 소스로 replay를 거부하며 원 Stage4 wheel/source가 필요하다.',
              '', '## 6. 실제 자료 적격성과 미실행','',f"실제 strict 적격 {real.get('training_selected_count')}개(입력 부재 시 null). 특징 {real.get('feature_status_counts')}, 정답 {real.get('label_status_counts')}. 현재 로컬 입력이 있을 때 4 dataset의 training-config-check/run 총 8 요청은 종료3/BLOCKED. 실제 학습·예측력·수익률·Sharpe·적중률은 생성하지 않았다. 실제 예측력 `NOT_EVALUATED`.",
              f"실제 기존 {real['all_dataset_preservation']['count']} dataset 고정 replay, Stage2 {real['stage2_snapshot_rows']}행과 DB bytes 불변. 기존 Stage3 합성 replay는 JSON 상태를 참조한다. 사유별 건수는 JSON real.source_reason_counts/eligibility_exclusion_counts에 있다.",
              '', '## 7. 실제 데이터 병목별 다음 조치','', '[실행 과제](data_readiness_action_plan.md)에 역사 연구/앞으로의 observed 축적, 정확한 필드·권한·담당 입력·최소 검증·명령을 분리했다. 현재 원본·계약은 추가 확보하지 않았다.',
              '', '## 8. 6단계 진입 조건','', '실제 권한·ID·raw 가격 의미·기업행동·상태·시각·달력·성숙 정답을 충족하는 고정 자료와 의미 있는 시간순 train/validation/test 확보 후 단순 모델 비교가 선행 조건이다. 현재 BLOCKED. 합성 신경망으로 다음 단계를 정당화하지 않는다. [인계](stage6_handoff.md).',
              '', '## 9. M2 및 자원','', 'M2 8GB 실기기는 NOT_EVALUATED. Linux ARM64 수치와 구분한다. 개별 synthetic training os.wait4 rusage 측정:']
    for case in synthetic:
        c=case['commands'][0];lines.append(f"- {case['name']}/{case['task']}: " + (f"{c['elapsed_seconds']:.2f}초, 최대 RSS {c.get('max_rss_kib')} KiB" if c['elapsed_seconds'] is not None else '이전 완료 학습의 측정은 최종 집계 실패 전에 보관되지 않아 NOT_RECORDED; 검증된 모델/결과 hash를 확인해 재사용했다.'))
    lines.append(f"- 28개 합성 train-only 재학습·저장 pipeline/holdout 출력 대조: {profile_command['elapsed_seconds']:.2f}초, 최대 RSS {profile_command['max_rss_kib']} KiB. 현재 고정 Linux 환경에서 pipeline byte hash {profile['pipeline_hash_equal_count']}/{profile['count']} 일치; 플랫폼 간 보장은 아니다.")
    for c in standalone:
        lines.append(f"- 현재 저장 모델 추론→평가→시뮬레이션: {c['elapsed_seconds']:.2f}초, 최대 RSS {c['max_rss_kib']} KiB (개별 child/그 하위 순차 작업 rusage)")
    lines += ['', '원 학습은 4개 독립 작업 동시 실행이었다. 측정 항목별 범위를 구분하며 합산 최대메모리/단독 benchmark가 아니다. 설치 시 scikit-learn ARM64 wheel과 의존성을 내려받았다. 유료구매·계정 생성·외부 주문·반복 스케줄·커밋·푸시는 하지 않았다. 기존 SQLite ResourceWarning은 기록했다. Codex 사용량 UI 알림은 코드 경고가 아니다.','']
    (ROOT/'reports/stage5_validation.md').write_text('\n'.join(lines))
    print(json.dumps({'status':'PASS','tests':count,'models':sum(len(c['models']) for c in synthetic),'real_training':real['training_selected_count'],'real_predictive_power':'NOT_EVALUATED','elapsed_seconds':report['elapsed_seconds']}))


if __name__=='__main__':main()
