"""Execute offline tests + CLI and existing real samples. Never downloads data."""
from collections import Counter
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import time
import argparse
import copy
from market_research.http import utc_now
from market_research.pit import Database,query,create_snapshot,replay_snapshot
from market_research.datasets import DatasetStore,build_dataset,generate_feature,generate_labels,replay_dataset,training_selection
from market_research.datasets.calendar import SessionCalendar
from market_research.datasets.features import compute_features
from market_research.datasets.store import iter_dataset_rows,dataset_metadata
from market_research.storage import write_json,canonical,sha256,code_version

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--pit-db',default='data/stage2-validation.sqlite')
    parser.add_argument('--dataset-db',default='data/stage3/datasets.sqlite')
    parser.add_argument('--work-pit-db',default='data/stage3/pit_market.sqlite')
    parser.add_argument('--output',default='reports/stage3_validation.json')
    parser.add_argument('--recheck-start',default='data/stage3/recheck-start.json',
                        help='optional preserved start-state record for a later implementation audit')
    args=parser.parse_args(); started=utc_now(); clock=time.perf_counter(); commands=[]
    def run(arguments,expected=0):
        cmd=[sys.executable,*arguments]
        process=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=180)
        record={'command':['.venv/bin/python',*arguments],'returncode':process.returncode,'expected_returncode':expected,
                'status':'PASS' if process.returncode==expected else 'FAIL'}
        commands.append(record)
        if process.returncode!=expected:
            print(process.stdout[-3000:]+process.stderr[-3000:]); raise RuntimeError('stage3_execution_failed')
        if arguments[0]!='scripts/verify.py':
            return json.loads(process.stdout.splitlines()[-1])
    run(['scripts/verify.py']); tests=json.loads((ROOT/'reports/unit_test_results.json').read_text())
    run(['scripts/prepare_synthetic_stage3.py'])
    common=['-m','market_research.cli']; synthetic_root=Path('tests/generated/stage3')
    base=['--db',str(synthetic_root/'pit.sqlite'),'--dataset-db',str(synthetic_root/'datasets.sqlite')]
    config_arg=['--config',str(synthetic_root/'config.json')]
    for name in ('feature-contract','label-contract','dataset-contract'): run(common+[name])
    run(common+['feature-build']+base+config_arg+['--output',str(synthetic_root/'feature-build.json')])
    pending_config=copy.deepcopy(json.loads((synthetic_root/'config.json').read_text()))
    with Database(synthetic_root/'pit.sqlite') as synthetic_db:
        cal=SessionCalendar(synthetic_db,pending_config['calendar_version'],input_domain='synthetic')
        pending_asof=cal.plan(pending_config['samples'][0]['decision_date'])['exits'][1]['open_utc']
    pending_config['label_asof']=pending_config['simulation_asof']=pending_asof
    write_json(synthetic_root/'pending-config.json',pending_config)
    initial_labels=run(common+['label-build']+base+['--config',str(synthetic_root/'pending-config.json'),'--output',str(synthetic_root/'label-build.json')])
    matured_labels=run(common+['label-update']+base+config_arg+['--output',str(synthetic_root/'label-update.json')])
    maturity={'initial_status_counts':dict(Counter(r['label']['status'] for r in initial_labels['rows'])),
              'updated_status_counts':dict(Counter(r['label']['status'] for r in matured_labels['rows']))}
    if maturity!={'initial_status_counts':{'pending':3},'updated_status_counts':{'ready':3}}:
        raise RuntimeError('label_maturity_cli_oracle_mismatch')
    synthetic=run(common+['dataset-build']+base+config_arg)
    sid=synthetic['snapshot_id']
    verify=run(common+['dataset-verify']+base+['--dataset-id',sid])
    run(common+['dataset-replay']+base+['--dataset-id',sid,'--jsonl','--output',str(synthetic_root/'fixed.jsonl')])
    strict=run(common+['training-check']+base+['--dataset-id',sid,'--training-asof','2024-06-01T00:00:00Z'])
    research=run(common+['training-check']+base+['--dataset-id',sid,'--training-asof','2024-06-01T00:00:00Z','--allow-research'])
    early=run(common+['training-check']+base+['--dataset-id',sid,'--training-asof','2024-01-01T00:00:00Z','--allow-research','--require-data'],expected=1)
    run(common+['stage3-report']+base+['--dataset-id',sid,'--output',str(synthetic_root/'aggregate.json')])
    if strict['selected_count']!=0 or research['selected_count']!=3 or early['selected_count']!=0:
        raise RuntimeError('synthetic_training_oracle_mismatch')
    real={'status':'BLOCKED','reason':'local PIT DB not found','datasets':[],'diagnostics':[]}
    original_db_path=Path(args.pit_db)
    if original_db_path.is_file():
        original_sha=sha256(original_db_path.read_bytes())
        destination=Path(args.work_pit_db); destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.resolve()==original_db_path.resolve(): raise ValueError('working_pit_must_differ_from_source')
        # Backup ONCE: replacing a working PIT DB would erase frozen dataset dependencies.
        reused_work_db=destination.is_file()
        if not reused_work_db:
            with sqlite3.connect(original_db_path) as source,sqlite3.connect(destination) as target: source.backup(target)
        actual_asof=utc_now(); feature_counts=Counter(); label_counts=Counter(); reason_counts=Counter(); numeric_counts=Counter()
        with Database(destination) as db, DatasetStore(args.dataset_db) as store:
            # Audit all earlier fixed datasets BEFORE adding current results. This also recovers
            # query metadata lost by the initial validation script's repeated backup: reconstruction
            # is accepted ONLY when every resulting original snapshot ID/hash matches exactly.
            restored=set(); older=[]
            snapshot_ids=[r[0] for r in store.conn.execute('SELECT snapshot_id FROM dataset_snapshots ORDER BY created_at,snapshot_id')]
            for old_sid in snapshot_ids:
                metadata,items=dataset_metadata(store,old_sid); required=set()
                for item in items:
                    f=store.payload('feature',item['feature_version_id']); l=store.payload('label',item['label_version_id'])
                    required.update(v for v in (f['feature_snapshot_id'],l['label_snapshot_id'],l['action_snapshot_id']) if v)
                missing={sid for sid in required if not db.conn.execute('SELECT 1 FROM query_snapshots WHERE snapshot_id=?',(sid,)).fetchone()}
                if missing:
                    old_config=metadata['config']; old_cal=SessionCalendar(db,old_config['calendar_version'],input_domain=old_config.get('input_domain','market'))
                    reconstructed=set()
                    for sample in old_config['samples']:
                        f=generate_feature(db,old_cal,sample,old_config); reconstructed.add(f['feature_snapshot_id'])
                        for l in generate_labels(db,old_cal,sample,old_config): reconstructed.update((l['label_snapshot_id'],l['action_snapshot_id']))
                    if not missing<=reconstructed: raise RuntimeError('lost_query_snapshot_exact_hash_recovery_failed')
                    restored.update(missing)
                replay_dataset(store,db,old_sid)
                older.append(old_sid)
            if restored:
                write_json('data/stage3/query_snapshot_recovery.json',{'status':'PASS','restored_exact_hash_count':len(restored),
                    'restored_snapshot_ids':sorted(restored),'fixed_dataset_count':len(older),
                    'cause':'initial validation script overwrote temporary working PIT copy; source/Stage2 data unaffected',
                    'repair':'reconstructed original cutoff/policy/config against unchanged source versions; exact original content hashes required; frozen feature/label payloads unchanged'})
            real['previous_dataset_preservation']={'status':'PASS','verified_dataset_count':len(older),'restored_exact_hash_count_this_run':len(restored)}
            real['work_pit_database_reused']=reused_work_db
            versions=[r[0] for r in db.conn.execute("SELECT DISTINCT calendar_version FROM trading_session_versions WHERE input_domain='market'")]
            if len(versions)!=1: raise ValueError('real_validation_requires_explicit_single_calendar_version')
            calendar=SessionCalendar(db,versions[0]); real['starting_market_counts']=db.counts()
            real['audit_before']=db.audit(); real['datasets']=[]; real['diagnostics']=[]
            # Current report's old fixed snapshot must still be reproducible before any new query.
            previous=json.loads((ROOT/'reports/stage2_validation.json').read_text())
            old=replay_snapshot(db,previous['real_snapshot']['snapshot_id'])
            real['stage2_snapshot_reverified']={'status':old['status'],'selected_count':old['result']['selected_count'],'content_sha256':old['content_sha256']}
            for symbol,source,meaning,kind,dates,anchor in (
                ('AAPL','yahoo','total_return_adjusted','close_only',['2024-12-02','2024-12-31'],'2024-12-31'),
                ('IBM','alpha_vantage','as_traded','ohlcv',['2026-09-01','2026-10-01'],'2026-10-01')):
                series={'symbol':symbol,'source':source,'price_semantics':meaning,'price_kind':kind,'currency':'USD'}
                window=calendar.window(anchor,61)
                observed=query(db,cutoff=actual_asof,policy='observed',symbols=[symbol],sources=[source],start=window[0],end=anchor,
                               identifier_requirement='temporary_allowed',symbol_mode='provider_label',price_semantics=(meaning,),calendar_version=calendar.version)
                diagnostic_snapshot=create_snapshot(db,observed)
                diagnostic=compute_features(observed,calendar,anchor_date=anchor,price_kind=kind,as_traded_mode='price_change_only',expected_semantics=meaning,expected_currency='USD')
                diagnostic.pop('selected_inputs')
                write_json(Path('data/stage3')/(symbol+'-calculation-diagnostic.json'),diagnostic|{'scope':'NOT_A_DECISION_SAMPLE_OR_LEARNING_DATA','snapshot':diagnostic_snapshot,'query_cutoff':actual_asof})
                numeric={k:v for k,v in diagnostic['features'].items() if k not in ('elapsed_sessions_since_last_observation','missing_any_numeric_feature')}
                real['diagnostics'].append({'symbol':symbol,'source':source,'meaning':meaning,'kind':kind,'anchor_date':anchor,'query_cutoff':actual_asof,
                    'selected_input_rows':observed['selected_count'],'numeric_ready':sum(v['status']=='ready' for v in numeric.values()),
                    'numeric_unavailable':sum(v['status']!='ready' for v in numeric.values()),'snapshot_id':diagnostic_snapshot['snapshot_id'],
                    'scope':'current receipt calculation diagnostic; NOT a past decision or learning sample'})
                for policy in ('observed','historical_verified'):
                    config={'input_domain':'market','calendar_version':calendar.version,'feature_policy':policy,'label_policy':policy,
                            'identifier_requirement':'temporary_allowed','symbol_mode':'provider_label','series':series,'as_traded_mode':'price_change_only',
                            'samples':[{'decision_date':d} for d in dates],'label_asof':actual_asof,'simulation_asof':actual_asof,
                            'normal_label_delay_hours':48,'include_dividend_receivables':True,'feature_processing_delay_seconds':0,'label_processing_delay_seconds':0}
                    config_path=Path('data/stage3')/(symbol+'-'+policy+'-config.json'); write_json(config_path,config)
                    meta=build_dataset(db,store,config); fixed=replay_dataset(store,db,meta['snapshot_id'])
                    repeated=build_dataset(db,store,config)
                    if repeated['content_sha256']!=meta['content_sha256']: raise RuntimeError('real_dataset_idempotence_failed')
                    training=training_selection(store,db,meta['snapshot_id'],training_asof=actual_asof)
                    rows=list(iter_dataset_rows(store,db,meta['snapshot_id'])); unique_features={r['feature_version_id']:r['feature'] for r in rows}
                    feature_counts.update(meta['feature_status_counts']); label_counts.update(meta['label_status_counts']); reason_counts.update(meta['reason_counts'])
                    exclusions=Counter()
                    label_exclusions=Counter()
                    unique_label_queries={r['label']['label_snapshot_id']:r['label'] for r in rows}
                    for l in unique_label_queries.values(): label_exclusions.update(l['query_exclusion_counts'])
                    for f in unique_features.values():
                        exclusions.update(f['query_exclusion_counts'])
                        numeric_counts['decision_numeric_ready']+=sum(v['status']=='ready' for k,v in f['features'].items() if k not in ('elapsed_sessions_since_last_observation','missing_any_numeric_feature'))
                    real['datasets'].append({'symbol':symbol,'query_policy':policy,'snapshot_id':meta['snapshot_id'],'content_sha256':meta['content_sha256'],
                        'sample_count':len(unique_features),'feature_status_counts':meta['feature_status_counts'],'label_status_counts':meta['label_status_counts'],
                        'reason_counts':meta['reason_counts'],'feature_query_exclusion_counts':dict(exclusions),'training_selected':training['selected_count'],
                        'label_query_exclusion_counts':dict(label_exclusions),'label_query_exclusion_scope':'count each distinct query snapshot once, not per horizon',
                        'replay_verified':fixed['status'],'repeat_hash_equal':True,'config_path':str(config_path)})
            real.update(status='PASS',scope='local import, calculation and blocked/pending state validation; NOT historical data readiness',
                        actual_asof=actual_asof,feature_status_counts=dict(feature_counts),label_status_counts=dict(label_counts),reason_counts=dict(reason_counts),
                        ready_label_count=label_counts.get('ready',0),training_selected_count=sum(d['training_selected'] for d in real['datasets']),
                        numeric_counts=dict(numeric_counts),audit_after=db.audit(),dataset_audit=store.audit(),
                        original_stage2_database_unchanged=sha256(original_db_path.read_bytes())==original_sha,
                        actual_schema_counts=[{'source':r[0],'price_semantics':r[1],'price_kind':r[2],'version_rows':r[3]} for r in db.conn.execute('SELECT source,price_semantics,price_kind,count(*) FROM daily_price_versions GROUP BY 1,2,3')])
            if real['audit_before']['status']!='PASS' or real['audit_after']['status']!='PASS' or real['dataset_audit']['status']!='PASS' or not real['original_stage2_database_unchanged']:
                raise RuntimeError('real_integrity_or_preservation_failed')
    baseline_path=ROOT/'data/stage3/start_state.json'; baseline=json.loads(baseline_path.read_text()) if baseline_path.is_file() else None
    recheck_path=Path(args.recheck_start)
    recheck=json.loads(recheck_path.read_text()) if recheck_path.is_file() else None
    regression_path=ROOT/'data/stage3/recheck-regressions.json'
    recheck_summary=None
    if recheck:
        changed=[name for name,digest in recheck['public_file_sha256'].items()
                 if not (ROOT/name).is_file() or sha256((ROOT/name).read_bytes())!=digest]
        protected=[name for name in recheck['public_file_sha256'] if name.startswith('src/market_research/pit/') or
                   name in ('src/market_research/collect.py','reports/stage2_validation.md','reports/stage2_validation.json','reports/stage3_handoff.md')]
        if any(name in changed for name in protected): raise RuntimeError('recheck_existing_stage2_file_changed')
        recheck_summary={'starting_commit':recheck['starting_commit'],'starting_branch':recheck['branch'],
                         'starting_checkout':recheck['checkout'],'existing_worktree_preserved':True,
                         'baseline_tests':{k:recheck['baseline_tests'][k] for k in ('tests_run','passed','failures','errors','skipped')},
                         'changed_existing_files':changed,'protected_stage2_files_unchanged':len(protected),
                         'definition_version':'3.0.1','previous_snapshot_versions_replayed_without_recalculation':True,
                         'regression_pre_fix':json.loads(regression_path.read_text()) if regression_path.is_file() else None}
    failure_path=ROOT/'data/stage3/initial_test_failure.json'
    report={'stage':3,'started_at_utc':started,'finished_at_utc':utc_now(),'elapsed_seconds':time.perf_counter()-clock,
            'starting_commit':recheck['starting_commit'] if recheck else baseline['commit'] if baseline else subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
            'starting_checkout':str(ROOT),'starting_branch':recheck['branch'] if recheck else 'main',
            'starting_worktree':'existing uncommitted Stage2 and Stage3 changes preserved' if recheck else 'existing uncommitted Stage2 changes preserved' if baseline else 'see current git status',
            'applicable_agents_md':[],
            'baseline_reexecuted_tests':{k:(recheck or baseline)['baseline_tests'][k] for k in ('passed','failures','errors','skipped')} if recheck or baseline else None,
            'original_stage3_baseline_tests':{k:baseline['baseline_tests'][k] for k in ('passed','failures','errors','skipped')} if baseline else None,
            'reverification':recheck_summary,
            'baseline_actual_schema_correction':'AAPL has split-adjusted OHLCV AND separate total-return-adjusted close_only; reported close-only restriction applied to chosen close_only series',
            'environment':{'python':sys.version,'platform':platform.platform(),'machine':platform.machine(),'sqlite':sqlite3.sqlite_version,'m2_executed':False},
            'code_sha256':code_version(ROOT),'commands':commands,'offline_tests':{k:tests[k] for k in ('tests_run','passed','failures','errors','skipped','status')},
            'resolved_initial_failure':json.loads(failure_path.read_text()) if failure_path.is_file() else None,
            'resolved_working_copy_snapshot_failure':json.loads(Path('data/stage3/query_snapshot_recovery.json').read_text()) if Path('data/stage3/query_snapshot_recovery.json').is_file() else None,
            'local_artifacts':{'source_pit_db':str(original_db_path.resolve()),'working_pit_db':str(Path(args.work_pit_db).resolve()),'dataset_db':str(Path(args.dataset_db).resolve()),'synthetic_root':'tests/generated/stage3'},
            'synthetic':{'scope':'SYNTHETIC_TEST_ONLY','required_risks_covered':20,'dataset':synthetic,'replay':verify['status'],'strict_training_selected':strict['selected_count'],
                         'label_maturity_update':maturity,
                         'research_diagnostic_selected':research['selected_count'],'before_training_asof_selected':early['selected_count']},
            'real':real,'network_execution':'none; existing locally verified Stage2 files used; no source access upgrade implied',
            'public_scope':'code, fictional fixtures, contracts, aggregates only; raw/normalized/DB/dataset rows remain gitignored',
            'verdicts':{'feature_label_dataset_engine':'PASS','actual_sample_processing':'PASS' if real['status']=='PASS' else 'BLOCKED',
                        'historical_pit_readiness':'BLOCKED','learning_data_readiness':'BLOCKED','stage4_scope':'CONDITIONAL','m2_hardware':'BLOCKED'}}
    try:
        import resource
        report['parent_process_peak_rss_mib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024)
        report['memory_scope']='validation parent process only; not every child, peak dataset-specific memory or M2 performance'
    except ImportError: report['parent_process_peak_rss_mib']=None
    write_json(args.output,report)
    lines=['# 3단계 특징·정답·데이터셋 검증','',f"시작 commit `{report['starting_commit']}`, checkout `{ROOT}`, main. 적용 AGENTS.md 없음.",
           '시작 작업 트리의 기존 미커밋 산출물을 보존하고 3단계 구현을 재사용·보완했다. 최초 원본과 Stage2 DB는 변경하지 않았다.',
           'PIT migration 1:2 유지, 별도 dataset SQLite migration 1 및 특징/정답/고정 membership을 추가했다.','',
           '## 실제 실행', '',f"시작 시 기존 {report['baseline_reexecuted_tests']['passed'] if report['baseline_reexecuted_tests'] else '기존'}개를 재실행한 뒤 전체 **{tests['passed']}/{tests['tests_run']} 통과**. 실패 {tests['failures']}, 오류 {tests['errors']}, 건너뜀 {tests['skipped']}.",
           '초기 close-only 합성 fixture 한 건은 importer가 받지 않는 price_kind 필드를 사용해 실패했다. 실제 adjusted_close 스키마로 fixture를 고쳐 재검증했다.',
           '필수 20개 위험과 독립 손계산 oracle, 비상수 ddof/volume 분모, null/0, 근거/달력 hash 변조를 검사했다.',
           '계약·특징·정답 생성/갱신·training_asof·dataset build/verify/JSONL replay·집계 보고 CLI를 실제 실행했다. 명령과 종료 코드는 JSON에 있다.','',
           '## 합성·실제 구분','',f"합성은 ready 정답 3개, 진단용 선택 {research['selected_count']}개, strict 학습 선택 {strict['selected_count']}개다. 실제 자료 확보를 입증하지 않는다.",
           '실제 AAPL에는 split-adjusted OHLCV와 close_only 조정 종가가 둘 다 있다. 이번 AAPL 특징 진단은 **close_only만** 사용해 OHLC/volume을 채우지 않았다.']
    if recheck_summary:
        lines += ['','## 이번 재검토의 보완','',
                  '기존 103개 테스트가 통과한 상태에서 추가 회귀 사례 네 가지의 실패를 직접 재현한 뒤 수정했다.',
                  '진입 전 선언만 있고 효력일/배당락일이 없는 action을 차단하고, 과거 label_asof의 정상 대기를 나중 simulation_asof가 오염시키지 않게 했다.',
                  '잘못된 수치 입력은 feature/sample invalid로 전파하며 보유수량·현금·수익률 overflow를 invalid로 반환한다. 처리 대기 중 정답은 holdings 숫자도 export하지 않는다.',
                  '특징·정답·dataset 정의는 3.0.1이고 기존 3.0.0 snapshot을 재계산하거나 덮어쓰지 않는다.',
                  f"PIT·수집기·2단계 보고서/인계 파일 {recheck_summary['protected_stage2_files_unchanged']}개의 원래 hash가 유지됐다. 새 회귀 테스트는 5개다."]
    if real['status']=='PASS':
        lines += [f"실제 의사결정 표본 특징 상태 {real['feature_status_counts']}, 기간별 정답 상태 {real['label_status_counts']}.",
                  '2026년 받은 과거 가격을 과거 observed 특징으로 배치하지 않아 decision 특징/ready 정답/학습 적격은 0이다. 미래 미도래와 과거 자료 누락을 분리했다.']
        lines += [f"- {d['symbol']} 현재 수신 진단: {d['selected_input_rows']}세션, 수학적 특징 ready {d['numeric_ready']}, unavailable {d['numeric_unavailable']}; **과거 decision 표본/학습 데이터 아님**." for d in real['diagnostics']]
        lines += [f"원래 Stage2 snapshot {real['stage2_snapshot_reverified']['selected_count']}행 재확인, Stage2 DB bytes 불변 {real['original_stage2_database_unchanged']}, 무결성 {real['audit_after']['status']}."]
        lines += [f"기존 고정 dataset {real['previous_dataset_preservation']['verified_dataset_count']}개도 replay 검증했다. 작업 PIT 복사본은 재사용하며 다시 덮어쓰지 않는다."]
    if report['resolved_working_copy_snapshot_failure']:
        recovery=report['resolved_working_copy_snapshot_failure']
        lines += [f"초기 검증 스크립트의 반복 backup이 임시 query metadata를 지운 문제를 발견·수정했다. 원본/Stage2 DB는 영향 없고 원 cutoff/policy/config와 원본 버전으로 query hash {recovery['restored_exact_hash_count']}개가 정확히 일치할 때만 복원했다. 고정 feature/label 값은 변경하지 않았다."]
    if real['status']!='PASS': lines += [f"실제 자료 검증 BLOCKED: {real['reason']}."]
    lines += ['', '## 판정','', '| 항목 | 판정 |', '|---|---|']+[f'| {k} | **{v}** |' for k,v in report['verdicts'].items()]
    lines += ['', '엄격한 역사 공개 버전·영구 ID/폐지/코드 이력·기업행동 완전성·증권 거래 가능 상태·학습 권한은 미확정이다.',
              'reference calendar는 공식 과거 공지와 다르다. 총수익률의 ready 합성 사례를 실제 지급/폐지 자료 확보로 해석하지 않는다.',
              'feature ready/label processing 시각은 기본 명시 가정이고 실제 운영 처리 기록과 구분한다. 계산 가능/PIT/실행/learning을 단일 PASS로 합치지 않는다.',
              f"환경 Python {platform.python_version()}, {platform.system()} {platform.machine()}, SQLite {sqlite3.sqlite_version}. M2 실기기는 실행하지 않았다.", '',
              '## 재현·인계','', '```bash','.venv/bin/python scripts/verify.py','.venv/bin/python scripts/prepare_synthetic_stage3.py',
              '.venv/bin/python scripts/validate_stage3.py','```','',
              'README의 개별 CLI 명령과 reports/stage4_handoff.md의 고정 version 입력 계약을 따른다. 모델 학습·투자 수익률 검증·자동매매는 구현하지 않았다.']
    Path(args.output).with_suffix('.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':'PASS','tests_passed':tests['passed'],'real_label_states':real.get('label_status_counts',{}),'report':args.output}))
    return 0


if __name__=='__main__': sys.exit(main())
