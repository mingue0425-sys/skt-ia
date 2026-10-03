# 첫 실제 기준선: 표본·성능 진단 — 2026-10-03

현재 [기준선 보고서](real_data_baseline.md)·[기준선 JSON](real_data_baseline.json)·저장 결과를 확인했다. 요청에 적힌 6,600행, train/validation/test 680/235/235, 경계·성숙 순제외 15개, 7개 모델, NOT_STRICT_PIT, strict 적격 0개, 거래 시뮬레이션 미실행은 현재 파일과 일치한다. 원 실험의 저장·재로드·재학습 재현 PASS는 기존 기록이며, 이번에는 원 모델 7개를 다시 학습하지 않았다.

**단일 후보는 분류 트리의 min_samples_leaf 8→32다. validation은 원 트리보다 개선됐지만 빈도 기준선보다 여전히 나쁘다. 기본 설정을 유지한다.** 수정 모델의 기존 test 평가는 실행하지 않았다. 공개 집계 수치·차이·진단·보존 확인 결과는 [진단 JSON](baseline_diagnosis.json)에 있다. Linux ARM64의 기존 `.venv`를 사용했다. 개별 가격·예측이 포함된 진단 원본은 gitignored `data/real_data_baseline/diagnosis/private_reports/`에 보존하고 공개 보고서에는 집계만 남겼다.

## 1. 6,600행에서 최종 표본까지의 구성

표의 행 수는 종목×일자 또는 종목×결정일 표본 수다. 고유 날짜 수는 종목 간 중복을 제거한다. 5종목 모두 실제 학습·평가에 사용됐으며 설정 의도와 일치한다.

| 종목 | 원자료 행 | 원자료 기간 | 중복 / 결측 / 필드 부족 순제외 | 결정기간 밖: 앞 + 뒤 | 기간 안 비월요일 | 요청 표본 lookback / horizon 제외 | 종목 필터 / 날짜 결합 제외 | 5일 표본 | 분할 순제외 train / val / test | 최종 train / val / test 행 | 각 구간 고유 날짜 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AAPL | 1320 | 2021-07-01~2026-10-02 | 0 / 0 / 0 | 64 + 5 = 69 | 1018 | 0 / 0 | 0 / 0 | 233 | 2 / 1 / 0 = 3 | 136 / 47 / 47 | 136 / 47 / 47 |
| MSFT | 1320 | 2021-07-01~2026-10-02 | 0 / 0 / 0 | 64 + 5 = 69 | 1018 | 0 / 0 | 0 / 0 | 233 | 2 / 1 / 0 = 3 | 136 / 47 / 47 | 136 / 47 / 47 |
| AMZN | 1320 | 2021-07-01~2026-10-02 | 0 / 0 / 0 | 64 + 5 = 69 | 1018 | 0 / 0 | 0 / 0 | 233 | 2 / 1 / 0 = 3 | 136 / 47 / 47 | 136 / 47 / 47 |
| TSLA | 1320 | 2021-07-01~2026-10-02 | 0 / 0 / 0 | 64 + 5 = 69 | 1018 | 0 / 0 | 0 / 0 | 233 | 2 / 1 / 0 = 3 | 136 / 47 / 47 | 136 / 47 / 47 |
| MCD | 1320 | 2021-07-01~2026-10-02 | 0 / 0 / 0 | 64 + 5 = 69 | 1018 | 0 / 0 | 0 / 0 | 233 | 2 / 1 / 0 = 3 | 136 / 47 / 47 | 136 / 47 / 47 |
| 합계 | 6600 | 동일 기간 | 0 / 0 / 0 | 320 + 25 = 345 | 5090 | 0 / 0 | 0 / 0 | 1165 | 10 / 5 / 0 = 15 | 680 / 235 / 235 | 136 / 47 / 47 (합산 아님) |

순서별 대조: **6,600 − 345 − 5,090 = 1,165**, 준비·필드·lookback·horizon 순제외 0, **1,165 − 15 = 1,150 = 680 + 235 + 235**. 따라서 680·235·235는 전체 5종목의 행 수이며 날짜 수가 아니다. 종목당 원자료 고유 날짜는 1,320개, 요청 월요일은 233개, 최종 고유 결정 날짜는 230개다.

