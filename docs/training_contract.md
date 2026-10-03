# 학습 계약 5.0.0

이 단계는 단일 target·horizon의 오프라인 모델 경로를 검증한다. 합성 결과로 실제 주식 예측력·수익성·통계적 유의성을 주장하지 않는다. 실제 자료의 현재 판정은 [검증 보고서](../reports/stage5_validation.md)에 있다.

## 입력과 과제

run config는 version=5.0.0, mode=strict/research/synthetic, dataset_snapshot_id/content_sha256, task=regression/classification, target_id, horizon=1/5/20, 순서가 고정된 features, models, seed, one_class_policy, model_selection=none, evaluation_asof, split, simulation, regression_threshold, classification_threshold를 명시한다. 최초 검증은 horizon=5이다. 생성 명령은 README에 있다.

- target_id=price_return: Stage3의 as_traded 진입 시가와 e+h 시가, 검증된 split 수량을 사용하고 배당은 제외한다.
- target_id=cash_total_return: 배당 현금·명시 선택한 미수금 포함 여부를 보존한다. 별도 ready 상태와 값을 요구하며 price_return으로 대체하지 않는다.
- classification 정답은 선택한 target의 `return > 0`. 0은 비상승이다. 회귀와 분류는 독립 run/model이다.

[Stage3 특징](feature_contract.md), [정답](label_contract.md), [dataset](dataset_contract.md), [Stage4 분할](validation_contract.md), [시뮬레이션](simulation_contract.md)을 재사용한다. `load_fixed`는 frozen membership·출처 파일·내용 hash를 검사한다. `training_selection`의 allow_research=False는 strict에서 유지한다. synthetic/research의 진단 후보에는 Stage5의 특징·target·성숙·권한 검사를 추가하며 학습 허가로 자동 승격하지 않는다. 실제 research 학습도 hash-bound learning_rights evidence의 model_training_allowed=true, 검증 상태·이용 가능 시각을 요구한다. 증권 ID·가격 의미·query/권한 상태를 artifact provenance에 보존한다.

Stage3의 기존 `training-check --dataset-id ... --training-asof ...`는 계속 진단 선택용이다. Stage5의 설정 전체 검사는 **training-config-check**다. 명령 이름 충돌이나 모드 자동 fallback은 없다.

## 표본과 전처리

fold마다 Stage4 `make_splits`의 날짜 구간·실제 label interval purge·training_asof를 적용한다. ready이며 그때 성숙한 고정 정답 버전만 train으로 쓴다. 같은 sample의 train/validation/test 중복은 거부한다. fold 간 expanding 과거 train 재사용은 정상이다. target별 제외 사유와 membership/정답 version ID를 저장한다. 기본 최소 train은 8이며 split에서 더 큰 수를 요구하면 그 값을 사용한다. 표본 부족이나 정보가 없는 입력은 명시적 거부다.

특징은 pipeline.ALLOWLIST의 Stage3 수치 특징 중 config에서 명시한 목록만 projection한다. 정답·상태·미래가격·label_available_at·ID·일자·행번호·버전·사후 적격성 이유는 입력 특징으로 허용하지 않는다. 원래 feature payload의 다른 열은 모델 matrix에 들어오지 않는다. projection 이후 matrix는 열 집합이 정확히 일치해야 하며 dict 순서와 관계없이 config 순서로 정렬한다. prediction에 요구 열 누락/추가가 있으면 거부한다.

전처리·모델을 하나의 JSON pipeline으로 저장한다. train의 중앙값으로 일시적 결측을 대치하고, train에서 전부 결측인 열·대치 후 상수 열을 제거한다. 제거 열·원 순서·active index·결측 수를 저장한다. Ridge/Logistic만 train의 population mean/std(ddof=0)로 스케일링한다. validation/test는 transform만 수행한다. infinity/NaN/잘못된 자료형·overflow는 거부하고, 관측 범위 밖 유한값을 자동 삭제하거나 clipping하지 않는다.

원 필드 자체가 없는 `missing_input_field`, 미검증 의미, invalid 특징은 구조적 계약 실패이므로 대치하지 않는다. 허용 일시적 결측은 missing session/collection failure, insufficient history/prelisting unverified, lookback outside calendar, 순수 수학 테스트의 temporary_missing이다. 이는 row의 전체 feature/sample 적격성 검사를 대신하지 않는다. 특히 close_only 자료의 open_gap은 중앙값으로 채울 수 없다.

## 고정 모델 설정

| 과제 | 모델 | 사전 고정 설정 |
|---|---|---|
| 회귀 | zero | 항상 0; estimator 학습 없음, 공통 입력 schema 검사 |
| 회귀 | mean | fold train target의 평균 |
| 분류 | frequency | `(n_up+1)/(n+2)`; Beta(1,1) smoothing |
| 회귀 | Ridge | alpha=1, fit_intercept=true, solver=svd |
| 분류 | Logistic | C=1, fit_intercept=true, lbfgs, max_iter=300, tol=1e-8; sklearn 기본 L2 |
| 회귀/분류 | DecisionTree | max_depth=3, min_samples_leaf=8, 고정 random_state=seed; sklearn 기본 squared_error/gini |

