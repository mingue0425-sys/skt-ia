# 금융 데이터 확보·시간순 검증·계좌 시뮬레이션 — 1·2·3·4·5·5.5단계

실제 데이터 수집·시점 계약·모델 학습·고정 예측 평가를 연결하는 작은 Python 연구 엔진이다.
5단계는 합성 기준선 학습을, 5.5단계는 실제 차단 진단과 앞으로의 observed 축적을 연결한다.
별도의 [실제 데이터 기준선 실험](reports/real_data_baseline.md)은 제한된 개인 연구 경로를 사용한다.
엄격한 역사 PIT 학습은 계속 BLOCKED이며 운영 거래는 구현하지 않는다.
[최종 판단](reports/stage1_data_feasibility.md),
[품질 요약](reports/sample_quality.summary.json), [2단계 인계](reports/stage2_handoff.md)를 먼저 본다.

최초 조사와 실제 수집은 별도 로컬 작업 공간에서 실행했다. 이 저장소에는 코드·설정·문서·
합성 테스트 fixture와 집계 검증 보고서를 포함한다. 이용 조건이 확정되지 않은 시장 데이터
원본·정규화 파일·행 단위 품질 결과는 포함하지 않는다. 상세 결과는 로컬 수집 후 생성한다.
Python3.13.5/Linux ARM64가 현재 주 실행 환경이다. 실제 CPU·RAM·저장공간과 측정 자원은
[5.5단계 검증](reports/stage5_5_validation.md)에 기록한다. 서비스·GPU·분산 프레임워크는 필요하지 않다.

```bash
git clone https://github.com/mingue0425-sys/skt-ia.git
cd skt-ia
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/verify.py
.venv/bin/python -m market_research.cli collect
.venv/bin/python -m market_research.cli collect --config configs/evidence_supplement.json --output reports/execution-dividend-evidence.json
.venv/bin/python -m market_research.cli smoke --refresh --output reports/execution-smoke.json
.venv/bin/python -m market_research.cli validate
.venv/bin/python scripts/audit_artifacts.py
```

새 설치에서 이번과 같은 의존성 버전이 필요하면 Python3.13 환경에서
`.venv/bin/python -m pip install -r requirements-lock.txt` 후 `pip install -e .`를 실행한다.
lock은 OS용 wheel을 지정하지 않는다. 현재 Linux ARM64 설치·검증 환경을 기준으로 한다.
Python>=3.11 설치도 허용하지만 동일 버전 lock으로 검증한 환경은3.13이다.

collect/smoke 종료코드: 0=모든 요청 성공/캐시/오프라인 재정규화, 2=일부 접근 실패를
기록하고 나머지 완료. validate:0=구조 검사 통과,1=구조 오류. 데이터 의미·PIT·권한은
별도 판정이다. 전체 collect는 FB/ATVI/Stooq/SEC/틀린 초기 달력 URL의 실패가 기록되어
현재2가 정상적으로 예상된다. 성공 원본을 다시 받지 않고 실패 요청만 재시도한다.
smoke는 AAPL/IBM demo/Nasdaq directory의3요청이며 --refresh가 있어야 실제 네트워크를
다시 확인한다. 단위 테스트는 원본 없이 오프라인으로 실행한다. validate/캐시 audit은 먼저 로컬에서
자료를 확보해야 하며, 확보한 캐시가 있으면 인터넷 없이 실행할 수 있다.

`configs/sample.json`은 검증용6개 보통주(AAPL/MSFT/NVDA/IBM/TSLA/META)와 비교용SPY,
별도 rename/merger probe 및 공식 증거 요청이다. 과거 투자Universe가 아니다.
Yahoo chart는 공식 지원 API 계약 미확인. Alpha Vantage는 공개 IBM demo 예제만 사용했다.
개인 키를 쓰려면 config의 auth_mode를 environment로 바꾸고
ALPHAVANTAGE_API_KEY 환경변수로 전달한다. 키는 config·CLI 인자에 쓰지 않는다.
이 옵션은 이용 권한을 자동 생성하지 않는다.

`data/raw`, `data/normalized`, `data/manifest`는 원본·제공처별 정규화·수신기록이다.
manifest SHA-256이 실제 파일명을 연결한다. `data/derived/alpha_ibm_sample.json`은
전체 IBM 응답에서2024년 및2026년 비교 구간만 선택한 샘플이다. 원본 full response는
그대로 보관했고 모든 과거행의 품질 검사를 완료했다고 주장하지 않는다.
`data/derived/calendar.json`은 library rules 기반 reference, 실제 시장 데이터가 아니다.
tests/fixtures는 합성 테스트 전용이며 data 디렉터리에 섞지 않는다.