결정기간은 2021-10-01~2026-09-25이며, 이 안의 일별 세션은 종목당 1,251개다. 앞 64행과 뒤 5행은 결정 표본으로 요청하지 않은 구간이다. 삭제된 가격 자료가 아니며 앞부분은 특징 lookback, 뒤쪽은 마지막 정답 가격 등에 사용된다. 1,018개 비월요일도 lookback·진입/종료 가격에 계속 사용된다. 휴장 월요일의 화요일 대체 결정은 없다.

builder는 61세션 특징 조회를 수행하고 1/5/20일 정답 3개씩 총 3,495쌍을 만든다. 고정 dataset에는 horizon=5의 1,165쌍만 합친다. 선택하지 않은 1/20일 정답 2,330쌍은 원자료 손실이나 품질 제외가 아니다. 실제 모델의 5개 특징은 최대 21세션을 요구한다. 첫 결정 2021-10-04에 충분한 사전 자료가 있고, 마지막 결정 2026-09-21의 5일 정답은 2026-09-29 종료라 요청 표본의 lookback·horizon 제외는 모두 0이다. 모든 일별 행을 결정 표본으로 생성하는 가상 계산을 실제 제외 수에 합산하지 않았다.

종목마다 독립적으로 동일 source/symbol을 조회·정렬한 뒤 horizon=5 회원을 이어 붙인다. 공통 날짜 inner join이나 추가 종목 선별이 없다. 각 종목의 원자료/결정 날짜 집합이 일치하며 종목 필터·결합 순감소는 0이다. query의 `security_identity_unverified`·`price_semantics_not_requested` count는 계약 보강 전 버전·다른 가격 의미를 배제한 **조회 버전 수**다. 같은 가격의 재조회·정규화 버전을 포함하므로 6,600개의 고유 원자료 행에서 빼거나 표본 제외에 더하지 않았다.

| 구간 | 요청 날짜 범위 | purge 전 행 / 날짜 | 최종 실제 결정 날짜 | 최종 행 / 고유 날짜 |
| --- | --- | --- | --- | --- |
| train | 2021-10-01~2024-09-30 | 690 / 138 | 2021-10-04~2024-09-16 | 680 / 136 |
| validation | 2024-10-01~2025-09-30 | 240 / 48 | 2024-10-07~2025-09-22 | 235 / 47 |
| 기존 test | 2025-10-01~2026-09-25 | 235 / 47 | 2025-10-06~2026-09-21 | 235 / 47 |

training_asof=2024-10-01 00:00 UTC. 모든 종목에서 2024-09-23의 종료/성숙 시각은 2024-10-01 13:30/20:15 UTC라 train에서 5개 제외된다. 2024-09-30은 종료 2024-10-08 13:30 UTC가 첫 validation 결정 2024-10-07 21:00 UTC 이후이고 training_asof에도 미성숙해 5개 제외된다. validation의 2025-09-29는 종료 2025-10-07 13:30 UTC가 첫 test 결정 2025-10-06 21:00 UTC 이후라 5개 제외된다.

이유별 발생은 train asof 부적격 10 + validation 침범 5 + test 침범 5 = **20건**, 중복을 제거한 순감소는 **15개**다. train의 2024-09-30 5개가 앞 두 이유에 중복된다. 별도 embargo는 없고 이후 학습 구간도 없다. 평가 단계 제외는 모든 모델·두 평가 구간에서 0이다.

## 2. 모델별 train / validation / 기존 test 지표

동일 target=price_return, horizon=5, 동일 회원·특징·정답 버전이다. 7개 모델의 validation/test 저장 예측을 우선 읽고 저장 지표와 1e-12 허용오차로 대조했다. 빠진 train 예측만 hash·환경·코드가 검증된 원 저장 모델로 추가 계산했다. 각 과제에서 모든 모델의 표본 집합이 완전히 같아 공통 표본 재제한은 필요 없다. train은 680개, validation과 기존 test는 각각 235개다. **train 수치는 학습 적합도 진단이며 일반화 성능이 아니다. 기존 test는 이미 본 기간의 설명이다.**

