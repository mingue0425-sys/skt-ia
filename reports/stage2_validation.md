# 2단계 저장·조회 엔진 검증

시작 commit: `fb78fc7d712dcd285f9e463ec3673fe732cdc0a5`. 시작 작업 트리는 clean이고 적용 AGENTS.md는 없었다.
기존 README·1단계 보고서·인계·정규화 스키마·연구 계약·reports/cross_provider_review.md와 코드를 비교했다.
현재 구현은 SQLite migration 1·2, 원본 참조/수신 사건/정규화 버전, 세 시간 정책, 충돌 처리, 고정 snapshot이다.
외부 계약·새 계정·모델·특징/정답 계산·수익성·자동매매는 수행하지 않았다.

## 실제 실행 결과

- 기존 31개를 포함한 오프라인 테스트 **72/72 통과**, 실패 0, 오류 0, 건너뜀 0.
- tests/fixtures/stage2_expected.json의 독립 예상 값과 비교했다. 17개 필수 사례에 더해 버전 근거 불일치·normalizer 충돌·제공처 불일치·비밀키 격리·늦게 도착한 원본 연결을 검사했다.
- 실제 적재: manifest 76, 수신 사건 41, 정규화 artifact 62.
- DB 가격 버전 33076, 기업행동 버전 89, 달력 행 1254. 버전 수는 독립 거래일 수가 아니다.
- observed **352행**, historical_verified **0행**. 실제 cutoff와 제외 사유는 JSON 보고서에 있다.
- 무결성 PASS, 재적재 멱등성 True, 실제 snapshot 재현 일치 True.
- strict 0행은 제한을 적용한 정상 빈 조회이며 검증된 역사 자료 확보 PASS가 아니다.
- 일반 python3 진단은 패키지 미설치로 한 번 실패했고, 설치된 .venv/bin/python으로 재실행해 해결했다.

## 실제 자료와 증거 한계

기존 로컬 원본을 먼저 실제 발견했고 원본을 변경하지 않았다. 1단계 자료에는 초기 manifest의 network flag/처리 완료 시각/정규화 byte hash 누락이 존재한다.
network flag가 없는 22건은 actual receipt unproven으로 observed에서 제외한다. 정규화 byte hash 없는 17건은 파일명의 canonical 내용 해시와 provenance를 확인하고 byte hash는 새로 계산했으며 별도 검증 수준으로 남겼다.
처리 완료 시각이 없는 자료의 parsing_completed_at은 null이다. 사용 가능 시각에는 이번 import의 실제 파싱/검증 완료를 사용하고 recorded_at이나 2024년으로 소급하지 않는다.
단일 보통주 IBM demo의 full 과거 응답은 다른 종목의 접근/폐지군/당시 공개 버전을 입증하지 않는다. 수집 실패 본문은 receipt만 저장한다.
Yahoo OHLC는 split_adjusted이고 현재 adjclose는 total_return_adjusted close_only로 분리했다. 이를 원가격·총수익률·당시 현금 배당으로 환산하지 않는다.
현재 Nasdaq directory는 유효 시작일 없는 현재 assertion이며 과거 ticker mapping/투자 universe로 조회하지 않는다.
규칙 달력은 공식 공지 버전이 아니다. 기본 XNYS 정규장 경계는 참고 가정이며 provider 세션/volume 포함 범위를 입증하지 않는다.
실제 historical publication evidence는 없다. 증거 인터페이스가 확인하는 것은 hash와 버전/시각 결합이며 upstream 근거 진위와 라이선스 검토는 별도 의무다.

## 판정

| 항목 | 판정 | 근거 |
|---|---|---|
| storage_version_query_engine | **PASS** | offline expected-value cases, CLI, integrity, idempotence and frozen replay |
| actual_import | **PASS** | existing bytes/hash/provenance checks; source failures remain failure receipts |
| observed_after_receipt | **PASS** | eligible actual receipt plus usable_at and completed observation interval; temporary/provider labels explicitly allowed |
| historical_verified_readiness | **BLOCKED** | current market sample lacks version-bound historical public timestamps; empty result is not data-readiness PASS |
| identifiers_and_price_meanings | **CONDITIONAL** | temporary source labels, vendor session/volume basis unverified; Yahoo split-adjusted, IBM demo as-traded, adjclose close-only |
| stage3_scope | **CONDITIONAL** | synthetic interface development and licensed limited observed sample processing; strict historical feature/label validation remains blocked |
| m2_hardware | **BLOCKED** | no actual Apple Silicon M2 access; Linux ARM64 execution is not M2 verification |

## 별도 실제 네트워크 실행

- yahoo: HTTP 200, success, 기간 {'end': '2024-12-31', 'start': '2024-01-02'}, 개수 {'actions': 4, 'bars': 252, 'directory': 0, 'documents': 0}.
- alpha_vantage: HTTP 200, success, 기간 {'end': '2026-10-01', 'start': '2026-05-11'}, 개수 {'actions': 0, 'bars': 100, 'directory': 0, 'documents': 0}.

## 실행 명령

각 명령의 반환 코드와 예상 반환 코드는 stage2_validation.json에 보존했다. 네트워크 검증은 별도의 smoke 실행이다.

```bash
.venv/bin/python scripts/verify.py
.venv/bin/python scripts/make_synthetic_stage2.py
.venv/bin/python -m market_research.cli db-init --db tests/generated/cli-stage2.sqlite
.venv/bin/python -m market_research.cli db-import --db tests/generated/cli-stage2.sqlite --data-dir tests/generated/stage2 --input-domain synthetic
.venv/bin/python -m market_research.cli smoke --config configs/stage2_smoke.json --data-dir data/stage2_collection --refresh --output data/stage2-smoke.json
.venv/bin/python scripts/prepare_stage2_calendar.py
.venv/bin/python scripts/validate_stage2.py --data-dir data/stage2_collection --calendar data/reference_calendar.json --smoke-report data/stage2-smoke.json
```

이번 검증의 모든 실제 입력 경로/기간/개수는 JSON 보고서에 있다. 공개 checkout은 원본이 없으므로 먼저 허용된 작은 샘플을 수집하거나 기존 로컬 루트를 지정한다.
DB/가격행/snapshot payload는 data/에만 보관했다. 보고서에는 집계만 포함한다.

## 3단계 인계

reports/stage3_handoff.md의 query/snapshot 입력 계약을 따른다. 임시 ID와 표본 처리는 허용하지만 엄격한 역사적 모델/수익 검증을 시작할 근거는 확보하지 못했다.
실행 환경은 위 JSON의 Python/Linux ARM64다. M2 8GB는 미실행이며 README의 동일 명령으로 재현하고 메모리/속도/시간대/SQLite 결과를 비교해야 한다.

## 추가 실행 확인

현재 소스로 wheel 빌드를 실행했고 포함된 PIT 모듈 8개가 소스와 byte 단위로 일치했다.
스키마 1에서 만든 실제 252행 snapshot을 migration 2 이후 다시 검증해 기존 내용 hash가 유지됨을 확인했다.
공개 후보 파일 51개에서 토큰 패턴·원본/정규화/DB/행 단위 결과 포함 여부를 점검했고 git diff --check가 통과했다.
빌드·기존 snapshot 검증 명령과 hash는 JSON의 additional_execution_checks에 보존했다.