timeout20초, 최대3시도, backoff1/2초와 Retry-After, 순차2초 간격(Alpha12초),
Alpha25/UTC일 로컬budget, HTTP401/403/429/404/HTML challenge/empty/schema failure 구분,
원자적 파일 저장, 성공 캐시 재사용, 수정응답 새 hash 버전, 처리/수신 시간 구분을 구현했다.
단일 수집 프로세스를 실행한다. 실패 원문을 지우거나 오류가격을 자동 삭제하지 않는다.

약관이 불명확한 공개 샘플의 장기 보관·모델 학습·재배포 허용을 추정하지 않는다.
샘플은 현재 로컬 검증에만 사용했고 data는 git에서 제외했다. 전체 역사 자료 수집과
모델 학습에 앞서 [제공처 비교](docs/provider_comparison.md)의 접근·권한·시각 공백을 해결한다.

## 2단계: SQLite 버전 저장과 시간 정책 조회

[검증 보고서](reports/stage2_validation.md), [집계 JSON](reports/stage2_validation.json),
[스키마](docs/local_store_schema.md), [시간 정책](docs/time_query_policies.md),
[3단계 인계](reports/stage3_handoff.md)를 참고한다. historical_verified 실제 자료는 여전히
BLOCKED이며 엔진 테스트의 PASS와 구별한다. DB/시장 원본/행 단위 snapshot은 git에서 제외한다.
현재 checkout의 코드·테스트 결과는 stage2 보고서가 기준이고 stage1 보고서는 최초 실행 기록이다.

기존 설치 명령을 실행한 뒤 아래 오프라인 예제를 실행할 수 있다. **SYNTHETIC_TEST_ONLY**이며
실제 시장 다운로드가 없다. 실제 가격과 다른 파일/DB/domain을 사용한다.

```bash
.venv/bin/python scripts/verify.py
.venv/bin/python scripts/make_synthetic_stage2.py
.venv/bin/python -m market_research.cli db-init --db tests/generated/demo.sqlite
.venv/bin/python -m market_research.cli db-import --db tests/generated/demo.sqlite --data-dir tests/generated/stage2 --input-domain synthetic
.venv/bin/python -m market_research.cli db-query --db tests/generated/demo.sqlite --cutoff 2024-01-03T22:00:00Z --policy historical_verified --input-domain synthetic --symbols SYN --allow-temporary --symbol-mode provider_label --require-data
.venv/bin/python -m market_research.cli snapshot-create --db tests/generated/demo.sqlite --cutoff 2024-01-03T22:00:00Z --policy historical_verified --input-domain synthetic --symbols SYN --allow-temporary --symbol-mode provider_label --output tests/generated/snapshot-meta.json
stage2_snapshot_id=$(.venv/bin/python -c 'import json; print(json.load(open("tests/generated/snapshot-meta.json"))["snapshot_id"])')
.venv/bin/python -m market_research.cli snapshot-verify --db tests/generated/demo.sqlite --snapshot-id "$stage2_snapshot_id"
.venv/bin/python -m market_research.cli snapshot-replay --db tests/generated/demo.sqlite --snapshot-id "$stage2_snapshot_id" --output tests/generated/fixed-query.json
.venv/bin/python -m market_research.cli db-audit --db tests/generated/demo.sqlite
```

날짜만 알려진 자료의 탐색 예제는 아래와 같다. 원래 publication 필드를 수정하지 않고
가정을 기록하며 결과와 snapshot에 NOT_STRICT_PIT를 표시한다.

```bash
.venv/bin/python -m market_research.cli db-query --db tests/generated/demo.sqlite --cutoff 2024-01-04T06:00:00Z --policy research_assumed --input-domain synthetic --symbols DATED --allow-temporary --symbol-mode provider_label --assumption-rule tests/generated/stage2/assumption_rule.json
```

현재 접근 범위의 두 작은 실제 요청을 새로 확인하고 로컬 집계 보고서를 생성하려면:

```bash
.venv/bin/python -m market_research.cli smoke --config configs/stage2_smoke.json --data-dir data/stage2_collection --refresh --output data/stage2-smoke.json
.venv/bin/python scripts/prepare_stage2_calendar.py
.venv/bin/python scripts/validate_stage2.py --data-dir data/stage2_collection --calendar data/reference_calendar.json --smoke-report data/stage2-smoke.json
```

validate_stage2는 다운로드를 수행하지 않는다. 기존 실제 루트가 있으면 --data-dir을 반복해
함께 지정할 수 있다. 공개 checkout에는 기존 원본이 없어 동일 과거 snapshot ID/행 수가
나온다고 보장하지 않는다. actual smoke가 실패하면 HTTP/정규화 결과와 코드 오류를 구별한다.

수동 DB 작업 예제에서 cutoff는 **실제로 수신·사용 가능해진 뒤의 UTC 시각**으로 지정한다.
아래 12:00Z는 예제이며 실행일/수신 완료에 맞춰 바꾼다.