회귀 수익률은 소수 단위다. MAE 0.04는 4%p이며 MSE는 소수 수익률 제곱 단위다. 아래 셀은 `MAE / MSE`다.

| 회귀 모델 | train MAE / MSE | validation MAE / MSE | 기존 test MAE / MSE |
| --- | --- | --- | --- |
| zero | 0.038517 / 0.003044998 | 0.038797 / 0.003794327 | 0.034102 / 0.002190427 |
| mean | 0.038432 / 0.003033093 | 0.038655 / 0.003754862 | 0.034323 / 0.002203240 |
| ridge | 0.037880 / 0.002919350 | 0.040240 / 0.003988815 | 0.034420 / 0.002272140 |
| tree | 0.036580 / 0.002613945 | 0.041381 / 0.004267653 | 0.036592 / 0.002594153 |

회귀 기준선 대비 `모델−기준선` 절대 차이와 `(모델−기준선)/기준선 × 100` 상대 차이다. 오차는 음수가 개선이다. 각 기준선 자신의 차이는 0이다. 분모 0일 때 상대 차이를 계산하지 않으며 이번 표의 오차 분모는 모두 양수다.

| 모델 | 기준선 | 지표 | train Δ (상대) | validation Δ (상대) | 기존 test Δ (상대) |
| --- | --- | --- | --- | --- | --- |
| zero | mean | MAE | +0.000085 (+0.22%) | +0.000142 (+0.37%) | -0.000221 (-0.64%) |
| zero | mean | MSE | +0.000011905 (+0.39%) | +0.000039465 (+1.05%) | -0.000012813 (-0.58%) |
| mean | zero | MAE | -0.000085 (-0.22%) | -0.000142 (-0.37%) | +0.000221 (+0.65%) |
| mean | zero | MSE | -0.000011905 (-0.39%) | -0.000039465 (-1.04%) | +0.000012813 (+0.58%) |
| ridge | zero | MAE | -0.000637 (-1.66%) | +0.001443 (+3.72%) | +0.000318 (+0.93%) |
| ridge | zero | MSE | -0.000125647 (-4.13%) | +0.000194488 (+5.13%) | +0.000081713 (+3.73%) |
| ridge | mean | MAE | -0.000552 (-1.44%) | +0.001586 (+4.10%) | +0.000097 (+0.28%) |
| ridge | mean | MSE | -0.000113743 (-3.75%) | +0.000233953 (+6.23%) | +0.000068900 (+3.13%) |
| tree | zero | MAE | -0.001937 (-5.03%) | +0.002584 (+6.66%) | +0.002489 (+7.30%) |
| tree | zero | MSE | -0.000431053 (-14.16%) | +0.000473325 (+12.47%) | +0.000403726 (+18.43%) |
| tree | mean | MAE | -0.001851 (-4.82%) | +0.002726 (+7.05%) | +0.002269 (+6.61%) |
| tree | mean | MSE | -0.000419148 (-13.82%) | +0.000512791 (+13.66%) | +0.000390913 (+17.74%) |

분류 기준선은 train 상승 빈도에 alpha=1을 적용한 `(361+1)/(680+2)=0.530791789`다. 정답은 수익률>0, 방향 예측은 확률>0.5다. 0 및 확률=0.5는 비상승이다. Log Loss는 원 코드와 동일한 natural log·epsilon=1e-15다. 거래 임계값 0.55는 이 정확도에 사용하지 않는다. 아래 셀은 `Brier / Log Loss / 방향 정확도`다.

| 분류 모델 | train Brier / LL / 정확도 | validation Brier / LL / 정확도 | 기존 test Brier / LL / 정확도 |
| --- | --- | --- | --- |
| frequency | 0.249046 / 0.691239 / 53.09% | 0.248721 / 0.690586 / 53.62% | 0.251865 / 0.696884 / 48.51% |
| logistic | 0.245501 / 0.683873 / 55.88% | 0.260952 / 0.716277 / 46.81% | 0.252964 / 0.698944 / 48.51% |
| tree | 0.231666 / 0.650236 / 58.24% | 0.290943 / 1.472177 / 48.94% | 0.261079 / 0.721145 / 48.51% |

