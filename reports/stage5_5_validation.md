# Stage5.5 검증 보고서

실행 근거: 2026-10-03T06:33:08.079361+00:00 · HEAD `fb78fc7d712dcd285f9e463ec3673fe732cdc0a5` · Linux aarch64. [JSON](stage5_5_validation.json)과 [상세 차단 표](data_blocker_matrix.md).

## 1. 시작 상태와 실제 변경

README·Stage5 보고/JSON·확보 계획·인계·출처/이용권·정책/selector/수집/manifest/특징/정답 코드와 실제 미커밋 작업 트리를 조사했다. 보고된 HEAD와 일치하며 Stage2~5 변경은 기존 사용자 작업으로 보존했다. observed scope/진단/review/단일 실행과16개 위험 테스트, 계약/CLI/보고·Linux 재현 명령을 추가했다. 별도 거래/회계 엔진이나 신경망은 추가하지 않았다.

## 2. 차단 원인별 변경 전·후

| 단위 | 전 | 후(기존 frozen 입력) |
|---|---:|---:|
| snapshot / 논리 sample |40 /4|40 /4|
| snapshot-특징 행 / 특징 version |80 /56|80 /56|
| 정답 version blocked / pending / ready |180 /60 /0|180 /60 /0|
| 논리 정답 blocked / pending / ready |9 /3 /0|9 /3 /0|
| 실제 학습 적격 sample |0|0|

전체 각 horizon80 version이며 총12 논리 sample-horizon이다. 이전 선택4 snapshot의8 특징·18 blocked/6 pending과 분모 차이를 명시했다. 이유별 overlap와 필드·정책은 차단 표/JSON을 참조한다. 신규 current lookback 진단은61세션, 신규 거래 sample/pending은0이다.

## 3. 코드로 해결한 문제와 외부 자료

현재 lookback/과거 observed 구분, explicit scope·학습 gate·검토 overlay, maturity 새 버전/실제 완료 시각, 다중 blocker 진단, 재실행/원본 보존을 연결했다. 기존 strict 의미와 학습 적격0을 유지했다. missing share identity·verified session/volume·action/state interval·ML 권한·역사 vintage/universe는 코드로 만들지 않았다. AAPL raw 시가/2025 endpoint도 미확보다.

## 4. 실제 요청·수신·특징·pending

공식 IBM compact demo에 새 HTTP 요청1회: HTTP200, 100행, 2026-05-12~2026-10-02. 수신 2026-10-03T05:47:00.714042+00:00, 처리 2026-10-03T05:47:00.749791+00:00. raw hash/receipt는 JSON에 기록했다. 후속 CLI는 성공 캐시 재정규화/캐시를 사용했으며 새 다운로드로 표현하지 않았다.

현재 cutoff 2026-10-03T06:28:08.778340Z에서 2026-10-02까지61연속세션·missing0·숫자 특징 산술 ready. 현재 받은 과거 입력이며 strict PIT/경제적 조정 수익률 증거가 아니다. 토요일 NON_TRADING_DAY라 거래 표본0·pending0을 유지했다. 금요일 과거 cutoff를 지정한 요청은 입력0/received_after_cutoff/MISSED_DECISION이었다. 실제 모델은 prediction_not_available다.

## 5. 재현·복구 검증

완료된 동일 cutoff CLI는 원본·query hash를 확인하고 idempotent_replay=true, 새 네트워크=false였다. 테스트는 정상 세션 pending3→성숙1/5일 ready2, 미래20일 버전/특징/기존 snapshot 불변과 손계산 open-to-open 일치를 확인했다. 이 자료는 임시 fictional HTTP/evidence fixture이며 실제 권한·표본이 아니다. raw 저장 후 crash·SQLite 오류·HTTP/파싱 실패의 기록/재개, 정정 후 원 snapshot 불변, 주말/휴장·late receipt/processing도 검증했다.

## 6. 실제 학습 가능 여부

40개 모두 strict selector0, 신규 두 제한 scope도 기존 자료 적격0이다. 선택4개 config의 검사/학습8요청은 종료3/BLOCKED, strict_dataset_has_no_eligible_samples로 거부됐다. 실제 학습·예측·수익률·Sharpe·적중률을 생성하지 않았다. actual fit에는 이용권/필드/시각/성숙 계약과100표본/180일·purge 후60/20/20·분류 양쪽 클래스가 필요하다. 작은 축적 성공으로 기준을 낮추지 않는다. 엄격한 역사 평가는 BLOCKED, 실제 예측력 NOT_EVALUATED다.

## 7. 기존 테스트·빌드·보존

전체 180/180 PASS; 이후 scope→Stage5 특징 목록 gate/실패 기록 강화의16개 집중 검사 PASS. wheel48개 Python 모듈 소스 byte 일치, offline 설치·48모듈 import·observed CLI PASS. pip check/diff check PASS. 최초 inventory 3733개 기존 데이터 파일 내용 불변, 실제40 frozen replay와 Stage2 fixed352행 재생 PASS. 기존 migration 거부 테스트의 SQLite ResourceWarning은 남아 있으며 실패는 아니다. 테스트 합성 흐름은 엔진 연결 검증이며 실제 예측력 평가와 구분한다.

## 8. 현재 Linux 자원

Cortex-A76 4코어, Python3.13.5, RAM 8,453,963,776바이트, swap 2,147,467,264바이트. 캡처 시 사용 가능 RAM 5,770,231,808바이트, 저장공간 여유 99,832,356,864바이트. 의존성/커널·전체 디스크는 JSON에 기록했다. RAM을 사전 가정하지 않았다.

전체 테스트 247.97초, 최대 RSS 237,456KiB. 이번 observed CLI(기존 성공 캐시, HTTP 시간 제외) 2.31초, 최대 RSS 160,656KiB; replay의 별도 수치는 JSON에 있다. os.wait4 child rusage이며 동시에 수행한 작업의 합산 peak나 장기 무인 운영 자원 보장은 아니다.

## 9. 사용자의 최소 다음 행동

IBM 한 증권과 개인/회사 사용목적을 확정하고 실제 계정 키·자동 수집/보관/ML 허용 근거를 제공한다. 검토된 source/security/session/volume 정의·action coverage·개별 상태를 JSON assertion/원문 hash 또는 공급자 내보내기로 제공한다. 운영 계약의 exact 필드를 사용하고 verified를 추정해서 쓰지 않는다. 허용된 실제 거래일 마감~결정 사이에 --refresh 단일 명령으로 수신·신선도·pending을 확인한 뒤 축적한다. [정확한 장애물별 행동](data_readiness_action_plan.md). Stage6는 실제 적격 자료와 단순 모델 비교 확보 전 보류한다. 구매/계약/계정 생성/주문/반복 스케줄/커밋/푸시는 하지 않았다.

| 판정 | 결과 |
|---|---|
| engine_connection | PASS |
| actual_data_access | PASS_OFFICIAL_IBM_DEMO_ONLY |
| current_observed_lookback | PASS |
| current_operational_sample | DEFERRED_NON_TRADING_DAY |
| real_training_readiness | BLOCKED |
| strict_historical_evaluation | BLOCKED |
| real_predictive_power | NOT_EVALUATED |