트리 계열은 scikit-learn 하나만 추가한다. 신경망·hyperparameter search·validation 후보 선택·별도 calibration은 구현하지 않았다. one_class_policy=error가 기본이고, 명시적 constant 선택에만 smoothed frequency fallback과 사실을 기록한다. frequency 자체는 한 클래스에서도 계산 가능하다. zero/mean/frequency는 정보 없는 특징 열을 요구하지 않는다. 학습되는 linear/tree에 유효 열이 없으면 거부한다. BLAS thread 수는 fit 동안 1이다.

[공식 Ridge](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html), [Logistic](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html) API를 확인하고 작은 선형 문제의 독립 닫힌 식과 export 예측으로 검증한다. 설치 버전은 requirements-lock.txt와 artifact environment를 참조한다.

## 실행과 출력

검사 → fold train 선택 → train-only 전처리 fit → model fit → JSON 저장/재로드 대조 → label 없는 특징 입력으로 validation/test 예측 → Stage4 예측 검증/성숙 정답 평가 → test 고정 규칙 Stage4 save_run/replay → 결과와 제외 사유 저장 순서다. 모델 설정·특징·임계값은 test 결과를 읽어 바꾸지 않는다. `fit_pipeline`은 test/validation 인자를 받지 않으며 `model_selection != none`은 거부한다.

Stage4 prediction_version=4.0.0에 output_task/target_id를 추가하는 호환 확장이다. legacy_joint 기록은 종전 두 출력을 계속 요구한다. regression은 probability_up=null, classification은 predicted_return=null이며 회귀 부호를 확률로 변환하지 않는다. 출력이 없는 지표는 null/output_not_provided다. 분류 방향 정확도는 probability_up>.5 기준이고 거래 규칙 .55와 구별한다. Stage4 calibration bin/Brier/log loss를 재사용한다. 회귀 calibration은 빈 집계이며 보정 모델은 없다.

Stage4 기존 Decimal 엔진을 사용한다. 회귀 공통 .005, 분류 공통 .55 임계값; 10,000 USD, allocation=.25, prior-volume cap=.05, h=5, held signal defer, partial_cancel, commission=1 USD/fill, half-spread=5bps, slippage=5bps인 **합성 가정**이다. cash 기준선은 같은 엔진이다. 회귀/분류의 결정 규칙 차이를 표시한다. zero 신호에 거래를 강제하지 않는다. 필요 없는 hold 기준선은 추가하지 않았다.

학습 완료/생성 시각은 실제 현재 시각, training_asof/logical_model_usable_at/simulated_generated_at은 별도 과거 재생 시각이다. 이 모델이 당시 실제 존재했다고 주장하지 않는다. strict 실제 운영 예측은 기존 Stage4의 operational 준비·실제 생성 조건을 요구한다. 이번 offline artifact의 model-predict는 strict operational 사용을 명시적으로 거부한다. 현재 실제 train 적격 0이어서 실제 모델을 만들지 않았다. future observed를 strict 학습으로 승인하려면 Stage3의 historical-only learning_ready 판정과 Stage4 strict 정책에 별도의 observed 운영 계약·테스트가 필요하며 상태 flag를 임의로 바꾸지 않는다.

## 합성과 재현

공개 generator는 signal/null을 고정 seed=20261003으로 만든다. 관측 x_i와 독립 미래 noise, 원가격 경로·정답 공식·close 후 이용 시각을 generator.json에 기록한다. Stage3가 가격에서 수익률을 만들고 같은 frozen 가격으로 Stage4 계좌를 계산한다. 순수 선형 수학 테스트는 별도이며 미래 정답으로 특징을 만들지 않는다. 무신호 자료에서 단일 seed의 정확도 .5/손익 0을 강제하지 않는다.

학습 config 검사는 training-config-check, fold 실행은 training-run, 저장 모델 추론은 model-predict, 평가는 prediction-evaluate, 계좌는 simulation-run, 저장 결과 재현은 training-replay, 집계는 stage5-report, 전체 보고 생성은 scripts/validate_stage5.py다. CLI 0=OK/PASS, 1=ERROR/INVALID/FAIL, 2=PARTIAL, 3=BLOCKED/INSUFFICIENT_DATA를 따른다. 결과는 집계와 모델 링크를 포함하며 상세 시장 행은 gitignored다. 실행 root가 같으면 result.json 포인터가 갱신되지만 results/<hash>.json·모델·Stage4 runs는 남는다.


## Stage5.5 실제 자료 scope 연결

[Observed 운영 계약](observed_operation_contract.md)의 fixed_security_research/forward_observed는
기존 strict_history와 분리한다. 기존 strict 의미·learning_ready와 합성 최소치는 그대로다.
신규 scoped dataset은 run config에 data_scope와 mode=research를 명시해야 한다.
특징 목록은 해당 frozen 특징의 required_features와 같아야 하며 selector의 다중 scope 조건을
fold training_asof에서 다시 검사한다. 전체 시장 폐지군을 단일 증권 scope에 일괄 요구하지 않지만
해당 증권·가격/세션·action/state·이용 권한·시각·ready target은 계속 요구한다.

실제 신규 scoped fit gate는100성숙 표본/180일 이상의 날짜 범위·purge 후train60/validation20/test20,
분류의 양쪽 클래스다. 예제 개수에 맞춰 낮추지 않는다. scope→Stage5 연결은 작은 수학 문제의
행 선택 테스트로 검증했고 실제 fit/예측은 여전히 미수행이다. 운영 CLI는 수신/표본/성숙 축적만
수행하며 gate 통과 후에도 자동 학습하거나 synthetic artifact를 실제 모델로 로딩하지 않는다.