분류 빈도 기준선 대비 차이: Brier·Log Loss는 절대 차이와 상대 %, 정확도는 %p 차이(양수가 개선)다. 기준선 자신의 차이는 0이다. 회귀 MAE·MSE와 분류 Brier·Log Loss의 크기를 서로 비교하지 않는다.

| 모델 | 지표 | train Δ | validation Δ | 기존 test Δ |
| --- | --- | --- | --- | --- |
| logistic | Brier | -0.003546 (-1.42%) | +0.012232 (+4.92%) | +0.001099 (+0.44%) |
| logistic | Log Loss | -0.007365 (-1.07%) | +0.025690 (+3.72%) | +0.002060 (+0.30%) |
| logistic | 방향 정확도 | +2.79%p | -6.81%p | +0.00%p |
| tree | Brier | -0.017380 (-6.98%) | +0.042222 (+16.98%) | +0.009214 (+3.66%) |
| tree | Log Loss | -0.041003 (-5.93%) | +0.781591 (+113.18%) | +0.024262 (+3.48%) |
| tree | 방향 정확도 | +5.15%p | -4.68%p | +0.00%p |

실제 상승 비율과 예측 확률 분포다. 빈도 기준선은 각 구간에서 동일 상수이며, 다른 모델의 분포는 행 기준 경험 분위수다.

| 구간 | 모델 | 실제 상승 | 평균 확률 | 최소 / 5% / 중앙 / 95% / 최대 | 상승 예측 행 |
| --- | --- | --- | --- | --- | --- |
| train | frequency | 361/680 (53.09%) | 0.530792 | 0.530792 / 0.530792 / 0.530792 / 0.530792 / 0.530792 | 680 |
| train | logistic | 361/680 (53.09%) | 0.530882 | 0.018744 / 0.444396 / 0.532857 / 0.608912 / 0.823277 | 529 |
| train | tree | 361/680 (53.09%) | 0.530882 | 0.204082 / 0.204082 / 0.537367 / 0.750000 / 1.000000 | 613 |
| validation | frequency | 126/235 (53.62%) | 0.530792 | 0.530792 / 0.530792 / 0.530792 / 0.530792 / 0.530792 | 235 |
| validation | logistic | 126/235 (53.62%) | 0.538717 | 0.341070 / 0.431577 / 0.542492 / 0.634124 / 0.803361 | 189 |
| validation | tree | 126/235 (53.62%) | 0.530031 | 0.204082 / 0.204082 / 0.537367 / 0.866667 / 1.000000 | 204 |
| 기존 test | frequency | 114/235 (48.51%) | 0.530792 | 0.530792 / 0.530792 / 0.530792 / 0.530792 / 0.530792 | 235 |
| 기존 test | logistic | 114/235 (48.51%) | 0.537420 | 0.408981 / 0.455705 / 0.537539 / 0.604320 / 0.750160 | 195 |
| 기존 test | tree | 114/235 (48.51%) | 0.534859 | 0.204082 / 0.204082 / 0.537367 / 0.625000 / 1.000000 | 221 |

## 3. 확인된 원인과 남은 불확실성

**날짜·단위·종목 정렬 오류는 확인되지 않았다.** 원자료·고정 달력을 사용해 1,165개 정답 전부를 next-session open → entry+5 sessions open으로 다시 계산했다. 원시 문자열 형식이 다른 UTC 시각은 timestamp로 정규화해 비교했다. 진입/종료 일정 불일치 0, 수익률 불일치 0, 특징·정답 종목 불일치 0, 시간 순서 위반 0, 단일 증권 ID 위반 0이었다. 5개 특징의 산술 재계산도 각각 불일치 0이다(허용오차 1e-12). 분할을 통과한 정답 1개의 `분할 후 수량×종료 시가/진입 시가−1` 계산도 저장 정답과 일치한다. 손계산용 개별 가격·날짜 사례는 로컬 진단 원본에 보존했다. 배당은 price_return에서 제외된다.