```bash
.venv/bin/python -m market_research.cli db-init
.venv/bin/python -m market_research.cli db-import --data-dir data/stage2_collection --calendar data/reference_calendar.json
.venv/bin/python -m market_research.cli db-query --cutoff 2026-10-02T12:00:00Z --policy observed --symbols AAPL,IBM --start 2024-01-02 --end 2026-10-01 --price-semantics as_traded,split_adjusted --allow-temporary --symbol-mode provider_label --require-data --output data/observed-query.json
.venv/bin/python -m market_research.cli db-query --cutoff 2024-12-31T22:00:00Z --policy historical_verified --symbols AAPL,IBM --price-semantics as_traded,split_adjusted --allow-temporary --symbol-mode provider_label --output data/historical-query.json
.venv/bin/python -m market_research.cli db-report --cutoff 2026-10-02T12:00:00Z --policy observed --symbols AAPL,IBM --allow-temporary --symbol-mode provider_label --price-semantics as_traded,split_adjusted --output data/db-report.json
```

snapshot-create는 db-query와 같은 조회 인자를 받는다. 고정 snapshot-replay는 저장된 version을
읽고 source hash까지 확인하며 현재 DB로 query를 다시 하지 않는다. 빈 읽기는 JSON EMPTY/0,
--require-data로 0행을 검증하면 종료1이다. --require-unambiguous는 충돌을 종료1로 바꾼다.
DB 적재의 격리는 JSON PARTIAL/2이며 source 실패 receipt의 적재 PASS를 다운로드 PASS로
해석하지 않는다. source priority와 정확한 역사 버전 근거를 임의로 만들어 사용하지 않는다.

현재 Linux ARM64에서 설치/시간대/메모리/DB/hash를 검증한다. 특징·정답 생성은 아래 3단계,
모델·평가 연결은 5단계, 실제 observed 축적은 5.5단계에 있다.

## 3단계: 특징·정답·고정 데이터셋

[특징 계약](docs/feature_contract.md), [정답 계약](docs/label_contract.md),
[데이터셋 계약](docs/dataset_contract.md), [실행 검증](reports/stage3_validation.md),
[집계 JSON](reports/stage3_validation.json), [4단계 인계](reports/stage4_handoff.md)를 참고한다.
PIT migration 1:2를 유지하고 별도 dataset SQLite migration 1을 사용한다.
새 단계의 현재 테스트는 stage3 보고서가 기준이며 stage2 기록은 최초 검증 증거다.
현재 특징·정답·dataset 정의는 3.0.1이다. 기존 구현의 103개 테스트를 재실행한 뒤
날짜 미상 action·과거 정답 평가 대기·invalid 수치·overflow·pending export를 보완했다.
DB schema는 유지하며 3.0.0 고정 snapshot도 원래 버전으로 재현한다.

아래는 **합성 테스트 전용** CLI다. 계약→특징→독립 정답→dataset→고정 replay를 실행한다.
실제 가격·계정·네트워크가 필요 없다.

```bash
.venv/bin/python scripts/verify.py
.venv/bin/python scripts/prepare_synthetic_stage3.py
.venv/bin/python -m market_research.cli feature-contract
.venv/bin/python -m market_research.cli label-contract
.venv/bin/python -m market_research.cli dataset-contract
.venv/bin/python -m market_research.cli feature-build --db tests/generated/stage3/pit.sqlite --dataset-db tests/generated/stage3/datasets.sqlite --config tests/generated/stage3/config.json --output tests/generated/stage3/features.json
.venv/bin/python -m market_research.cli label-build --db tests/generated/stage3/pit.sqlite --dataset-db tests/generated/stage3/datasets.sqlite --config tests/generated/stage3/config.json --output tests/generated/stage3/labels.json
.venv/bin/python -m market_research.cli label-update --db tests/generated/stage3/pit.sqlite --dataset-db tests/generated/stage3/datasets.sqlite --config tests/generated/stage3/config.json
.venv/bin/python -m market_research.cli dataset-build --db tests/generated/stage3/pit.sqlite --dataset-db tests/generated/stage3/datasets.sqlite --config tests/generated/stage3/config.json --output tests/generated/stage3/dataset-meta.json
stage3_dataset_id=$(.venv/bin/python -c 'import json; print(json.load(open("tests/generated/stage3/dataset-meta.json"))["snapshot_id"])')
.venv/bin/python -m market_research.cli dataset-verify --db tests/generated/stage3/pit.sqlite --dataset-db tests/generated/stage3/datasets.sqlite --dataset-id "$stage3_dataset_id"
.venv/bin/python -m market_research.cli dataset-replay --db tests/generated/stage3/pit.sqlite --dataset-db tests/generated/stage3/datasets.sqlite --dataset-id "$stage3_dataset_id" --jsonl --output tests/generated/stage3/fixed.jsonl
.venv/bin/python -m market_research.cli training-check --db tests/generated/stage3/pit.sqlite --dataset-db tests/generated/stage3/datasets.sqlite --dataset-id "$stage3_dataset_id" --training-asof 2024-06-01T00:00:00Z
.venv/bin/python -m market_research.cli stage3-report --db tests/generated/stage3/pit.sqlite --dataset-db tests/generated/stage3/datasets.sqlite --dataset-id "$stage3_dataset_id" --output tests/generated/stage3/aggregate.json
.venv/bin/python scripts/validate_stage3.py
```

