# 시간순 검증·예측 계약 4.0.0

엔진은 모델을 학습하거나 탐색하지 않는다. 단일 프로세스에서 고정 Stage3 데이터셋과
별도로 제공된 예측을 검증한다. synthetic / research / strict를 명시하고 자동 전환하지 않는다.
synthetic는 가상 자료 전용이며 research는 학습 허가나 역사적 검증을 뜻하지 않는다.
strict는 market·권한·영구 증권 ID·역사적 PIT·실제 준비 기록을 모두 요구한다.

## 고정 입력

`load_fixed(store, pit, dataset_id, content_hash, mode)`는
`replay_dataset(store, pit, dataset_id, verify_files=True)`와
`iter_dataset_rows(store, pit, dataset_id, verify_files=True)`를 사용한다.
현재 query나 최신 정답을 다시 선택하지 않는다. Stage3 migration은 변경하지 않는다.
입력 특징·정답 정의 3.0.0/3.0.1을 지원하며 실제 실행 manifest에는 사용한 버전을 기록한다.

행 구조는 membership의 sample_id / horizon_sessions / feature_version_id / label_version_id와
feature / label이다. 특징·정답의 원본 가격/action snapshot, assertion, 달력 파일 hash를 검증한다.
source·symbol·증권 식별 수준·통화·가격 의미·세션·조회 정책·가정·상태·권한을 보존한다.
가격/거래 세션 상세 의미는 Stage3 고정 query의 원본 행에 연결된다.
읽기 함수의 기본 max_rows=100000은 로컬 메모리 상한이며 이를 넘으면 거부한다.
처리는 작은 고정 데이터셋을 메모리에서 검사한다. 이 상한은 M2 메모리 보장이 아니다.

학습 선택은 반드시 기존
`training_selection(store, pit, id, training_asof=..., allow_research=False)`를 통과한다.
research/synthetic의 `allow_research=True`는 명시된 엔진 진단이다.
추가로 `feature_cutoff <= feature_ready_at <= decision_at < entry < exit`를 요구한다.
Stage3의 진입 전 준비 검사보다 엄격한 의사결정 시각 검사이며 기존 선택기를 느슨하게 만들지 않는다.
label_available_at은 exit 이상, training_asof 이하이고 권한 확정 시각도 asof 이하이어야 한다.
미래 정정 내용의 확정 시각을 가진 frozen 버전은 과거 학습에서 제외한다. 옛 정답을 최신 버전으로
대체하지 않는다. 옛 버전이 필요하면 그 버전을 포함한 별도 fixed dataset을 명시한다.

## 분할

`make_splits(store, pit, dataset_id, rows, spec, mode)`를 사용한다.
단위는 Stage3 일정의 UTC decision 날짜이며 현재 XNYS 계약에서는 정규장 날짜와 같다.
같은 날짜의 종목은 같은 구간에 배치한다. 종목별 무작위 분할은 없다.

- expanding: train_dates 만큼 시작하고, train 시작을 유지한 채 step_dates씩 확장한다.
- rolling: train_dates 길이를 유지하며 step_dates씩 이동한다.
- explicit: folds의 train / validation / test에 `[첫 날짜, 마지막 날짜]`를 지정한다. 양 끝 날짜를 포함한다.

각 fold의 training_asof는 explicit에서 필수다. 자동 계획은 첫 validation 날짜 UTC 00:00이다.
train 마지막 날짜의 시작 이후, 첫 validation 의사결정 이하이어야 한다.
훈련은 train 날짜 범위와 asof 적격성의 교집합이다. validation은 이후 모델 선택용, test는
최종 미래 평가용이다. 이번 단계는 어느 구간에서도 학습·모델 선택을 수행하지 않는다.
validation 결과로 재학습하려면 새 asof와 새 fixed 예측 기록을 사용하는 별도 실행이 필요하다.
validation이 이후 test에 겹치는 정답도 제거한다.

정답의 정보 구간은 보수적인 닫힌 `[intended_entry_at, intended_exit_at]`이다.
train exit가 첫 validation decision 이상이면 purge한다. validation exit가 첫 test decision
이상이면 purge한다. 경계를 정확히 접하는 경우도 제외한다. feature_ready_at과
label_available_at 검사는 별도로 수행하므로 purge 통과가 학습 적격성을 뜻하지 않는다.

gap_dates는 구간 사이 계획에서 제외할 서로 다른 decision 날짜 수다. 실제 입력에 존재하는
날짜를 센다. purging은 실제 label 시각 겹침 제거, embargo는 평가 이후 훈련을 잠시 금지하는
별도 개념이다. 순수 과거→미래 분할이므로 미래 훈련 구간과 embargo를 만들지 않는다.
gap을 20행 제거로 구현하지 않는다. 날짜 크기와 실제 exit/availability 검사를 함께 적용한다.

이번 정책은 **task_specific**이다. 한 실행의 horizon은 1/5/20 중 하나이며 그 정답만 필요하다.
1일 과제에 미성숙 20일 정답을 섞어 제외하지 않는다. 다중 정답 공동 모델의 all-horizons 정책은
미지원이고 다음 단계에서 새 계약을 정의해야 한다.

min_samples는 train / validation / test 각각 양의 정수다. 부족하거나 fold가 없으면
INSUFFICIENT_DATA이고 metrics/simulation은 null이다. 순서가 뒤집힌 날짜·겹친 구간 등은
INVALID 계약으로 거부한다(CLI 입력 오류 JSON ERROR). 제외 membership과 이유를 기록한다.
훈련 적격성 집계는 전체 frozen dataset에 대한 기존 selector 집계와 추가 계약 집계이며,
fold의 purge 집계는 해당 train/validation 날짜 범위에 대한 별도 집계다.