**train-only 전처리 위반·상수/중복 특징은 확인되지 않았다.** 원 모델 모두 fit_count=680이며 중앙값·active index, 선형 모델의 평균·표준편차가 train 행에서 독립 계산한 통계와 일치했다. 실제 5개 특징은 모든 구간에서 결측 0, train에서 상수·전부 결측·완전히 동일한 열 0이다. runner는 train만 fit_pipeline에 전달하고 validation/test에는 저장 상태로 transform만 적용한다. 현재 표본에서 imputation으로 무의미해진 특징은 없다.

**작은 잎의 과도한 확률 확신과 일반화 격차는 관측됐다.** Ridge·회귀 트리의 train MAE/MSE와 Logistic·분류 트리의 train Brier/LL는 각각 해당 기준선보다 작지만 validation에서는 모두 더 크다. 따라서 “train과 validation 모두 기준선보다 나쁨”에 해당하지 않는다. 가장 구체적인 분류 트리 문제는 train 16개 모두 상승인 깊이-2 잎의 확률 1.0이다. validation의 이 잎 10개 중 5개는 비상승이며, 이 5개만 Log Loss 합의 약 49.92%를 차지한다. 오분류 한 개당 `−log(1−clip(1))≈34.54`이므로 validation LL 1.47218의 직접 원인이 된다. 다른 모델의 성능 격차에는 기간 변화와 특징 신호 부족도 섞일 수 있어 단일 인과 설명으로 확정하지 않는다.

**원가격 분할 경계 특징은 경제적 수익률 특징이 아니다.** 기존 코드가 `price_change_only`로 명시한 의도적 의미다. train 6개 결정 표본에서 MA/변동성 등의 창이 AMZN 또는 TSLA 분할을 통과한다. 해당 표본의 큰 가격 변화·MA 비율·변동성은 원가격 산술과 일치하며 부호·배율 구현 오류로 확인되지 않았다. 개별 특징 값은 로컬 진단 원본에 보존했다. validation의 변동성 최댓값은 0.071180577로 train 분할 경계와 범위가 다르다. 경제적 신호 해석과 train 스케일에 영향을 줄 수 있지만 이번 성능 악화에 대한 기여는 분리 추정하지 않았다. 우선순위상 관측된 작은 잎 문제 하나를 다루며 특징 처리까지 동시에 바꾸지 않았다.

**validation 상승 비율 변화만으로 분류 악화를 설명할 수 없다.** train 53.09% → validation 53.62%로 +0.53%p여서 빈도 기준선 Brier는 0.249046→0.248721로 오히려 작아진다. 기존 test의 48.51%는 train보다 −4.58%p이고 빈도 기준선 Brier가 0.251865로 커진다. 후자는 기존 결과를 설명하는 관측이며 변경 선택에는 사용하지 않았다. validation 실제 빈도를 상수 예측으로 바꾸어 배포·선택하지 않았다.

**회귀 validation MSE는 TSLA와 소수 큰 실제 수익률에 집중된다.** 아래 share는 각 모델 전체 validation 제곱오차 합에서의 비중이다. 분류 Brier는 수익률 크기를 사용하지 않으므로 같은 극단값 설명을 그대로 적용할 수 없다.

| 회귀 모델 | TSLA MSE | TSLA 전체 제곱오차 비중 | 오차 상위 5행 비중 |
| --- | --- | --- | --- |
| zero | 0.013469128 | 71.00% | 39.89% |
| mean | 0.013339265 | 71.05% | 39.30% |
| ridge | 0.014083071 | 70.61% | 37.50% |
| tree | 0.015095380 | 70.74% | 39.47% |

zero 기준선의 제곱오차 상위 5행은 모두 TSLA의 양의 수익률 표본이다. 개별 결정 날짜·수익률·예측은 로컬 진단 원본에 보존했다. 전 정답 원자료 계산 대조를 통과했으며 분할·수익률 단위 오류로 확인되지 않았다. 다만 공급자 가격의 외부 독립 진위까지 검증한 것은 아니다. 종목·행 삭제 또는 winsorization을 실행하지 않았다. 분류 트리 validation Brier의 종목별 오차 비중은 17.84~23.99%로 한 종목에 압도적으로 집중되지 않는다.

