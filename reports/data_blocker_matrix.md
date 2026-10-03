# 실제 차단 원인 표 — Stage5.5

평가 시각 2026-10-03T06:28:14.482370Z. 모든40 frozen dataset의 원본/hash를 재생 검증했다. 상세 각 version·snapshot·조건은 로컬 `data/stage5_5/diagnostics.json`이다.

## 집계 단위

40 snapshot / 4 sample / 80 snapshot-특징 행 / 56 distinct 특징 version / 240 정답 version(1·5·20일 각80) / 12 논리 sample-horizon이다. 특징 version56 blocked, 정답180 blocked·60 pending. 논리 특징4 blocked, 논리 정답9 blocked·3 pending, 실제 적격0이다.

기존 Stage5의 특징8·정답18 blocked/6 pending은 선택된4 snapshot의 정책별 반복 행 집계다. 이번 전체 version 합집합과 분모가 다르며 악화나 신규 시장 표본 증가가 아니다. 변경 전·후 기존 입력과 적격 수는 같다. 현재 관측 진단61행은 별도 새 수신 이후 snapshot이며 학습 표본이 아니다.

| 범주 | 해당 논리 sample-horizon 수 | 해석 |
|---|---:|---|
| code_schema_connection | 12 | derived no-input/readiness 및 historical-only 연결 제한의 영향을 표시; 원본/스키마 무결성 오류는0 |
| corporate_actions | 12 | 구간 완전성 assertion 없음; 빈 사건 목록 승격 금지 |
| future_outcome | 3 | IBM10/1의 종료1/5/20 세션이 현재 미래 |
| observed_timing | 12 | 옛 cutoff 뒤 실제 수신/미입증 legacy receipt가 있는 정책 버전 |
| past_outcome_missing | 4 | AAPL12/31의3개 및12/2의20일 endpoint가2025년으로 로컬 범위 밖 |
| price_fields | 12 | 원시가/의미/수치 입력 조건 또는 그 결과의 계산 불가 |
| publication_version | 12 | 정확한 공개/버전 또는 당시 calendar 근거 부족; strict 차단 |
| rights | 12 | source/symbol 학습 이용허용 근거 없음 |
| security_identity_listing | 12 | temporary ID, verified share identity 없음 |
| tradability | 3 | 성숙 IBM9/1의 첫 오류3; pending 표본도 향후 개별 상태 필요 |

행은 여러 범주에 포함된다. 합산 금지. 예: publication_version∩observed_timing=12, rights∩security_identity_listing=12. 전체 교차 수는 검증 JSON에 있다.

## 실제 sample과 차단 함수/정확한 필드

| sample ID · 증권 · 결정 | snapshot 예시 | 첫 특징/정답 차단 | 원본에 있는 것 / 없는 것 | 조치 |
|---|---|---|---|---|
| `d826a4c8f0f5f2690292a4eb6a55cd422acfa1b5fe7bff754a75796c7e2c8bac` · AAPL · 2024-12-02 | `010944b1a29791a46596174080ace4f4ca0825b07ba7a5c82a2aaf35972fe44a` | generate_feature→query.eligible: zero inputs; compute_labels: blocked / opening_prices_unavailable_close_only | Yahoo split-adjusted OHLC 존재; 선택된 total-return-adjusted close_only에는 open 없음. as-traded open/2025 endpoint/공개 version·ID·권한 부족 | 과거 receipt 소급 불가; current lookback/new scope는 구현. 외부 증거 없이 적격 승격 불가 |
| `03785dd95f147632fe1b551e786cc3b1138f2b5ae1cc09742a7dbd43eb95b8e0` · AAPL · 2024-12-31 | `010944b1a29791a46596174080ace4f4ca0825b07ba7a5c82a2aaf35972fe44a` | generate_feature→query.eligible: zero inputs; compute_labels: blocked / opening_prices_unavailable_close_only | Yahoo split-adjusted OHLC 존재; 선택된 total-return-adjusted close_only에는 open 없음. as-traded open/2025 endpoint/공개 version·ID·권한 부족 | 과거 receipt 소급 불가; current lookback/new scope는 구현. 외부 증거 없이 적격 승격 불가 |
| `454827a4badedb5f43232815467666f451a35bcc46727dcc042b5d052ea6e808` · IBM · 2026-09-01 | `0a9539ea1df3ccf8f91244a191572d73a6fff5c10dee367eff3d36e65f35278d` | generate_feature→query.eligible: zero inputs; compute_labels: blocked / security_trading_status_unverified | IBM 원 OHLCV 존재; receipt는 옛 decision 뒤. 공개 version·share ID·정규세션/volume 확정·action/state/권한 부족 | 과거 receipt 소급 불가; current lookback/new scope는 구현. 외부 증거 없이 적격 승격 불가 |
| `c755a8dc2b4b4f41ac2998750aefec2d6eabd9d8b0189038c177f12c553f2ae9` · IBM · 2026-10-01 | `0a9539ea1df3ccf8f91244a191572d73a6fff5c10dee367eff3d36e65f35278d` | generate_feature→query.eligible: zero inputs; compute_labels: pending / target_open_not_yet_reached | IBM 원 OHLCV 존재; receipt는 옛 decision 뒤. 공개 version·share ID·정규세션/volume 확정·action/state/권한 부족 | 과거 receipt 소급 불가; current lookback/new scope는 구현. 외부 증거 없이 적격 승격 불가 |