training-check는 권한/PIT를 검사해 합성 학습 적격 0을 정상 EMPTY로 반환한다.
--allow-research는 별도 진단 선택이며 RESEARCH_DIAGNOSTIC_NOT_TRAINING으로 표시한다.
--require-data는 특징/정답 ready 또는 dataset ready pair/선택 수가 0이면 종료1이다.
blocked/pending/invalid 표본 저장·빈 읽기·snapshot 무결성 완료는 종료0이며 JSON의 상태와
개수를 함께 읽는다. dataset-build의 PASS를 학습 데이터 준비 PASS로 해석하지 않는다.

validate_stage3는 기존 `data/stage2-validation.sqlite`를 발견하면 최초 한 번 backup하고
이후 작업 DB를 재사용하여 이전 query/dataset snapshot을 보존한다. 원 DB/원본은 변경하지 않는다.
--pit-db/--work-pit-db/--dataset-db로 경로를 지정한다. 새 source DB를 연구하려면 새 작업 경로와
dataset DB를 함께 지정하거나 기존 작업 DB에 명시적으로 artifacts를 추가한다. 작업 DB를 덮어쓰지 않는다.
원본 없는 공개 clone에서는 오프라인 검증을 완료하고 실제 자료를 BLOCKED로 기록한다.
실제 원본/DB/행 결과는 gitignored data에만 보관하며 집계 보고서만 공개 대상이다.

실제 AAPL의 split-adjusted OHLCV와 total-return-adjusted close_only는 별개다. 3단계 진단은
close_only에서 종가 특징만, IBM as_traded에서는 경제적 수익률과 구별한 가격 변화 특징을
계산한다. 현재 수신 진단을 과거 의사결정이나 learning_ready 표본으로 사용하지 않는다.
2026년 수신 자료를 2024년 observed cutoff로 소급하거나 historical→assumed fallback하지 않는다.
entry=e session open, h일 exit=e+h session open이며 정답 평가/available_at은 특징 cutoff와 분리한다.
기업행동 complete coverage·증권 거래 상태·당시 버전·학습 권한이 부족하면 정답/학습은 blocked다.

## 4단계: 시간순 검증기와 비용·기업행동 계좌 시뮬레이터

[검증 계약](docs/validation_contract.md), [시뮬레이션 계약](docs/simulation_contract.md),
[현재 실행 보고서](reports/stage4_validation.md), [집계 JSON](reports/stage4_validation.json),
[5단계 인계](reports/stage5_handoff.md)를 따른다. 고정 dataset/feature/label snapshot을
재현하고 expanding/rolling/explicit 날짜 분할, training_asof 적격성·기간 purge,
독립 예측 기록 지표, long/cash 회계, 비용 시나리오와 재현 manifest를 제공한다.
모델 탐색·튜닝·실제 수익성 검증·자동매매는 수행하지 않는다.