**표본 부족 여부와 신호의 안정성은 불확실하다.** 유효 특징 5개·train 680행이지만 5종목×136 결정 날짜이며 독립 680관측은 아니다. 최대 선택 lookback 21세션, builder 조회 61세션, 종목 간 상관과 일부 휴일 정답 구간 중첩이 있다. 한 validation의 47날짜와 단일 fold로 “lookback 대비 반드시 부족”이나 통계적 유의성을 판정하지 않는다. 현재 vendor vintage, 가정된 공개/가용 시각, 기업행동·거래상태 완전성, 현재 존속 5종목 선정 편향은 그대로 남는다. NOT_STRICT_PIT·strict 적격 0·시뮬레이션 NOT_RUN 상태를 유지한다.

코드 근거: [실제 실행 설정](../scripts/run_real_data_baseline.py), [특징 생성](../src/market_research/datasets/features.py), [표본 builder](../src/market_research/datasets/builder.py), [정답·성숙](../src/market_research/datasets/labels.py), [달력 일정](../src/market_research/datasets/calendar.py), [시간 분할·purge](../src/market_research/stage4/splits.py), [전처리·모델](../src/market_research/training/pipeline.py), [train/추론 경로](../src/market_research/training/runner.py), [평가 산식](../src/market_research/stage4/metrics.py).

## 4. 선택한 변경 하나와 변경 전 기록

- 관측: 분류 트리 train Brier 0.231665911, validation 0.290942907; train Log Loss 0.650235582, validation 1.472177108. 빈도 기준선 validation Brier 0.248720643, Log Loss 0.690586427. 확률 1.0 잎은 train 16개 전부 상승; validation 10개 중 비상승 5개. 깊이 2에 위치한다.
- 단일 변경: **classification/tree의 min_samples_leaf 8 → 32**. max_depth=3·seed·특징·표본·전처리·다른 모델 설정 유지. 16개 순수 잎보다 큰 최소치로 작은 잎 추정을 제한한다. 32는 후보를 돌려 고른 값이 아니다.
- 기대: 작은 잎의 과도한 확률 확신과 validation 오차 감소. 새 잎이 순수하지 않을 것이라는 보장은 없다.
- 비교: 동일 validation 235개에서 Brier 주 지표, Log Loss 보조 지표, 방향 정확도 참고. Brier 감소·Log Loss 비악화를 개선 조건으로 사전 기록한다.
- 처리: 개선되지 않으면 원 설정을 유지하고 추가 후보 탐색 없이 종료. 개선해도 빈도 기준선보다 나쁘면 실험 후보로만 보관하며 기본 설정으로 승격하지 않는다.
- 선택 근거: train·validation만 사용. 기존 test는 원 결과 설명에만 사용하며 수정 모델을 평가하지 않는다.

이 기록을 후보 fit 전에 저장했고 전문과 SHA-256을 진단 JSON의 candidate.pre_change_record / candidate.plan.change_plan_sha256에 보존했다. 깊이 3→2는 fit 전 코드 검토에서 확률 1.0인 깊이-2 잎을 그대로 둔다는 것을 확인해 평가하지 않았다. 평가한 후보는 **min_samples_leaf=32 하나뿐**이다. 프로세스 안에서 해당 fit 설정만 바꾸고 복원했으며 공유 PRESETS·기준선 코드·원 설정 파일은 수정하지 않았다. 회귀 트리 설정도 유지한다.

## 5. validation 비교 결과

| 모델 | 공통 validation 행 | Brier | Log Loss | 방향 정확도 |
| --- | --- | --- | --- | --- |
| 원 분류 트리 (leaf=8) | 235 | 0.290942907 | 1.472177108 | 48.94% |
| 단일 후보 (leaf=32) | 235 | 0.264493835 | 0.725894791 | 52.77% |
| train 빈도 기준선 | 235 | 0.248720643 | 0.690586427 | 53.62% |