| sample · horizon | entry date | exit date | 로컬 원 계약 endpoint evidence |
|---|---|---|---|
| `03785dd95f14…` · 1 | 2025-01-02 | 2025-01-03 | version counts 0/0; non-null open 0/0 |
| `03785dd95f14…` · 5 | 2025-01-02 | 2025-01-10 | version counts 0/0; non-null open 0/0 |
| `03785dd95f14…` · 20 | 2025-01-02 | 2025-02-03 | version counts 0/0; non-null open 0/0 |
| `454827a4bade…` · 1 | 2026-09-02 | 2026-09-03 | version counts 7/7; non-null open 7/7 |
| `454827a4bade…` · 5 | 2026-09-02 | 2026-09-10 | version counts 7/7; non-null open 7/7 |
| `454827a4bade…` · 20 | 2026-09-02 | 2026-10-01 | version counts 7/7; non-null open 7/7 |
| `c755a8dc2b4b…` · 1 | 2026-10-02 | 2026-10-05 | version counts 0/0; non-null open 0/0 |
| `c755a8dc2b4b…` · 5 | 2026-10-02 | 2026-10-09 | version counts 0/0; non-null open 0/0 |
| `c755a8dc2b4b…` · 20 | 2026-10-02 | 2026-10-30 | version counts 0/0; non-null open 0/0 |
| `d826a4c8f0f5…` · 1 | 2024-12-03 | 2024-12-04 | version counts 5/5; non-null open 0/0 |
| `d826a4c8f0f5…` · 5 | 2024-12-03 | 2024-12-10 | version counts 5/5; non-null open 0/0 |
| `d826a4c8f0f5…` · 20 | 2024-12-03 | 2025-01-02 | version counts 5/0; non-null open 0/0 |

## 코드로 해결한 연결과 외부 조건

1. 현재 수신한 과거 가격은 현재 cutoff의 lookback으로 허용하고 과거 decision에는 제외한다. 기존 query가 이미 구분한 동작을 단일 운영 경로에 연결했다.
2. 역사 정책만 허용하던 learning_ready를 조용히 바꾸지 않고 explicit fixed/forward scope selector·Stage5 gate를 추가했다. 해당 증권과 고정 특징·권한을 검사하며 전체 폐지군은 제한 scope 조건으로 추가하지 않았다.
3. standalone label-update의 미확정 권한 상태를 실제 승인으로 간주하지 않는다. 운영 maturity는 검토된 evidence를 다시 검사해 별도 scoped readiness를 만들고 actual 완료·새 정답/고정 dataset으로 보존한다.
4. 첫-error 뒤 숨은 원시가·publication·identity·coverage/rights·2025 endpoint 누락을 다중 진단으로 드러냈다. 기존 실제 schema/hash/link 오류는 발견하지 않았다.
5. 현재 pending60 version은3개 논리 horizon이며 모두 future_exit_session이다. 종료가 지났지만 local 범위 밖인4개는 pending과 별도 past_outcome_missing이다. 기존 snapshot 수정 없음.

필요한 권한·키·증권/세션 필드 정의·행동/상태·내보내기 형식과 최소 검증은 [실행 과제](data_readiness_action_plan.md)와 [운영 계약](../docs/observed_operation_contract.md)에 있다.
