# 5단계 기준선 모델 인계

Stage4 엔진/합성 검증은 PASS, 실제 학습 자료와 역사적 성능은 BLOCKED다.
이번 구현은 모델을 학습하지 않는다. 기준 근거는 stage4_validation.md/json 및
docs/validation_contract.md, docs/simulation_contract.md다. 실제 파일/미커밋 diff를 확인하고
HEAD만으로 구현을 판단하지 않는다. 현재 시작 commit은 fb78fc7d712dcd285f9e463ec3673fe732cdc0a5이며
Stage2~4 구현은 미커밋 작업 트리에 있다. 커밋·푸시·운영 거래는 미수행이다.

## 가능한 범위

synthetic 고정 dataset에서 단순 기준선 모델의 시간순 적격 선택→fit→예측 저장→평가/계좌
연결을 구현할 수 있다. 훈련/선택/평가 데이터를 분리하고 preprocessing도 train으로만 fit한다.
research는 명시적 진단으로 남기고 권한/strict PIT를 확보하기 전 실제 학습·투자 성능을 주장하지 않는다.
현재 실제 ready 정답과 strict 학습 적격은 모두 0이다. 실제 조건이 달라졌으면 새 고정
dataset/hash와 변경 근거를 기록해야 하며 기존 snapshot은 보존한다.

## 입력·분할 인터페이스

```python
from market_research.pit import Database
from market_research.datasets.store import DatasetStore, replay_dataset, iter_dataset_rows, training_selection
from market_research.stage4.inputs import load_fixed
from market_research.stage4.splits import make_splits
from market_research.stage4.runs import save_run, replay_run, read_run

with Database(pit_path) as pit, DatasetStore(dataset_path) as store:
    fixed = replay_dataset(store, pit, dataset_snapshot_id, verify_files=True)
    rows = list(iter_dataset_rows(store, pit, dataset_snapshot_id, verify_files=True))
    eligible = training_selection(store, pit, dataset_snapshot_id,
                                  training_asof=training_asof, allow_research=False)
    checked, rows = load_fixed(store, pit, dataset_snapshot_id, content_hash, mode)
    split = make_splits(store, pit, dataset_snapshot_id, rows, split_spec, mode)
    # Fit is Stage5 work. Use frozen IDs from the chosen fold's train partition.
    run = save_run(run_root, pit, store, config, fixed_prediction_records, project_root)
    replay = replay_run(run["path"], pit, store, project_root)
```

membership은 sample_id/horizon_sessions/feature_version_id/label_version_id, payload는 feature/label이다.
feature_snapshot_id, label_snapshot_id, action_snapshot_id와 hash-bound assertion/달력이 출처를 연결한다.
임의 최신 조회·정답 정정 소급·권한 가정·실제 수신 시각 소급을 금지한다.
strict 선택은 기존 allow_research=False를 유지하고 Stage4가 준비<=decision, asof 성숙을 추가 검사한다.
synthetic/research에서의 선택은 기존 allow_research=True 진단이며 strict 학습 허가가 아니다.

split_spec은 kind=expanding/rolling의 train_dates/validation_dates/test_dates/step_dates/gap_dates,
또는 kind=explicit의 folds 날짜 범위와 training_asof를 받는다. horizon=1/5/20 하나를 선언한다.
task_specific 정책이며 1일 과제에서 미성숙 20일 정답을 요구하지 않는다. 다중 과제는 새 정책이 필요하다.
label interval=[entry,exit]로 경계 접촉까지 purge하고 availability는 별도로 검사한다.
INSUFFICIENT_DATA/INVALID fold에서 모델·성능을 만들지 않는다. 자동 재학습·모델 탐색은 없다.

## 모델 예측 기록

prediction_version=4.0.0. prediction_id, sample_id, model_or_signal_id, model_version,
training_asof, generated_at, decision_at, intended_entry_at, horizon, predicted_return,
probability_up, dataset_snapshot_id, dataset_content_sha256, feature_version_id,
feature_snapshot_id, mode, generation_kind가 필요하다. 선택 predicted_quantiles는 q→수익률 map.
generated_at=실제 이번 계산 시각, replay의 simulated_generated_at=별도 과거 가상 의사결정 시각.
strict 실제 운영 기록에는 generation_kind=operational을 사용한다. 과거 재생을 실제 운영 기록으로
꾸미지 않는다. 예측 training_asof는 평가 fold와 일치해야 한다. 예측은 정답을 읽어 생성하지 않는다.
oracle는 정확성 검사로만 분리하며 포트폴리오 실행에 사용할 수 없다.