## 예측 기록

prediction_version=4.0.0. 최소 필드:
prediction_id, sample_id, model_or_signal_id, model_version, training_asof, generated_at,
decision_at, intended_entry_at, horizon, predicted_return, probability_up,
dataset_snapshot_id, dataset_content_sha256, feature_version_id, feature_snapshot_id,
mode, generation_kind. predicted_quantiles는 선택적인 `{ "0.1": 수익률, "0.9": 수익률 }`이다.
oracle 여부도 기록한다(기본 false).

operational에서는 generated_at이 실제 생성 주장이다. decision<=generated_at<entry만
거래 가능하다. replay에서는 generated_at은 이번 계산 시각이고 simulated_generated_at은
별도 시뮬레이션 의사결정 시각이다. 과거 생성 기록으로 표현하지 않는다. strict는 operational만
허용하고 synthetic는 operational 주장을 거부한다. 연구용 replay는 RESEARCH로 남긴다.
시각 경계를 넘은 예측은 지표에 사용할 수 있어도 tradable=false이고 예정 진입은 차단한다.
훈련 asof는 decision 이하이고 실행 fold의 asof와 일치해야 한다.

같은 ID의 동일/상충 중복과 sample+horizon 중복은 모두 거부하며 첫 기록도 사용하지 않는다.
잘못된 데이터셋·특징 snapshot·일정·버전·모드·비유한 수치도 거부한다. 예측 누락은 목록으로
남긴다. 원시 prediction-check는 전체 dataset 기준 누락을 표시하며, simulation-run은 해당
fold의 test 과제에 예측이 완비됐는지 검사한다. train 및 다른 horizon 예측은 필요하지 않다.
oracle는 명시적 정확성 단위 검사에서만 허용하고 포트폴리오 실행은 거부한다.
외부 예측이 실제로 정답을 보지 않았는지 파일 계약만으로 증명하지 못한다. 이번 합성 신호는
정답 값을 읽지 않는 고정 +2%/0.6 예측이며 생성 코드가 공개돼 있다.

## 지표

평가 label_available_at<=evaluation_asof, feature/label ready와 모드 계약을 요구한다.
pending/blocked/invalid/미성숙/결측 예측과 목표의 제외를 이유별로 기록한다. 복수 이유의 합은
행 수와 다를 수 있다. 정확히 0 수익률은 `up=false`, 예측 방향은 predicted_return>0이다.
MAE/MSE는 비율 단위, direction accuracy / Brier / binary log loss는 전체 행 평균이다.
확률은 유한한 [0,1]만 허용한다. log loss 계산에만 epsilon=1e-15로 clipping한다.
Brier·보정표에는 원 확률을 사용한다. 입력 오류 확률을 자동 보정하지 않는다.

일별 Spearman IC는 동점에 평균 순위를 주고 순위 간 Pearson 상관으로 계산한다.
서로 다른 종목 2개 미만, 상수 예측/목표, 같은 날짜 중복 증권은 null과 사유다.
IC 평균은 정의된 날짜들의 동일 가중 평균이며 날짜별 count/undefined reason도 남긴다.
MAE 등의 equal-date 평균은 먼저 각 날짜의 평균을 구한 다음 날짜에 동일 가중치를 준다.
전체 행 가중 평균과 함께 반환한다. 보정표는 [0,.1)…[.9,1]의 10구간으로 count,
mean_probability, observed_up_frequency를 반환하며 빈 구간은 null이다.

분위수는 유한한 0<q<1, 값의 단조 증가(동일 값 허용), 서로 다른 q를 요구한다.
crossed quantile는 예측 계약 오류다. pinball=max(q*(y-v),(q-1)*(y-v))의 q별 평균을 구한다.
행별 가장 작은/큰 제공 q의 닫힌 구간 포함률을 q쌍별로 반환한다. 입력 없음은 null과 사유다.
수치 미정의는 0으로 채우지 않는다. 유한한 입력의 loss가 overflow하면 해당 지표 전체를
null/numeric_overflow로 반환한다. 그 행을 조용히 제거하거나 작은 오차로 바꾸지 않는다.
평균도 크기를 나눠 합산해 중간 overflow를 막는다. 5/20일 중첩 목표는 독립 표본이 아니며
지표로 유의성·투자 수익성·실제 역사적 검증 PASS를 주장하지 않는다.

## CLI와 결과

split-plan / split-check, prediction-check / prediction-evaluate, simulation-run,
run-show / run-replay, stage4-report를 기존 flat CLI에 추가했다.
JSON OK/PASS=0, INVALID/FAIL/ERROR=1, PARTIAL=2,
BLOCKED/INSUFFICIENT_DATA=3. argparse 문법 오류는 기존 표준 usage/2다.
run-show=0은 보관된 실행의 무결성 조회 완료이며 내부 결과의 상태를 승격하지 않는다.
stage4-report는 기존 실행의 집계 JSON 생성으로 내부 BLOCKED/PARTIAL 상태를 유지한다.

실행 manifest는 fixed dataset ID/hash, 사용 정의·예측·정책 버전, split/asof, 전체 설정,
고정 예측 hash, 코드 hash, Python/OS/의존성, seed=null(난수 없음), 제외/오류/경고와 결과 hash를
연결한다. 실행 시각은 별도 execution_metadata다. config와 fixed_predictions는 그대로 저장한다.
result_content_sha256는 canonical result만, run_id는 환경·코드를 포함한 manifest hash다.
run-replay는 원 파일과 원 입력을 확인하고 현재 코드 hash가 다르면 명시적으로 거부한다.
원 소스/wheel과 환경을 보관해 재현한다. 새 생성 실행과 fixed replay를 구분한다.