아래 명령을 실제 실행했다. 네트워크와 실제 시장 원본 없이 실행 가능한 **SYNTHETIC_TEST_ONLY**다.
분할·배당이 있는 새 합성 fixture는 별도 `stage4-actions` 루트이며 이미 존재하는 고정 입력은
덮어쓰지 않는다. fixture를 새로 만들면 로컬 절대 경로·생성 기록 때문에 ID가 달라질 수 있다.
그 후 같은 입력/설정의 fixed replay는 결과 내용 hash가 같아야 한다.

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/prepare_synthetic_stage4.py --root tests/generated/stage4-actions
stage4_dataset_id=$(.venv/bin/python -c 'import json; print(json.load(open("tests/generated/stage4-actions/input.json"))["dataset_snapshot_id"])')
.venv/bin/python -m market_research.cli split-plan --db tests/generated/stage4-actions/pit.sqlite --dataset-db tests/generated/stage4-actions/datasets.sqlite --dataset-id "$stage4_dataset_id" --dataset-hash "$stage4_dataset_id" --mode synthetic --config tests/generated/stage4-actions/split.json --output tests/generated/stage4-actions/split-plan.json
.venv/bin/python -m market_research.cli split-check --db tests/generated/stage4-actions/pit.sqlite --dataset-db tests/generated/stage4-actions/datasets.sqlite --dataset-id "$stage4_dataset_id" --dataset-hash "$stage4_dataset_id" --mode synthetic --config tests/generated/stage4-actions/split.json
.venv/bin/python -m market_research.cli prediction-check --db tests/generated/stage4-actions/pit.sqlite --dataset-db tests/generated/stage4-actions/datasets.sqlite --dataset-id "$stage4_dataset_id" --dataset-hash "$stage4_dataset_id" --mode synthetic --predictions tests/generated/stage4-actions/predictions.json
.venv/bin/python -m market_research.cli prediction-evaluate --db tests/generated/stage4-actions/pit.sqlite --dataset-db tests/generated/stage4-actions/datasets.sqlite --dataset-id "$stage4_dataset_id" --dataset-hash "$stage4_dataset_id" --mode synthetic --predictions tests/generated/stage4-actions/predictions.json --evaluation-asof 2024-06-01T00:00:00Z --horizon 1
.venv/bin/python -m market_research.cli simulation-run --db tests/generated/stage4-actions/pit.sqlite --dataset-db tests/generated/stage4-actions/datasets.sqlite --mode synthetic --config tests/generated/stage4-actions/config.json --predictions tests/generated/stage4-actions/predictions.json --run-root tests/generated/stage4-actions/runs --output tests/generated/stage4-actions/run-meta.json
stage4_run_file=$(.venv/bin/python -c 'import json; print(json.load(open("tests/generated/stage4-actions/run-meta.json"))["path"])')
.venv/bin/python -m market_research.cli run-show --run-file "$stage4_run_file" --output tests/generated/stage4-actions/run-show.json
.venv/bin/python -m market_research.cli run-replay --db tests/generated/stage4-actions/pit.sqlite --dataset-db tests/generated/stage4-actions/datasets.sqlite --run-file "$stage4_run_file"
.venv/bin/python -m market_research.cli stage4-report --run-file "$stage4_run_file" --output tests/generated/stage4-actions/stage4-report.json
.venv/bin/python scripts/validate_stage4.py
python3 -c 'from setuptools.build_meta import build_wheel; build_wheel("dist/stage4")'
git diff --check
```

Stage4 종료코드: 0=OK/PASS, 1=INVALID/FAIL/ERROR, 2=PARTIAL,
3=BLOCKED/INSUFFICIENT_DATA. argparse 문법 오류의 표준 2는 별도다.
위 prediction-check는 test 1일 예측 4개만 제공했으므로 전체 dataset 42개 기준
누락을 보고하고 PARTIAL/2다. simulation-run은 선택 fold의 test 예측이 완비된 것을 검사한다.
run-show 완료/0은 보관된 실행의 내부 상태를 변경하지 않는다.

`validate_stage4.py`는 전체 테스트, CLI 성공/오류/부분/자료 부적격, 기존 Stage3 고정 dataset의
원본 연결 재현, 비용 시나리오, wheel 소스 일치와 보호 파일을 검사해 집계 JSON을 생성한다.
기존 실제 Stage3 DB가 있으면 네 개의 기존 보고 dataset을 **다시 계산하지 않고** strict로
검사한다. 실제 자료 부재 시 BLOCKED를 기록하고 합성 검증을 끝까지 수행한다.
원본 Stage2 DB·기존 fixed snapshot은 변경하지 않고 Stage4 결과만 별도 저장한다.
이번 runtime venv에는 setuptools가 없어 설치된 시스템 Python/setuptools 78.1.1로 wheel을
오프라인 빌드했다. 다른 환경에서는 pyproject.toml의 build backend를 먼저 설치해야 한다.

예측 생성 시각과 simulated_generated_at은 별도이며 합성의 과거 시각을 실제 운영 기록으로
표현하지 않는다. 주문 크기는 과거 available close/volume, 체결은 다음 시가 가정이고
스프레드·슬리피지는 execution price에 한 번만 반영한다. 동일 종목 보유 중 신호는 보류한다.
미해결 종료 가격·정지·폐지 대가는 포지션을 유지하고 stale/불확실성을 표시한다.
말단 강제 청산은 없다. 실제 학습 적격 0의 strict 거부는 정상 검증 결과다.


## 5단계: 기준선 학습·고정 추론·Stage4 연결

[검증 보고서](reports/stage5_validation.md), [JSON](reports/stage5_validation.json),
[학습 계약](docs/training_contract.md), [모델 artifact 계약](docs/model_artifact_contract.md),
[실제 데이터 실행 과제](reports/data_readiness_action_plan.md), [6단계 인계](reports/stage6_handoff.md).
실제 예측력은 **NOT_EVALUATED**, 실제 학습 적격 0이면 strict 학습은 종료3/BLOCKED다.
합성 신호/null의 성과는 연결 검사다. 매매 임계값·모델·seed는 test 결과로 변경하지 않는다.
현재 Linux ARM64의 실행 결과와 실제 시장 예측력 판정은 구분한다.

아래는 공개 합성 자료만 사용하는 재현 명령이다. sklearn 의존성을 추가했으므로 최신 설치 또는
requirements-lock.txt 설치가 필요하다. 기존 Stage2~4 dataset과 snapshot은 보존한다.

```bash
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/prepare_synthetic_stage5.py
.venv/bin/python -m market_research.cli training-config-check \
  --db tests/generated/stage5/signal/pit.sqlite \
  --dataset-db tests/generated/stage5/signal/datasets.sqlite \
  --mode synthetic --config tests/generated/stage5/signal/regression.json \
  --output tests/generated/stage5/signal/check.json