| 차이 | Brier Δ | Log Loss Δ | 정확도 Δ |
| --- | --- | --- | --- |
| 원 트리 대비 | -0.026449073 (-9.09%) | -0.746282317 (-50.69%) | +3.83%p |
| 빈도 기준선 대비 | +0.015773192 (+6.34%) | +0.035308364 (+5.11%) | -0.85%p |

후보 train Brier / Log Loss / 정확도는 0.237198067 / 0.666859069 / 59.41%다(적합도 진단). validation 확률 최소/중앙/최대는 0.272727 / 0.553957 / 0.718310, 평균 0.516201이며 확률 1.0 예측은 10→0개가 됐다.

사전 조건인 Brier 감소·Log Loss 비악화를 충족했다. 하지만 빈도 기준선보다 Brier +6.34%, LL +5.11%, 정확도 −0.85%p이므로 **후보를 로컬 비교 결과로 보관하고 기본 설정은 승격하지 않는다.** 한 후보의 validation 개선은 후보 선택에 관한 근거이며 미사용 자료에서의 성능 입증이 아니다. 추가 후보·특징 탐색은 하지 않았다.

필요한 검증만 실행했다: 해당 모델 한 번 fit, 기존 portable adapter와 sklearn train 예측 대조(PASS·최대 오차 0), 모델 저장→재로드 후 validation 예측 일치(rtol=atol=1e-12), 기존 train 회원·전처리 상태 불변, validation 예측 계약 OK·235개·제외 0. 원 7개 모델 재학습·전체 테스트·wheel·전체 snapshot replay는 실행하지 않았다. 기존 원자료·DB·보고서·예측·모델 등 50개 파일의 전후 hash가 모두 일치했다. 기존 미커밋 변경을 유지하고 커밋·푸시하지 않았다.

저장 위치(기존과 동일하게 gitignored):

- 후보 모델: `data/real_data_baseline/diagnosis/classification/models/f18ca8719429b5fb5f7022d4ff3b77eab723801044a9429ec12b7fb570659f64.json`
- 후보 validation 예측: `data/real_data_baseline/diagnosis/classification/predictions/f18ca8719429b5fb5f7022d4ff3b77eab723801044a9429ec12b7fb570659f64-validation.json`
- pipeline SHA-256: `7eee01af8c5d50b7f536c4f8ae130c29eaf8483fa8d9c5994a75a2d26d294fe8`

아래 명령은 기존 원자료·frozen 회원·원 모델 provenance를 재사용해 해당 후보 하나만 학습·저장·validation 평가한다. 새 다운로드나 기존 test 예측은 수행하지 않는다. 현재 원본 코드·의존성·로컬 데이터가 필요하다. 실행 결과의 pipeline hash를 위 값과 비교할 수 있다.

