# 4단계 실행 검증

엔진 정확성 **PASS**, 실제 자료 준비 **BLOCKED**, 실제 역사적 성능 **BLOCKED**.
모델 학습·튜닝·실제 수익성 검증·자동매매·커밋·푸시는 수행하지 않았다.
현재 실행 근거는 [stage4_validation.json](stage4_validation.json)이다.

## 1. 시작 상태와 변경

checkout `/home/sechi/Stk-ia`, origin `mingue0425-sys/skt-ia`, main HEAD
`fb78fc7d712dcd285f9e463ec3673fe732cdc0a5`를 직접 확인했다.
2·3단계 코드/계약/테스트/보고서는 미커밋 변경 및 untracked 파일에 있었다.
README, stage3_validation md/json, stage4_handoff, 특징·정답·dataset·조회 계약,
replay/iterator/training_selection, snapshot 구현과 테스트를 읽었다. 적용 AGENTS.md는 없었다.
기존 108개 테스트를 먼저 재실행해 실패/오류/건너뜀 0을 확인했다.

Stage4는 새 `src/market_research/stage4/`의 입력 검사·분할·예측 계약·지표·고정 tape·회계·실행
저장·CLI, 합성 fixture/37개 관련 테스트, 준비/검증 스크립트와 계약/인계 문서를 추가했다.
기존 파일 변경은 CLI 명령 연결과 README 확장이다. 빌드가 egg-info/SOURCES.txt를 갱신했다
(gitignored 생성 메타데이터). 원 Stage2 DB, Stage3 DB·구현·고정 자료·이전 인계/보고서는 보존했다.
초기 보호 파일 SHA-256 목록은 로컬 `data/stage4/starting_state.json`에 있다.
초기 목록은 디렉터리 단위여서 root README hash는 없고 시작 시 읽은 내용과 변경 행위로 기록했다.

## 2. 환경·명령·테스트·빌드

Python 3.13.5, Linux aarch64, SQLite 3.46.1.
exchange_calendars 4.13.2, numpy 2.5.3, pandas 3.0.6.
최종 실행 UTC `2026-10-02T15:42:44`~`15:43:34`(한국 시각 10월 3일), 약 50.37초.
네트워크 요청·다운로드가 없었다.

- 전체 **145/145 통과**, 실패 0·오류 0·건너뜀 0. 기존 108개와 새 37개 포함.
- CLI/준비/빌드/diff 명령 **17개 검사 통과**. 부분 2, 오류 1, 부적격 3도 예상 코드와 일치.
- wheel **PASS**, 현재 Python 모듈 **37개 byte 일치**, 누락 0. `git diff --check` 통과.
- 기존 실패 migration 테스트의 SQLite 객체에서 실제 ResourceWarning 1건이 관찰됐다.
  실행 실패가 아니며 Stage4 검증 보고서에 남겼다. 추적으로 `tests/test_pit.py:410`의 예상 migration 거부에서 생성자가 SQLite를
  닫지 않는 기존 경로임을 확인했다. 추적 검사 1개도 통과했고 Stage4 차단 결함이 아니므로
  기존 Stage2 구현은 변경하지 않았다.

실제 실행한 주요 명령은 README와 JSON commands에 있다.
`scripts/validate_stage4.py`가 전체 테스트·CLI·이전 자료 replay·실제 거부·wheel 비교를 수행했다.
runtime venv에 setuptools가 없어 첫 no-build-isolation wheel 명령은 실패했다.
설치된 시스템 Python/setuptools 78.1.1의 build_wheel로 오프라인 빌드해 해결했다.
최초 새 테스트 29개 중 두 기대값 오류는 canonical timestamp 표현 및 보유 중 신호 보류를
기대값에 반영해 수정했다. 이 두 오류와 빌드 실패를 현재 PASS로 숨기지 않고 JSON에 기록했다.

## 3. 합성 시간순 검증

expanding / rolling / explicit 날짜 구간과 같은 날짜의 공통 배치를 검사했다.
실제 entry/exit의 닫힌 정답 구간을 사용해 경계 접촉까지 purge하고, asof 이후 성숙·미래 정정
정답·decision 이후 준비 특징을 제외했다. 빈 fold/부족 표본은 성능을 생성하지 않는다.
이번 전체 흐름은 training_asof `2024-04-10T00:00:00Z`, train 5·validation 2·test 4다.
처음 계획한 train 날짜 6개 중 마지막 정답은 asof 미성숙이라 제외됐다.
한 horizon 과제별 정책을 유지하며 20일 미성숙을 1일 선택에 섞지 않았다.

별도 fixed 수작업 예측 4개는 실제 계산 시각과 과거 simulated_generated_at을 구분한다.
중복/상충 ID·dataset 불일치·범위 밖 확률·crossed quantile·oracle 일반 성과 혼입을 거부한다.
늦은 생성 기록은 예정 진입을 차단한다. MAE/MSE/방향/Brier/log loss/날짜별 IC/보정표/
pinball/구간 포함률, 날짜 평균 대 행 평균, 동점·상수·결측·pending의 null/제외를 손계산했다.
이번 1종목 날짜별 IC는 정의되지 않아 null이다. 지표로 통계적 유의성이나 투자 성능을 주장하지 않는다.
같은 기업행동 event_key의 상충 frozen 정정 버전 거부, loss overflow의 null 처리,
추가 계약 검사 후의 최종 적격 membership 유지도 회귀 검사했다.

## 4. 합성 회계·체결·비용