for dataset_kind in signal null; do
  for task in regression classification; do
    training_case="tests/generated/stage5/$dataset_kind"
    .venv/bin/python -m market_research.cli training-run \
      --db "$training_case/pit.sqlite" --dataset-db "$training_case/datasets.sqlite" \
      --mode synthetic --config "$training_case/$task.json" \
      --training-root "$training_case/$task-release" --output "$training_case/$task-release/result.json"
    .venv/bin/python -m market_research.cli training-replay \
      --db "$training_case/pit.sqlite" --dataset-db "$training_case/datasets.sqlite" \
      --mode synthetic --training-result "$training_case/$task-release/result.json" \
      --output "$training_case/$task-release/replay.json"
  done
done
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/validate_stage5.py
```

training-run은 각 fold의 전처리/모델을 먼저 저장하고 재로드한 뒤 validation/test 예측과
Stage4 metrics/calibration·시뮬레이션·cash 기준선·fixed replay를 생성한다. 모델은 JSON이며
pickle을 불러오지 않는다. test 결과로 후보를 선택하는 기능은 없다. 기존 Stage3
`training-check --dataset-id ... --training-asof ...`는 종전 선택 진단으로 유지된다.

저장 모델 추론과 기존 평가/시뮬레이션 CLI를 따로 재현하려면 아래 예제를 실행한다.

```bash
.venv/bin/python scripts/replay_stage5_example.py
.venv/bin/python scripts/replay_stage5_example.py --task classification
.venv/bin/python scripts/profile_stage5_training.py
```

이 명령은 이미 저장된 signal/regression-release의 Ridge artifact ID·원 config에서
model-predict → prediction-evaluate → simulation-run을 호출한다. 실행 기록은
`tests/generated/stage5/signal/standalone/`에 남는다. 저장 dataset/hash·artifact root·model ID·mode를
명시하며 원 환경/코드가 달라지면 거부한다. 다른 dataset 배포·strict 운영 사용은 별도 계약이 필요하다.
집계 CLI는 `stage5-report --db ... --dataset-db ... --mode synthetic --training-result ... --output ...`다.

실제 strict 재검증 config/거부 결과와 재현 명령은 로컬 `data/stage5/release` 및 공개 검증 JSON에
있다. 원본이 없는 공개 checkout에서는 실제 검사 BLOCKED/missing input을 기록하며 합성 검증을
진행할 수 있다. `validate_stage5.py`의 실제 원본 감사는 이 작업 공간의 보존된 실제 DB를 사용한다.
수집·권한 조건과 수동 observed 명령은 data_readiness_action_plan.md를 따른다. 반복 수집이나
외부 주문을 활성화하지 않는다.


## 5.5단계: 실제 차단 진단과 observed 단일 실행

[검증 보고서](reports/stage5_5_validation.md), [차단 표](reports/data_blocker_matrix.md),
[운영 계약](docs/observed_operation_contract.md), [확보 과제](reports/data_readiness_action_plan.md)를 본다.
40 snapshot은 네 논리 표본의 반복 버전이다. 새 공개 IBM demo의100행은 현재 수신 버전이며
과거 observed 이력이 아니다. 토요일 실행에서는 금요일까지의 현재 lookback61세션만 계산하고
가상 거래 표본/pending을 만들지 않았다. 실제 모델은 없고 prediction_not_available다.

현재 Linux에서 작은 공개 demo 연결 진단을 한 번 실행한다. 공개 예제의 진단 범위이며 일반
계정·다른 종목·장기 무인 수집·학습 권한을 승인한 명령이 아니다.

```bash
.venv/bin/python -m market_research.cli observed-config-check --config configs/observed_ibm_demo.json
.venv/bin/python -m market_research.cli observed-run \
  --config configs/observed_ibm_demo.json --data-dir data/observed_ibm \
  --output data/observed_ibm/execution.json
# 동일 설정/결정 날짜 또는 동일 explicit cutoff는 완료 결과와 원본 hash를 재생한다.
.venv/bin/python -m market_research.cli observed-run \
  --config configs/observed_ibm_demo.json --data-dir data/observed_ibm \
  --output data/observed_ibm/replay.json
# 로컬 기존40 snapshot이 있는 이번 환경의 상세 차단 진단
.venv/bin/python -m market_research.cli data-blockers \
  --db data/stage3/pit_market.sqlite --dataset-db data/stage3/datasets.sqlite \
  --output data/observed_ibm/blockers.json