```bash
cd /home/sechi/Stk-ia
.venv/bin/python - <<'PY'
import copy, json, sqlite3
from pathlib import Path
import numpy as np
from market_research.storage import canonical, sha256, write_json
from market_research.http import utc_now
from market_research.training.pipeline import PRESETS, fit_pipeline, predict, project_features
from market_research.training.runner import training_rows, feature_inputs, infer
from market_research.training.artifacts import save_model, load_model
from market_research.stage4.predictions import validate_predictions
from market_research.stage4.metrics import evaluate

project = Path.cwd()
root = project / 'data/real_data_baseline'
report = json.loads((project / 'reports/baseline_diagnosis.json').read_text())
result = json.loads((root / 'classification/result.json').read_text())
config = result['config']
fold = result['input_check']['splits']['folds'][0]
original = next(m for m in result['folds'][0]['models'] if m['model_name'] == 'tree')
old = load_model(original['artifact']['path'], root / 'classification/models',
                 original['artifact']['model_id'], 'research', project)
conn = sqlite3.connect(f'file:{root}/datasets.sqlite?mode=ro', uri=True)
rows = []
for item in fold['partitions']['train'] + fold['partitions']['validation']:
    f = json.loads(conn.execute('SELECT payload_json FROM feature_versions WHERE feature_version_id=?',
                               (item['feature_version_id'],)).fetchone()[0])
    l = json.loads(conn.execute('SELECT payload_json FROM label_versions WHERE label_version_id=?',
                               (item['label_version_id'],)).fetchone()[0])
    assert sha256(canonical(f)) == item['feature_version_id']
    assert sha256(canonical(l)) == item['label_version_id']
    rows.append(item | {'feature': f, 'label': l})
x, y, members, excluded = training_rows(rows, fold['partitions']['train'], config, fold['training_asof'])
assert not excluded and len(y) == 680
assert members == old['content']['provenance']['training_membership']
settings = PRESETS['tree']
PRESETS['tree'] = settings | {'min_samples_leaf': 32}
started = utc_now()
try:
    pipeline = fit_pipeline(x, y, config['features'], 'classification', 'tree', config['seed'], minimum=60)
finally:
    PRESETS['tree'] = settings
assert pipeline['preprocessing'] == old['content']['pipeline']['preprocessing']
assert sha256(canonical(pipeline)) == report['candidate']['artifact']['pipeline_sha256']
provenance = copy.deepcopy(old['content']['provenance'])
provenance['baseline_diagnosis'] = report['candidate']['plan']
saved = save_model(root / 'diagnosis/classification/models', pipeline, provenance, project, started)
loaded = load_model(saved['path'], root / 'diagnosis/classification/models', saved['model_id'], 'research', project)
inputs = feature_inputs(rows, fold['partitions']['validation'])
values = [project_features(r['feature'], config['features']) for r in inputs]
np.testing.assert_allclose(predict(pipeline, values), predict(loaded['content']['pipeline'], values),
                           rtol=1e-12, atol=1e-12)
preds, rejects = infer(loaded, inputs, config['dataset_snapshot_id'], config['dataset_content_sha256'], 'research')
assert not rejects and len(preds) == 235
ids = {r['sample_id'] for r in inputs}
validation_rows = [r for r in rows if r['sample_id'] in ids]
checked = validate_predictions(preds, validation_rows, config['dataset_snapshot_id'],
                               config['dataset_content_sha256'], 'research')
assert checked['status'] == 'OK'
metrics = evaluate(validation_rows, checked['accepted'], evaluation_asof=config['evaluation_asof'],
                   mode='research', horizon=5)
assert metrics['count'] == 235 and not metrics['exclusion_counts']
path = root / 'diagnosis/classification/predictions' / f"{saved['model_id']}-validation.json"
if not path.exists():
    write_json(path, preds)
print(json.dumps({'pipeline_sha256': saved['pipeline_sha256'], 'metrics': metrics['metrics_row_weighted']}, indent=2))
PY
```

## 6. 다음 독립 평가에 필요한 미사용 기간 또는 새 데이터

현재 수집 끝은 2026-10-02, 기존 test의 마지막 결정은 2026-09-21, 마지막 정답 종료는 2026-09-29다. 이후 2026-09-30~10-02의 3세션은 마지막 test target에 직접 포함되지는 않지만 이번 원자료 진단 범위이고, 새 월요일 결정의 성숙한 5일 정답 구간을 제공하지 못한다. 월요일 제한으로 생략된 과거 일자를 다시 떼어 독립 “새 test”라고 부르지 않는다. **현재 보관 자료에는 이번 선택 뒤 독립 평가할 성숙한 미사용 구간이 없다. 새 데이터가 필요하다.**

다음 행동 하나: 기준선과 leaf=32 후보·평가 규칙을 지금 고정하고 **2026-10-05 이후의 실제 미관측 기간**을 독립 평가용으로 예약한다. 고정 XNYS 달력에서 첫 월요일 결정 2026-10-05의 진입은 10-06, 종료는 10-13이므로 해당 종료 가격의 일별 관측 완료·수신과 정답 성숙 이후부터 평가 가능하다. 충분한 여러 결정 날짜가 쌓일 때까지 모델·특징을 새 평가 결과로 재선택하지 않는다. 이 날짜들은 보관된 기준 달력에 따른 계획이며 실제 세션·가용성은 신규 관측에서 확인해야 한다. 기존 test에 대한 새 성능 주장은 만들지 않았다.
