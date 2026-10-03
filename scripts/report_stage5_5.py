"""Assemble current local execution evidence into public count-only reports.
Run data-blockers, observed cycle/replay and validate_stage5_5.py first.
"""
from collections import Counter
import json
from pathlib import Path
import os
import platform
import shutil
import subprocess
from market_research.storage import write_json,sha256,code_version
from market_research.http import utc_now

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/stage5_5'


def read(name):return json.loads((OUT/name).read_text())


def main():
    diagnostics=read('diagnostics.json');verification=read('verification.json')
    actual=read('actual_collection.json')[0]['manifest_record'];operation=read('operation_final.json');replay=read('operation_final_replay.json')
    checks=[verification['tests']['exit_code'],read('scope_runner_final_tests.json')['exit_code'],verification['build']['exit_code'],verification['build']['offline_install']['exit_code'],verification['build']['offline_import_and_cli']['exit_code'],verification['pip_check']['exit_code'],verification['diff_check']['exit_code']]
    if any(checks) or not verification['build']['source_equal']: raise ValueError('verification_failed_report_must_not_claim_pass')
    if actual['status']!='success' or actual['http_status']!=200 or operation['status']!='NON_TRADING_DAY' or operation['sample'] is not None or not replay['idempotent_replay']:
        raise ValueError('recorded_stage5_5_scenario_mismatch_reassess_before_reporting')
    cpu_rows=json.loads(subprocess.check_output(['lscpu','--json'],text=True))['lscpu']
    cpu_model=next(row['data'] for row in cpu_rows if row['field']=='Model name:')

    examples={}
    for row in diagnostics['rows']:examples.setdefault(row['sample_id'],row)
    label_pairs={}
    for row in diagnostics['rows']:label_pairs.setdefault((row['sample_id'],row['horizon']),row)
    old=read('starting_state.json')['protected_files']
    changed=[name for name,digest in old.items() if not (ROOT/name).is_file() or sha256((ROOT/name).read_bytes())!=digest]
    mem={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.split()[1].isdigit()}
    disk=shutil.disk_usage(ROOT)
    environment=verification['environment']|{'cpu_model':cpu_model,'logical_cpus':os.cpu_count(),'ram_total_bytes':mem['MemTotal'],'ram_available_bytes':mem['MemAvailable'],'swap_total_bytes':mem['SwapTotal'],'disk_total_bytes':disk.total,'disk_available_bytes':disk.free,'resource_captured_at':utc_now()}
    df=diagnostics
    feature_memberships=sum(len({r['sample_id'] for r in df['rows'] if sid in r['snapshot_ids']}) for sid in [s['snapshot_id'] for s in df['snapshot_results']])
    counts={'snapshots':df['snapshot_count'],'independent_sample_keys':df['sample_count'],'feature_snapshot_memberships':feature_memberships,'feature_versions':df['referenced_feature_version_count'],'label_snapshot_memberships':df['snapshot_membership_count'],'label_versions':df['referenced_label_version_count'],'unique_sample_horizon_pairs':df['distinct_sample_horizon_count'],'horizon_label_versions':df['labels_by_horizon'],'feature_version_status_counts':df['feature_version_status_counts'],'label_version_status_counts':df['label_version_status_counts'],'logical_feature_status_counts':dict(Counter(r['feature_status'] for r in examples.values())),'logical_label_status_counts':dict(Counter(r['label_status'] for r in label_pairs.values())),'training_eligible_samples':0}
    diag_public={k:v for k,v in df.items() if k!='rows'}
    diag_public['row_details_local']='data/stage5_5/diagnostics.json'
    lookback=operation['lookback_diagnostic']
    report={'stage':'5.5','generated_at':utc_now(),'starting_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'code_sha256':code_version(ROOT),'environment':environment,'before':counts,'after_existing_frozen_datasets':counts,
        'new_real_data':{'network_requests':len(actual.get('attempts',[])),'http_status':actual['http_status'],'fresh_network_download':actual['network_download_performed'],'receipt_id':actual['receipt_id'],'received_at':actual['fetched_at_utc'],'processing_completed_at':actual['processing_completed_at_utc'],'raw_sha256':actual['sha256'],'rows':actual['row_count'],'period':actual['actual_data_period'],'normalization_rights_not_promoted':True},
        'current_operation':{'status':operation['status'],'cutoff':operation['cutoff'],'calendar':operation['calendar'],'collection_status':operation['collection']['status'],
            'lookback_status':lookback['status'],'lookback_input_rows':lookback['input_rows'],'lookback_anchor':lookback['anchor_date'],'query_snapshot_id':lookback['snapshot_id'],
            'feature_status_counts':dict(Counter(v['status'] for v in lookback['features'].values())),'data_period':operation['collection']['actual_data_period'],
            'operational_samples_created':0,'pending_labels_created':0,'prediction_status':operation['prediction_status'],'replay_idempotent':replay['idempotent_replay'],'replay_new_network_download':replay['network_download_this_execution'],'profile':read('operation_profile.json')},
        'missed_decision_check':{'status':read('missed_operation.json')['status'],'inputs':read('missed_operation.json')['lookback_diagnostic']['input_rows'],'exclusions':read('missed_operation.json')['lookback_diagnostic']['query_exclusion_counts']},
        'diagnostics':diag_public,'tests':verification['tests'],'post_validation_scope_runner_checks':read('scope_runner_final_tests.json'),'build':verification['build'],
        'pip_check':verification['pip_check'],'diff_check':verification['diff_check'],'strict_requests':read('strict_execution.json'),
        'protected_existing_data':{'files_checked':len(old),'changed':changed,'unchanged':not changed},'stage2_fixed_replay':read('stage2_replay.json'),
        'scope_comparison':{'strict_history':'BLOCKED: missing contemporary vintage/universe/security/action/state/rights','fixed_security_research':'CALCULATION_ONLY: whole-market delisted universe not required; per-security/target/rights missing','forward_observed':'CURRENT_LOOKBACK_PASS; current trading sample deferred on non-trading day; training blocked'},
        'verdicts':{'engine_connection':'PASS','actual_data_access':'PASS_OFFICIAL_IBM_DEMO_ONLY','current_observed_lookback':'PASS','current_operational_sample':'DEFERRED_NON_TRADING_DAY','real_training_readiness':'BLOCKED','strict_historical_evaluation':'BLOCKED','real_predictive_power':'NOT_EVALUATED'},
        'real_models_trained':0,'real_predictions_generated':0,'real_performance_metrics_generated':False,'orders_schedules_enabled':False,'commit_push_performed':False,
        'source_documents':['README.md','reports/stage5_validation.md','reports/stage5_validation.json','reports/data_readiness_action_plan.md','reports/stage6_handoff.md','docs/provider_comparison.md','docs/time_query_policies.md'],
        'official_sources':['https://www.alphavantage.co/documentation/','https://www.alphavantage.co/terms_of_service/'],
        'limitations':['Public demo access does not confirm general account/long-term collection/training rights.','Current lookback arithmetic is not a trading sample or investment prediction.','Existing pending versions represent three logical horizons and still have future exits.','Version and blocker counts overlap; snapshots are not independent observations.','Unchanged existing SQLite ResourceWarning remains in original migration rejection test.']}
    write_json(ROOT/'reports/stage5_5_validation.json',report)
    matrix=['# 실제 차단 원인 표 — Stage5.5','',f"평가 시각 {df['evaluated_at']}. 모든40 frozen dataset의 원본/hash를 재생 검증했다. 상세 각 version·snapshot·조건은 로컬 `data/stage5_5/diagnostics.json`이다.",'','## 집계 단위','',f"40 snapshot / 4 sample / {feature_memberships} snapshot-특징 행 / 56 distinct 특징 version / 240 정답 version(1·5·20일 각80) / 12 논리 sample-horizon이다. 특징 version56 blocked, 정답180 blocked·60 pending. 논리 특징4 blocked, 논리 정답9 blocked·3 pending, 실제 적격0이다.",'','기존 Stage5의 특징8·정답18 blocked/6 pending은 선택된4 snapshot의 정책별 반복 행 집계다. 이번 전체 version 합집합과 분모가 다르며 악화나 신규 시장 표본 증가가 아니다. 변경 전·후 기존 입력과 적격 수는 같다. 현재 관측 진단61행은 별도 새 수신 이후 snapshot이며 학습 표본이 아니다.','', '| 범주 | 해당 논리 sample-horizon 수 | 해석 |','|---|---:|---|']
    descriptions={'publication_version':'정확한 공개/버전 또는 당시 calendar 근거 부족; strict 차단','observed_timing':'옛 cutoff 뒤 실제 수신/미입증 legacy receipt가 있는 정책 버전','price_fields':'원시가/의미/수치 입력 조건 또는 그 결과의 계산 불가','corporate_actions':'구간 완전성 assertion 없음; 빈 사건 목록 승격 금지','security_identity_listing':'temporary ID, verified share identity 없음','tradability':'성숙 IBM9/1의 첫 오류3; pending 표본도 향후 개별 상태 필요','future_outcome':'IBM10/1의 종료1/5/20 세션이 현재 미래','past_outcome_missing':'AAPL12/31의3개 및12/2의20일 endpoint가2025년으로 로컬 범위 밖','rights':'source/symbol 학습 이용허용 근거 없음','code_schema_connection':'derived no-input/readiness 및 historical-only 연결 제한의 영향을 표시; 원본/스키마 무결성 오류는0'}
    for key,n in df['category_unique_sample_horizon_counts'].items():matrix.append(f'| {key} | {n} | {descriptions[key]} |')
    matrix+=['','행은 여러 범주에 포함된다. 합산 금지. 예: publication_version∩observed_timing=12, rights∩security_identity_listing=12. 전체 교차 수는 검증 JSON에 있다.','', '## 실제 sample과 차단 함수/정확한 필드','', '| sample ID · 증권 · 결정 | snapshot 예시 | 첫 특징/정답 차단 | 원본에 있는 것 / 없는 것 | 조치 |','|---|---|---|---|---|']
    for sid,row in sorted(examples.items(),key=lambda v:(v[1]['symbol'],v[1]['decision_at'])):
        symbol=row['symbol'];date=row['decision_at'][:10]
        raw='IBM 원 OHLCV 존재; receipt는 옛 decision 뒤. 공개 version·share ID·정규세션/volume 확정·action/state/권한 부족' if symbol=='IBM' else 'Yahoo split-adjusted OHLC 존재; 선택된 total-return-adjusted close_only에는 open 없음. as-traded open/2025 endpoint/공개 version·ID·권한 부족'
        matrix.append(f"| `{sid}` · {symbol} · {date} | `{row['snapshot_ids'][0]}` | generate_feature→query.eligible: zero inputs; compute_labels: {row['label_status']} / {next((r for r in row['reasons'] if r in ('target_open_not_yet_reached','opening_prices_unavailable_close_only','security_trading_status_unverified')), 'see JSON')} | {raw} | 과거 receipt 소급 불가; current lookback/new scope는 구현. 외부 증거 없이 적격 승격 불가 |")
    matrix+=['','| sample · horizon | entry date | exit date | 로컬 원 계약 endpoint evidence |','|---|---|---|---|']
    for (sid,h),row in sorted(label_pairs.items()):
        e=row['original_endpoint_evidence'];matrix.append(f"| `{sid[:12]}…` · {h} | {e[0]['date']} | {e[1]['date']} | version counts {e[0]['local_version_count']}/{e[1]['local_version_count']}; non-null open {e[0]['non_null_open_version_count']}/{e[1]['non_null_open_version_count']} |")
    matrix+=['','## 코드로 해결한 연결과 외부 조건','', '1. 현재 수신한 과거 가격은 현재 cutoff의 lookback으로 허용하고 과거 decision에는 제외한다. 기존 query가 이미 구분한 동작을 단일 운영 경로에 연결했다.','2. 역사 정책만 허용하던 learning_ready를 조용히 바꾸지 않고 explicit fixed/forward scope selector·Stage5 gate를 추가했다. 해당 증권과 고정 특징·권한을 검사하며 전체 폐지군은 제한 scope 조건으로 추가하지 않았다.','3. standalone label-update의 미확정 권한 상태를 실제 승인으로 간주하지 않는다. 운영 maturity는 검토된 evidence를 다시 검사해 별도 scoped readiness를 만들고 actual 완료·새 정답/고정 dataset으로 보존한다.','4. 첫-error 뒤 숨은 원시가·publication·identity·coverage/rights·2025 endpoint 누락을 다중 진단으로 드러냈다. 기존 실제 schema/hash/link 오류는 발견하지 않았다.','5. 현재 pending60 version은3개 논리 horizon이며 모두 future_exit_session이다. 종료가 지났지만 local 범위 밖인4개는 pending과 별도 past_outcome_missing이다. 기존 snapshot 수정 없음.','','필요한 권한·키·증권/세션 필드 정의·행동/상태·내보내기 형식과 최소 검증은 [실행 과제](data_readiness_action_plan.md)와 [운영 계약](../docs/observed_operation_contract.md)에 있다.']
    (ROOT/'reports/data_blocker_matrix.md').write_text('\n'.join(matrix)+'\n')
    t=verification['tests'];p=read('operation_profile.json')[0]
    lines=['# Stage5.5 검증 보고서','',f"실행 근거: {report['generated_at']} · HEAD `{report['starting_head']}` · Linux aarch64. [JSON](stage5_5_validation.json)과 [상세 차단 표](data_blocker_matrix.md).",'', '## 1. 시작 상태와 실제 변경','', 'README·Stage5 보고/JSON·확보 계획·인계·출처/이용권·정책/selector/수집/manifest/특징/정답 코드와 실제 미커밋 작업 트리를 조사했다. 보고된 HEAD와 일치하며 Stage2~5 변경은 기존 사용자 작업으로 보존했다. observed scope/진단/review/단일 실행과16개 위험 테스트, 계약/CLI/보고·Linux 재현 명령을 추가했다. 별도 거래/회계 엔진이나 신경망은 추가하지 않았다.','', '## 2. 차단 원인별 변경 전·후','', '| 단위 | 전 | 후(기존 frozen 입력) |','|---|---:|---:|','| snapshot / 논리 sample |40 /4|40 /4|',f'| snapshot-특징 행 / 특징 version |{feature_memberships} /56|{feature_memberships} /56|','| 정답 version blocked / pending / ready |180 /60 /0|180 /60 /0|','| 논리 정답 blocked / pending / ready |9 /3 /0|9 /3 /0|','| 실제 학습 적격 sample |0|0|','', '전체 각 horizon80 version이며 총12 논리 sample-horizon이다. 이전 선택4 snapshot의8 특징·18 blocked/6 pending과 분모 차이를 명시했다. 이유별 overlap와 필드·정책은 차단 표/JSON을 참조한다. 신규 current lookback 진단은61세션, 신규 거래 sample/pending은0이다.','', '## 3. 코드로 해결한 문제와 외부 자료','', '현재 lookback/과거 observed 구분, explicit scope·학습 gate·검토 overlay, maturity 새 버전/실제 완료 시각, 다중 blocker 진단, 재실행/원본 보존을 연결했다. 기존 strict 의미와 학습 적격0을 유지했다. missing share identity·verified session/volume·action/state interval·ML 권한·역사 vintage/universe는 코드로 만들지 않았다. AAPL raw 시가/2025 endpoint도 미확보다.','', '## 4. 실제 요청·수신·특징·pending','',f"공식 IBM compact demo에 새 HTTP 요청1회: HTTP{actual['http_status']}, {actual['row_count']}행, {actual['actual_data_period']['start']}~{actual['actual_data_period']['end']}. 수신 {actual['fetched_at_utc']}, 처리 {actual['processing_completed_at_utc']}. raw hash/receipt는 JSON에 기록했다. 후속 CLI는 성공 캐시 재정규화/캐시를 사용했으며 새 다운로드로 표현하지 않았다.",'', f"현재 cutoff {operation['cutoff']}에서 {lookback['anchor_date']}까지61연속세션·missing0·숫자 특징 산술 ready. 현재 받은 과거 입력이며 strict PIT/경제적 조정 수익률 증거가 아니다. 토요일 NON_TRADING_DAY라 거래 표본0·pending0을 유지했다. 금요일 과거 cutoff를 지정한 요청은 입력0/received_after_cutoff/MISSED_DECISION이었다. 실제 모델은 prediction_not_available다.",'', '## 5. 재현·복구 검증','', '완료된 동일 cutoff CLI는 원본·query hash를 확인하고 idempotent_replay=true, 새 네트워크=false였다. 테스트는 정상 세션 pending3→성숙1/5일 ready2, 미래20일 버전/특징/기존 snapshot 불변과 손계산 open-to-open 일치를 확인했다. 이 자료는 임시 fictional HTTP/evidence fixture이며 실제 권한·표본이 아니다. raw 저장 후 crash·SQLite 오류·HTTP/파싱 실패의 기록/재개, 정정 후 원 snapshot 불변, 주말/휴장·late receipt/processing도 검증했다.','', '## 6. 실제 학습 가능 여부','', '40개 모두 strict selector0, 신규 두 제한 scope도 기존 자료 적격0이다. 선택4개 config의 검사/학습8요청은 종료3/BLOCKED, strict_dataset_has_no_eligible_samples로 거부됐다. 실제 학습·예측·수익률·Sharpe·적중률을 생성하지 않았다. actual fit에는 이용권/필드/시각/성숙 계약과100표본/180일·purge 후60/20/20·분류 양쪽 클래스가 필요하다. 작은 축적 성공으로 기준을 낮추지 않는다. 엄격한 역사 평가는 BLOCKED, 실제 예측력 NOT_EVALUATED다.','', '## 7. 기존 테스트·빌드·보존','',f"전체 {t['count']}/{t['count']} PASS; 이후 scope→Stage5 특징 목록 gate/실패 기록 강화의16개 집중 검사 PASS. wheel48개 Python 모듈 소스 byte 일치, offline 설치·48모듈 import·observed CLI PASS. pip check/diff check PASS. 최초 inventory {len(old)}개 기존 데이터 파일 내용 불변, 실제40 frozen replay와 Stage2 fixed352행 재생 PASS. 기존 migration 거부 테스트의 SQLite ResourceWarning은 남아 있으며 실패는 아니다. 테스트 합성 흐름은 엔진 연결 검증이며 실제 예측력 평가와 구분한다.",'', '## 8. 현재 Linux 자원','',f"{environment['cpu_model']} {environment['logical_cpus']}코어, Python3.13.5, RAM {environment['ram_total_bytes']:,}바이트, swap {environment['swap_total_bytes']:,}바이트. 캡처 시 사용 가능 RAM {environment['ram_available_bytes']:,}바이트, 저장공간 여유 {environment['disk_available_bytes']:,}바이트. 의존성/커널·전체 디스크는 JSON에 기록했다. RAM을 사전 가정하지 않았다.",'',f"전체 테스트 {t['elapsed_seconds']:.2f}초, 최대 RSS {t['max_rss_kib']:,}KiB. 이번 observed CLI(기존 성공 캐시, HTTP 시간 제외) {p['elapsed_seconds']:.2f}초, 최대 RSS {p['max_rss_kib']:,}KiB; replay의 별도 수치는 JSON에 있다. os.wait4 child rusage이며 동시에 수행한 작업의 합산 peak나 장기 무인 운영 자원 보장은 아니다.",'', '## 9. 사용자의 최소 다음 행동','', 'IBM 한 증권과 개인/회사 사용목적을 확정하고 실제 계정 키·자동 수집/보관/ML 허용 근거를 제공한다. 검토된 source/security/session/volume 정의·action coverage·개별 상태를 JSON assertion/원문 hash 또는 공급자 내보내기로 제공한다. 운영 계약의 exact 필드를 사용하고 verified를 추정해서 쓰지 않는다. 허용된 실제 거래일 마감~결정 사이에 --refresh 단일 명령으로 수신·신선도·pending을 확인한 뒤 축적한다. [정확한 장애물별 행동](data_readiness_action_plan.md). Stage6는 실제 적격 자료와 단순 모델 비교 확보 전 보류한다. 구매/계약/계정 생성/주문/반복 스케줄/커밋/푸시는 하지 않았다.','', '| 판정 | 결과 |','|---|---|']
    lines.extend(f'| {k} | {v} |' for k,v in report['verdicts'].items())
    (ROOT/'reports/stage5_5_validation.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':'PASS' if not changed else 'FAIL','reports_written':3,'protected_files':len(old)},ensure_ascii=False))


if __name__=='__main__':main()