상수 raw 가격·비용 0에서 부 생성 없음, 매수/매도/수수료 cash 대조, 상승/하락 NAV와 낙폭,
보유/주문 split 조정·단주 보존, ex 권리/미수금/지급 중복 방지, 부분/거절/만료·보유 신호 보류,
정지/종료 누락/폐지 대가 누락, embedded 비용의 한 번 반영, prior volume만 크기 산정,
1/5/20 exit 인덱스, 미체결 및 말단 미청산 보존을 독립 기대값으로 확인했다.

전체 fixed 흐름은 실제 Stage3 replay → split → 선택 검사 → 별도 예측 → 주문/체결 →
split/dividend 권리·지급 → NAV/지표 → 동일 결과 재현이다.
초기 1,000, 첫 진입 9주 → 2:1 split 후 18주, 배당 권리 36, 다음 진입/청산 19주.
직접 수수료 4.00, 체결 차이 3.70(스프레드 1.85+슬리피지 1.85)로
**1,000+36−4−3.70=최종 현금 1,028.30**과 일치한다. 체결 4건, action ledger 3건,
미청산 0. 불리한 합성 비용의 최종 현금은 991.00, 동일 회계 규칙의 cash 비교는 1,000.00이다.
별도 단위 사례는 비용 증가로 체결 수량이 10→9주가 되는 차이도 검사했다.
모두 가상 경로/가정 비용의 계산 검증이며 실제 예상 수익률/비용 추정이 아니다.

## 5. 재현·회귀

dataset ID/hash `6413bea3a2ffd8df9ef69922b9bcb9f8406d764d54f8b7f524661d165cb2e6a8`.
result hash `fb60a4be4f3bee3e49a90ffc87390110681ea74aa70cf92029a95e4303dfd332`.
run ID `f9d6eb9fd5819638da80e67aa83da395af88675d8da805480d0d011c836476d2`.
새 fixture를 다른 경로에서 생성할 때의 ID와 이 고정 입력의 반복 재생을 구분한다.
실행 시각은 별도 메타데이터이며 결과 내용 hash가 재실행과 일치했다.
코드 hash `caf8a77d32184c168cba0b021e79f692c71846c83023ea0c2aa2abbd41af2644`.

기존 실제 fixed dataset **40개**, 기존 합성 **7개**를 원 파일 검증과 함께 모두 replay했다.
원 Stage2 snapshot **352행**을 재검증했다. Stage2·Stage3 DB bytes가 불변이고 migration이 없다.
기존 108개 회귀도 통과했다. 시장 원본/행 단위 자료는 새 공개 파일에 추가하지 않았다.

## 6. 실제 적격성·차단

새 query나 현재 자료 보완을 수행하지 않고 기존 네 개의 고정 실제 dataset을 검사했다.
frozen label 평가 asof `2026-10-02T14:31:42.659568Z`를 명시적으로 사용했다.
고정 상태를 현재 실행 시각에 맞춰 재계산하거나 pending을 임의 ready로 바꾸지 않았다.
현재 직접 확인한 특징 **blocked 8**, 정답 **blocked 18·pending 6·ready 0**, 엄격 학습 선택 **0**.
네 strict 실행은 각각 **BLOCKED/종료 3**, metrics와 simulation은 null이다.

집계 source 이유: no_computable_price_features 8, no_eligible_feature_inputs 8,
opening_prices_unavailable_close_only 12, security_trading_status_unverified 6,
target_open_not_yet_reached 6. 이는 복수 사유 집계이고 학습 exclusion 합과 별개다.
query 근거에는 실제 receipt 미증명/과거 cutoff 이후 수신, 공개 timestamp 미상,
가격 의미 제외가 있다. 식별자 공백·권한 unconfirmed·운영 준비/PIT 미충족을 별도 집계했다.
query별 정확한 count는 JSON datasets에 있다. 이전 AAPL/IBM 현재 수신 특징 계산은
이번에 반복하지 않았으며 과거 학습 표본으로 사용하지 않았다.

## 7. 실제 역사적 검증

**BLOCKED**. 실제 Sharpe/수익률/적중률을 생성하지 않았다.
권한·영구 ID/당시 universe·원본 버전 공개 근거·원 OHLCV/세션 의미·action/state 완전성·
실제 준비 기록·역사 달력 근거가 필요하다. 합성 엔진 PASS를 이 조건의 PASS로 승격하지 않는다.

## 8. 5단계 범위

합성 fixed 입력으로 기준선 모델의 데이터 연결·시간순 학습/예측 저장·누수 검사를 구현할 수 있다.
research는 명시적 조건부 진단만 가능하다. 실제 모델 학습/성능 주장은 아직 BLOCKED다.
정확한 인터페이스·예측 필드·단일 horizon·frozen 버전·asof·코드 hash는
[stage5_handoff.md](stage5_handoff.md)에 인계한다.

## 9. M2 및 미검증

M2 실기기는 **미실행/BLOCKED**. 이번 Linux ARM64 통과를 M2 8GB 보장으로 표현하지 않는다.
M2 설치/동일 fixture·hash/UTC-DST/메모리·속도·패키지 wheel을 확인해야 한다.
복수 증권 실제 통합 tape, 실제 시가 경매·시장 충격, 결제 시차, 복잡한 합병/폐지 대가,
공동 다중 horizon 모델, 통계적 유의성 검정, 실제 수익성은 이번 검증 범위 밖이다.

| 판정 대상 | 결과 |
|---|---|
| 엔진 정확성(지원 계약·합성 경로) | PASS |
| 실제 자료 준비 | BLOCKED |
| 실제 역사적 성능 | BLOCKED |
| 5단계 합성 기준선 연결 | PASS |
| 명시적 연구 모드 | CONDITIONAL |
| M2 실기기 | BLOCKED |