# 오프라인 전체 tests·wheel/source·설치/import·보존 검사(현재 작업의 starting_state가 있을 때)
.venv/bin/python scripts/validate_stage5_5.py
```

신규 checkout에는 기존 시장 DB/raw/snapshot/starting_state가 없으므로 같은40개나 과거 ID를
재현할 수 없다. 원본 없이 `.venv/bin/python -m unittest discover -s tests -v`로 오프라인 테스트와
`.venv/bin/python -m market_research.cli observed-config-check --config configs/observed_ibm_demo.json`을 실행할 수 있다.

다음 정상 거래일의 마감~close+60분 결정 사이에 실행하면 actual receipt/usable을 조회하고
실제 특징 완료 시각을 기록한 뒤1/5/20일 pending 정답을 등록한다. 늦으면 MISSED_DECISION,
마감 이전은 SESSION_NOT_CLOSED, 주말/휴장은 NON_TRADING_DAY다. 이미 지난 결정 날짜를
입력해도 현재 수신을 당시 observed 입력으로 사용하지 않는다. 새로운 actual cutoff에 기존
표본의 성숙 horizon만 갱신하고 원래 특징/미래 pending/snapshot을 보존한다.

같은 날짜의 **새 수신·정답 평가**에는 실제 새 explicit cutoff 설정을 만든다. 아래 명령은
기존 config를 덮어쓰지 않는다. 새 네트워크 응답이 필요하면 해당 범위 권한 확인 후 --refresh를
붙인다. 이미 완료된 동일 실행에는 --refresh도 다운로드를 다시 하지 않는다.

```bash
.venv/bin/python - <<'PYCODE'
import json
from market_research.http import utc_now
from market_research.storage import write_json
c=json.load(open('configs/observed_ibm_demo.json'))
c.update(cutoff_policy='explicit',cutoff=utc_now())
write_json('data/observed_ibm/next-cutoff.json',c)
PYCODE
.venv/bin/python -m market_research.cli observed-run \
  --config data/observed_ibm/next-cutoff.json --data-dir data/observed_ibm \
  --output data/observed_ibm/next-execution.json
```

explicit cutoff 이후 시작한 새 다운로드/재정규화는 그 cutoff에서 제외된다. 최신 수신까지 쓰려면
권한 확인 후 after_processing 정책으로 별도 날짜 실행을 준비한다. 동일 after_processing 실행이
완료되어 있으면 저장 cutoff를 재생하므로 무인 운영은 회차별 explicit cutoff/사전 일정과 수신 단계의
순서를 운영 계약대로 관리한다. CLI를 미리 실행하고 cutoff를 미래로 넣는 방식은 지원하지 않는다.

운영 계정은 `configs/observed_ibm_account.example.json` 복사본에
collection_rights를 지정하고 ALPHAVANTAGE_API_KEY를 비밀 관리로 주입한다. 필드·ID를
검토한 price_contract_evidence, 개별 trading_state/action_coverage/learning_rights는 hash-bound
JSON으로 제공한다. 필요한 exact 필드는 운영 계약에 있다. 승인되지 않은 raw hash/권한/필드는
명시적으로 거부한다. 증거 없는 verified=true 예제를 복사해서 승인하지 않는다.

승인 후 무인 운영의 1회 명령 예시는 `observed-run --config data/observed_ibm/account.json --data-dir data/observed_ibm --refresh`다.
account.json은 계정 예제의 참조들을 실제 검토 자료로 채운 파일이며 키는 환경변수로만 주입한다.
그날 첫 after_processing 실행은 실제 새 수신을 사용하고 같은 실행의 재시도는 journal을 확인한다.
장기 스케줄 활성화는 담당자의 별도 운영 작업이다.

복구는 같은 설정·경로로 재실행한다. executions/*.json의 last_failure/steps와 collection/manifest의
HTTP/파싱 상태를 확인한다. 성공 수집 checkpoint는 재사용하고 실패 수집만 다시 시도한다.
raw·normalized·manifest·pit.sqlite·datasets.sqlite·calendar.json·samples/*.json을 유지한다.
완료 journal 삭제로 missed 결정을 소급 생성하지 않는다. 새 유효 일정/cutoff를 사용한다.
한 Linux writer와 동일 storage root만 사용한다. 호출 예산은 root별25/UTC일,12초 간격,
최대3시도/timeout20초이며 다른 프로세스의 호출은 중앙에서 별도로 관리해야 한다.
cron/systemd/주문은 활성화하지 않았다.

selector를 직접 검사하려면 새 실제 dataset ID에 명시적 scope를 사용한다.

```bash
.venv/bin/python -m market_research.cli training-check \
  --db data/observed_ibm/pit.sqlite --dataset-db data/observed_ibm/datasets.sqlite \
  --dataset-id "$observed_dataset_id" --training-asof "$actual_training_asof" \
  --data-scope forward_observed --require-data --output data/observed_ibm/readiness.json