test 표본의 선택 horizon 예측이 모두 필요하다. 전체 dataset의 다른 horizon/훈련 예측 누락을
prediction-check가 PARTIAL로 표시할 수 있지만 simulation-run은 해당 test 완비 여부를 검사한다.
중복/상충 ID·범위 밖 확률·crossed quantile·dataset/feature 불일치는 거부한다.
생성이 entry 이상이면 예정 진입 불가이며 해당 이유를 기록한다.

## 실행·시뮬레이션 인터페이스

config에는 mode, dataset_snapshot_id/content_sha256, evaluation_asof, split, fold_index,
simulation, 선택 cost_scenarios를 포함한다. 준비 스크립트의 공개 합성 config 생성 코드가 예제다.
simulation에는 policy_version=4.0.0, horizon, currency, initial_cash, start_at/end_at, costs가 필수다.
allocation_fraction, max_prior_volume_participation, insufficient_cash_policy,
repeat_signal_policy, signal_threshold는 계약의 기본값 또는 명시 설정을 사용한다.

한 horizon 전략, long/cash, 정수 기본 주문, prior available close/volume 크기 산정,
다음 시가 체결 가정, 보유 종목 새 신호 보류, 즉시 결제 현금 재사용, 말단 강제 청산 없음이다.
split 후 단주·배당 미수금·미체결·출구 누락 포지션을 보존한다. 스프레드/슬리피지는
execution price에 한 번만 포함한다. 기본/불리한 비용과 cash 비교는 같은 엔진을 사용한다.
현재 Stage3 fixed builder는 단일 시리즈이므로 복수 증권 실제 tape에는 각 증권의 coverage/state
고정 계약이 먼저 필요하다. 이를 임시 ticker 결합이나 공통 상태 파일로 자동 채우지 않는다.

run 파일은 data/stage4 또는 tests/generated에 보관한다. manifest의 코드/환경·입력/설정/예측
hash와 결과 hash를 확인한다. executed_at은 가변 별도 메타데이터다. run-replay는 현재
source hash가 다르면 거부한다. Stage4 wheel과 원 소스를 보관하고 새 Stage5 실행을 별도로 만든다.
실제 raw/DB/행 결과는 공개 저장소에 추가하지 않는다. 공개 재현은 합성 fixture와 집계만 사용한다.

## 현재 재현 증거와 다음 점검

README의 prepare_synthetic_stage4 --root tests/generated/stage4-actions와
scripts/validate_stage4.py를 실행한다. 최종 전체 145개 테스트/CLI 검사 17개/wheel 37모듈 일치,
실제 기존 dataset 40개·합성 7개 replay, Stage2 snapshot 352행/DB bytes 불변을 확인했다.
합성 cash 1,028.30과 fixed result hash는 stage4_validation.json에 기록돼 있다.
검증 스크립트의 기존 migration ResourceWarning은 기록돼 있으며 실제 엔진 실패가 아니다.

실제 선행 조건은 Stage3 인계의 권한·증권 식별/당시 universe·역사 버전 근거·raw 세션/통화·
기업행동/거래 상태·공식 역사 달력·운영 준비 이력을 해결하는 것이다. 현재 값만 계산 가능한
AAPL/IBM 진단을 과거 학습 표본으로 사용하지 않는다. 신규 확보가 없으면 실제 검증 BLOCKED를 유지한다.

M2 8GB는 미실행이다. 동일 오프라인 테스트/fixture/hash, 설치 및 UTC/DST·달력,
메모리/속도와 wheel 실행을 실기기에서 확인한다. Linux ARM64 통과와 구분한다.
이번 기록은 엔진 정확성 검증이며 통계적 유의성·실제 시가 유동성·수익성 증거가 아니다.
