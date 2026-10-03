# 특징 계약 3.0.1

특징 계산은 고정된 **가격** query snapshot만 받는다. labels 모듈/미래 정답 query를
참조하지 않는다. query policy는 명시하며 cutoff <= decision_at이다. 기본 decision은
버전이 고정된 XNYS 정규장 close+60분이며 휴일·DST·조기폐장은 달력 행을 따른다.
예측 일정과 제공처 공개 시각·증권 거래 가능 상태는 서로 다른 근거다.

| 특징 | 공식·단위 | 최소 연속 세션/포함 범위 | 입력 |
|---|---|---|---|
| close_return_n (n=1,5,20,60) | C[t]/C[t-n]-1, 비율 | n+1, 현재 포함 | close |
| realized_log_return_volatility_n (5,20,60) | log(C[i]/C[i-1])의 표본 표준편차 | n+1종가/n수익률, 현재 포함 | close |
| close_to_ma_n (20,60) | C[t]/mean(C[t-n+1:t])-1 | n, 현재 포함 | close |
| open_gap | O[t]/C[t-1]-1 | 두 연속 세션 | current open, previous close |
| intraday_high_low_ratio | H[t]/L[t]-1 | 현재 한 세션 | high, low |
| volume_to_prior20_mean | V[t]/mean(V[t-20:t-1]) | 21, 분모는 현재 제외 | volume |
| missing_any_numeric_feature | 계산 불가능한 특징 존재, bool | 입력 상태 | 상태 |
| elapsed_sessions_since_last_observation | 마지막 관측~anchor의 달력 세션 수 | 마지막 관측 필요 | 날짜 |

변동성은 자연로그 가격 비율, ddof=1, 비연율화이며 연율화 상수는 없다.
[Python statistics.stdev 문서](https://docs.python.org/3/library/statistics.html#statistics.stdev)를
확인했고 직접 작은 기대값으로 검증한다. 모든 비율은 무차원이고 표준편차는 일별 단위다.

as_traded에는 **명시적 as_traded_mode=price_change_only**를 적용한다. 다일 특징 이름은
close_price_change_n / realized_log_price_change_volatility_n이며 분할 경계에서 경제적
수익률을 뜻하지 않는다. 검증되지 않은 action으로 raw 복원/총수익을 만들지 않는다.
split_adjusted와 total_return_adjusted는 같은 제공처/증권/통화/세션/meaning/kind의 값으로만
계산하고 현재 조정값의 수학적 계산 가능성과 당시 버전의 PIT 적격성을 분리한다.
unknown 의미/통화, 서로 다른 시리즈, 상충 query는 차단한다.

close_only에서 OHLC/volume 특징은 unavailable이다. 다른 원가격 OHLCV를 가져와 채우지
않는다. 거래량 단위·조정 기준을 보존하며 미검증이면 learning 조건을 충족하지 못한다.
가격×거래량/달러 거래대금 특징은 구현하지 않았다.

연속 **거래 세션**을 요구한다. 결측 날짜를 압축하거나 forward/back fill하지 않는다.
달력 밖 lookback, 표본 시작/상장 전 미확인, 실제 세션 누락, 필드 null, 0/음수 가격,
음수 거래량과 자료형 오류를 reason으로 구분한다. 거래량 0은 실제 관측값이며 거래정지
여부는 미확정이다. 전체 기간 정규화·winsorization·특징 선택은 수행하지 않는다.
필수 수치의 잘못된 자료형·비유한 값·0/음수 가격·음수 거래량과 계산 overflow는 해당 특징을
invalid로 표시하고 표본도 invalid로 보존한다. 다른 특징 하나가 계산된다고 실행 가능한
표본으로 승격하지 않는다. null·lookback 부족은 unavailable로 구분한다.

feature_ready_at은 actual_operational_record를 명시적으로 제공한 경우 실제 주장이고,
기본은 cutoff+명시 지연의 **assumed_dependency_processing_schedule**이다. 실행 시각을
과거 실제 특징 준비로 소급하지 않는다. 준비가 entry 이후면 표본은 blocked다.
별도 diagnostic 함수의 과거 anchor 계산은 의사결정 표본이나 학습 데이터가 아니다.