```

이 변수들은 실제 생성 ID와 평가 시각으로 설정한다. sample/pending 추가 성공과 학습 준비는
구분한다. 실제 fit은100성숙 표본/180일·purge 후60/20/20·권한·클래스·특징 검사를 통과한
고정 dataset에서 Stage5의 research/data_scope 설정으로 별도 수행한다. 기준을 낮추거나
synthetic 모델을 실제 운영에 쓰지 않는다. 실제 적격 자료와 단순 모델 비교 전 Stage6는 보류한다.

## 실제 데이터 기준선: 제한된 개인 비상업 연구

[결과 보고서](reports/real_data_baseline.md)와 [집계 JSON](reports/real_data_baseline.json)을 참고한다.
EODHD 공식 demo의 AAPL/MSFT/AMZN/TSLA/MCD 일별 자료·분할·배당·증권 식별을 사용한다.
현재 과거 버전과 명시적 가정에 따른 `research_assumed` 실행이며 `NOT_STRICT_PIT`다.
사용자가 확인한 개인 비상업 목적의 일회성 demo 평가로 한정한다. 원본·정규화·DB·예측·모델은
gitignored `data/real_data_baseline/`에 보관하고 재배포하지 않는다.

```bash
.venv/bin/python scripts/run_real_data_baseline.py --personal-noncommercial
```

처음 실행하면 실제 자료를 수집하고, 기존 Stage3~5 경로로 월요일 결정 표본을 생성한다.
고정된 날짜 분할·purge와 train 전용 전처리로 기준선/Ridge/Logistic/작은 트리를 학습하고
저장→재로드→test 예측·평가 및 한 번의 동일 설정 재학습 비교를 수행한다.
재실행은 같은 로컬 원본·고정 dataset을 검증해 재사용한다. 제공처의 현재 과거값이 정정되면
새 다운로드의 결과는 달라질 수 있으므로 원본과 저장소를 유지한다.
`--root data/다른이름`은 기존 자료를 보존한 별도 수집 실행이다.
공식 경매 체결가와 과거 거래 상태가 확인되지 않아 거래 시뮬레이션은 수행하지 않는다.

## 최소 전향 평가: 두 고정 분류 모델

[준비 보고서](reports/forward_evaluation_setup.md)와 [고정 설정](configs/forward_evaluation.json)을 따른다.
기존 train 상승 빈도(0.530791788856305)와 저장 leaf=32 트리 후보만 비교한다.
후보는 실험용이며 학습·튜닝·Champion 승격·주문·스케줄은 실행하지 않는다.
모델·원본이 있는 현재 Linux ARM64 환경에서 기존 기능을 연결하고
`data/forward_evaluation/`에 새 불변 예측/feature/label 버전을 저장한다.

```bash
cd /home/sechi/Stk-ia
# 지금: 현재 lookback과 두 모델 연결만 확인; 전향 예측으로 저장하지 않음
.venv/bin/python scripts/run_forward_evaluation.py --dry-run --cache-only
# 유효 월요일 마감~close+60분 안에 수동 한 번 실행
.venv/bin/python scripts/run_forward_evaluation.py
# 종료 일별 자료 수신 후 성숙/정정 정답만 갱신하고 같은 표본으로 비교
.venv/bin/python scripts/run_forward_evaluation.py --update-only --refresh
```

다음 후보는 뉴욕 2026-10-05이며 한국 **10월 6일 05:00~06:00**, 권장 시작은 **05:20**이다.
실제 수신·처리 cutoff와 추론 완료는 decision_at 이하이어야 한다.
target은 기존 **10월 6일 09:30 EDT 시가→10월 13일 09:30 EDT 시가**의 분할 반영 가격수익률>0이다.
휴장 월요일은 주를 건너뛰고, 시점 미준수·누락·오래된 자료는 차단한다.
동일 입력 재실행은 원 예측을 반환하며 상충 입력은 덮어쓰지 않는다.
주말/휴장/마감 전에는 dry-run이다. 과거 날짜 인수는 missed 진단/원 저장 예측 재생만 허용한다.

현재 **준비 완료, 실제 전향 예측은 미실행**이다. 캐시 dry-run 5종목 연결 성공은 전향 성과가 아니다.
첫 예측에는 10월 5일 bar가 창 안에 수신돼야 하며, 성숙 평가에는 종료 자료와
기업행동 complete coverage·종목 거래 상태의 실제 hash-bound 근거가 추가로 필요하다.
근거 참조는 보고서의 `label_evidence.json` 형식을 따르고 원 학습의 연구 가정은 유지한다.
종료코드는 0=완료/dry-run/정상 pending, 3=예측 blocked/missed/수집 실패,
1=설정·모델·파일 무결성 오류다. 정답 blocked/pending은 JSON의 `label_updates`와
`evaluation.label_status_counts`를 함께 확인한다.
로컬 timestamp/hash는 독립 시각 인증이 아니다. 여러 의사결정 날짜가 쌓이기 전 성능을 일반화하지 않는다.
