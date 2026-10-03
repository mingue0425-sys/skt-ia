# 3단계 특징·정답·데이터셋 검증

시작 commit `fb78fc7d712dcd285f9e463ec3673fe732cdc0a5`, checkout `/home/sechi/Stk-ia`, main. 적용 AGENTS.md 없음.
시작 작업 트리의 기존 미커밋 산출물을 보존하고 3단계 구현을 재사용·보완했다. 최초 원본과 Stage2 DB는 변경하지 않았다.
PIT migration 1:2 유지, 별도 dataset SQLite migration 1 및 특징/정답/고정 membership을 추가했다.

## 실제 실행

시작 시 기존 103개를 재실행한 뒤 전체 **108/108 통과**. 실패 0, 오류 0, 건너뜀 0.
초기 close-only 합성 fixture 한 건은 importer가 받지 않는 price_kind 필드를 사용해 실패했다. 실제 adjusted_close 스키마로 fixture를 고쳐 재검증했다.
필수 20개 위험과 독립 손계산 oracle, 비상수 ddof/volume 분모, null/0, 근거/달력 hash 변조를 검사했다.
계약·특징·정답 생성/갱신·training_asof·dataset build/verify/JSONL replay·집계 보고 CLI를 실제 실행했다. 명령과 종료 코드는 JSON에 있다.

## 합성·실제 구분

합성은 ready 정답 3개, 진단용 선택 3개, strict 학습 선택 0개다. 실제 자료 확보를 입증하지 않는다.
실제 AAPL에는 split-adjusted OHLCV와 close_only 조정 종가가 둘 다 있다. 이번 AAPL 특징 진단은 **close_only만** 사용해 OHLC/volume을 채우지 않았다.

## 이번 재검토의 보완

기존 103개 테스트가 통과한 상태에서 추가 회귀 사례 네 가지의 실패를 직접 재현한 뒤 수정했다.
진입 전 선언만 있고 효력일/배당락일이 없는 action을 차단하고, 과거 label_asof의 정상 대기를 나중 simulation_asof가 오염시키지 않게 했다.
잘못된 수치 입력은 feature/sample invalid로 전파하며 보유수량·현금·수익률 overflow를 invalid로 반환한다. 처리 대기 중 정답은 holdings 숫자도 export하지 않는다.
특징·정답·dataset 정의는 3.0.1이고 기존 3.0.0 snapshot을 재계산하거나 덮어쓰지 않는다.
PIT·수집기·2단계 보고서/인계 파일 12개의 원래 hash가 유지됐다. 새 회귀 테스트는 5개다.
실제 의사결정 표본 특징 상태 {'blocked': 8}, 기간별 정답 상태 {'blocked': 18, 'pending': 6}.
2026년 받은 과거 가격을 과거 observed 특징으로 배치하지 않아 decision 특징/ready 정답/학습 적격은 0이다. 미래 미도래와 과거 자료 누락을 분리했다.
- AAPL 현재 수신 진단: 61세션, 수학적 특징 ready 9, unavailable 3; **과거 decision 표본/학습 데이터 아님**.
- IBM 현재 수신 진단: 61세션, 수학적 특징 ready 12, unavailable 0; **과거 decision 표본/학습 데이터 아님**.
원래 Stage2 snapshot 352행 재확인, Stage2 DB bytes 불변 True, 무결성 PASS.
기존 고정 dataset 36개도 replay 검증했다. 작업 PIT 복사본은 재사용하며 다시 덮어쓰지 않는다.
초기 검증 스크립트의 반복 backup이 임시 query metadata를 지운 문제를 발견·수정했다. 원본/Stage2 DB는 영향 없고 원 cutoff/policy/config와 원본 버전으로 query hash 48개가 정확히 일치할 때만 복원했다. 고정 feature/label 값은 변경하지 않았다.

## 판정

| 항목 | 판정 |
|---|---|
| feature_label_dataset_engine | **PASS** |
| actual_sample_processing | **PASS** |
| historical_pit_readiness | **BLOCKED** |
| learning_data_readiness | **BLOCKED** |
| stage4_scope | **CONDITIONAL** |
| m2_hardware | **BLOCKED** |

엄격한 역사 공개 버전·영구 ID/폐지/코드 이력·기업행동 완전성·증권 거래 가능 상태·학습 권한은 미확정이다.
reference calendar는 공식 과거 공지와 다르다. 총수익률의 ready 합성 사례를 실제 지급/폐지 자료 확보로 해석하지 않는다.
feature ready/label processing 시각은 기본 명시 가정이고 실제 운영 처리 기록과 구분한다. 계산 가능/PIT/실행/learning을 단일 PASS로 합치지 않는다.
환경 Python 3.13.5, Linux aarch64, SQLite 3.46.1. M2 실기기는 실행하지 않았다.

## 재현·인계

```bash
.venv/bin/python scripts/verify.py
.venv/bin/python scripts/prepare_synthetic_stage3.py
.venv/bin/python scripts/validate_stage3.py
```

README의 개별 CLI 명령과 reports/stage4_handoff.md의 고정 version 입력 계약을 따른다. 모델 학습·투자 수익률 검증·자동매매는 구현하지 않았다.

## 추가 배포·고정 버전 검사

실제 고정 dataset 40개와 합성 고정 dataset 7개를 원본 hash 검증과 함께 모두 replay했다.
현재 소스로 wheel을 빌드하고 포함된 Python 모듈 28개가 작업 공간 소스와 byte 단위로 일치함을 확인했다.
공개 후보 파일 72개를 검사해 시장 원본·DB·행 결과와 토큰 패턴이 포함되지 않음을 확인했다. git diff --check도 통과했다.
