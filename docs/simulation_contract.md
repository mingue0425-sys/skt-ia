# 포트폴리오 회계·체결 계약 4.0.0

명시적 초기 현금, 단일 기준 통화, long/cash, 무차입·무공매도, 정수 기본 주문,
단일 프로세스 이벤트 처리다. 운영 주문·브로커 연결·실제 시가 경매 검증은 없다.
`runs.execute`가 fixed dataset·fold·예측·적격성을 확인한 뒤 `simulate`를 호출한다.
저수준 simulate의 직접 호출은 합성 회계 검사 인터페이스이며 독립 역사적 적격성 인증이 아니다.

## 가격·일정·시각

frozen_tape는 Stage3 fixed feature/label/action snapshot과 hash-bound calendar/coverage/state
assertion만 replay한다. 현재 query를 호출하지 않는다. 가격은 단일 통화 as_traded OHLCV이고
증권 단위 internal ID로 처리한다. 조정 가격과 기업행동을 함께 사용하지 않는다.
동일 날짜/증권에 상충 frozen 가격 버전이 있으면 선택 기준을 임의로 만들지 않고 거부한다.
시장 자료의 learning/PIT 미검증은 strict 실행 전에 BLOCKED다. 합성 coverage는 실제 자료의
근거로 사용할 수 없다. 현재 Stage3 builder는 dataset당 단일 시리즈/상태 assertion이다.
여러 증권의 실제 통합 tape는 각 증권 coverage/state를 고정하는 추가 입력 계약이 필요하다.
순위 지표/분할은 여러 증권 행을 지원하며 저수준 합성 회계는 복수 포지션을 검사한다.

진입=e(다음 session open), 종료=e+h session open, h=1/5/20이다. 실제 시간은 고정 calendar
인덱스로 정한다. 휴일을 압축하거나 관측 누락 세션을 제거하지 않는다. 일별 바의 open을
체결 기준으로 사용하는 것은 **다음 정규장 시가 체결 가정**이다. 해당 바의 공개/수신 시각을
시가에서 안 것처럼 주문 크기 결정에 쓰지 않는다. 일별 계좌 close 평가는 사후 회계 관측이고
당시 실시간 가격 수신·실제 거래 가능성을 증명하지 않는다.

신호 시점에 available_at<=신호 시각인 가장 최근 raw close와 volume만 주문 크기에 사용한다.
quantity=floor(현재 cash*allocation_fraction/prior_close),
floor(prior_volume*max_prior_volume_participation)의 작은 값이다. 기본 두 비율은 1이며 예제는
별도 설정한다. 시가를 미리 조회하지 않고 비용·gap으로 현금이 부족하면 사전 정책을 적용한다.
여러 미체결 주문에 현금을 예약하지 않으므로 동시 과예약은 체결 순서에 따라 부분/거절된다.
주문 원장에는 크기 산정 가격·날짜·available_at·과거 거래량이 남는다.

시장 충격과 유동성 제한도 prior_volume만 사용한다. 미래 당일 volume은 주문 크기나
체결 한도에 사용하지 않는다. prior price/volume가 없으면 신호를 보류한다.
이는 과거 volume에 대한 결정적 체결 가정이며 실제 시가 유동성을 입증하지 않는다.

## 주문·체결

한 실행은 한 horizon이다. 같은 종목을 보유하거나 진입 주문이 남아 있으면 새 신호는
already_holding_or_pending_entry로 보류한다. 기본 정책 defer 이외는 아직 미지원이다.
signal_threshold 이하 예측도 보류하며 oracle로 포트폴리오를 실행할 수 없다.
replay 실제 실행 시각과 simulated_generated_at을 구분하고 진입 이후 생성된 기록은
tradable=false로 기록한다. 실제 strict 운영 예측은 decision<=generated_at<entry가 필요하다.

created→pending→filled, rejected, expired 또는 partially_filled→cancelled의 history를 저장한다.
시가 gap/비용 현금 부족 정책은 reject 또는 partial_cancel이다. partial_cancel은 정수 주식
단위로 가능한 최대 수량을 체결하고 잔량을 즉시 취소한다. 후일 재시도하지 않는다.
부분 진입의 horizon은 원래 예정 e부터 계산하며 잔량으로 새 horizon을 만들지 않는다.
분할로 이미 변환된 주문은 소수 수량을 유지할 수 있다. 추가 현금 부족의 부분 주문은
정수 단위로 보수적으로 축소한다. 미체결·취소 잔량은 보유수량/NAV에 포함하지 않는다.

동일 시각 처리 순서: 이전 날짜 지급일 배당의 보수적 현금 지급 → split → ex-dividend 권리 →
예정 청산 → 예정 진입 → 세션 close 평가 → 동일 시각 신호. 주문/신호 순서는 ID로 결정적이다.
청산 현금은 **결제 시차를 생략하여 즉시 재사용**한다. 그래도 전일 주문 크기를 미래
청산 현금으로 늘리지 않는다. 음수 현금/수량과 모든 cash ledger 일치를 이벤트마다 검사한다.

## 비용

costs에서 생략된 모든 비용은 0이다. 현행 세율을 내장하지 않았다. 다음은 순수 가정 입력이다.

| 설정 | 처리 |
|---|---|
| commission_bps / commission_fixed | 체결 notional에 비례 + 체결별 고정 현금 수수료 |
| half_spread_bps | buy open*(1+bps/10000), sell open*(1-bps/10000)에 포함 |
| slippage_bps | 동일 방향 체결 가격에 포함 |
| impact_bps_at_full_prior_volume | bps*체결수량/prior_volume를 체결 가격에 포함 |
| sell_tax_bps | 매도 notional에 설정 비율, 현금 차감 |
| other_fixed | 체결별 기타 현금 비용 |

스프레드·슬리피지·충격의 합을 체결 가격에 한 번 적용한다. 체결notional에서 현금에
반영됐으므로 이를 다시 차감하지 않는다. fill 원장에는 기준 open, execution price, notional,
직접 commission/tax/other, 각 embedded cost와 전체 execution_difference, cash 전후를 기록한다.
embedded cost 세 항목의 이론 합과 가격 tick 반올림 후 execution_difference가 약간 다를 수 있다.
직접 비용과 체결 차이는 별도로 표시하며 합계 표의 execution_difference를 다른 embedded
항목과 다시 합산하지 않는다. 과도한 가격 차이(100% 이상)는 오류다.

Decimal precision=34, 주문 원수량=floor 정수, split-created 수량은 Decimal로 유지한다.
cash/notional/직접 비용·배당 권리는 0.01, 체결 가격은 0.000001, ROUND_HALF_EVEN이다.
수량은 명시 cash-in-lieu 증거가 없으므로 임의 반올림/현금화하지 않는다. split 연산은 34자리
Decimal 정밀도이므로 반복 무한소수 split에 완전한 유리수 보존을 주장하지 않는다.
평가 NAV는 Decimal 수량*raw 가격+현금+미수금(중간 cent 반올림 없음)이다.
경제적 수익률 보고 비율은 float이고 원장 금액은 문자열 Decimal로 export한다.

기본·adverse 비용 시나리오는 같은 fixed 신호/달력/입력으로 별도 초기 계좌에서 실행한다.
체결 수량·현금·fill count 차이를 보고한다. 예제 1달러 수수료/5bps half-spread/5bps slippage와
adverse 2달러/50bps/50bps는 합성 가정이며 실제 예상 비용 추정치가 아니다.

## 기업행동·결측

Stage3 label_contract 3.0.1의 as_traded 수량 회계와 동일한 split/dividend 의미를 사용한다.
raw event_type의 split/splits, dividend/dividends를 해석한다. 적용 date는 effective/ex date이고
event_date(선언일)로 대체하지 않는다. 효력일 미상이면 전체 실행은 BLOCKED다.
분할은 효력 session open의 보유수량, 관련 pending 주문 수량·산정 가격·prior volume,
이전 mark를 동일 비율로 조정한다. 동일 action_id는 한 번만 처리하고 상충 ID는 오류다.
snapshot들을 합칠 때 source/security/event_key가 같은 사건의 경제적 내용이 같으면
한 대표 버전만 사용하고 내용이 다르면 conflicting_frozen_corporate_action_versions로 거부한다.
저수준 tape에서 같은 event_key의 다른 action_id를 중복 전달하는 경우도 거부한다.
분할 자체에 수수료·현금을 만들지 않는다. 분할로 생긴 단주는 유지한다.

배당은 split 후 주당 as_declared_cash_per_share와 기준 통화를 요구한다. ex-open 직전
보유분에 권리가 생겨 미수금으로 잡는다. ex-open 진입은 해당 권리가 없고 ex-open 청산은
권리를 유지한다. 재투자는 없다. payment_date와 amount가 미확인이면 BLOCKED다.
date-only 지급은 같은 날짜에 입금했다고 추정하지 않고 **첫 관측 세션 중 payment_date보다
뒤의 날짜 open**에 미수금→현금으로 옮긴다. 동일 권리·지급은 중복할 수 없다.
미수금은 NAV에 포함되고 실거래용 현금에는 지급 전까지 포함되지 않는다.

거래정지 날짜에는 진입·청산을 체결하지 않는다. 전체 거래량 0을 자동 halt로 해석하지 않는다.
정해진 exit open 누락/정지면 주문을 한 번 expire하고 포지션과 이유를 유지한다.
다음 관측이나 마지막 가격으로 예정 청산을 대체하지 않는다. 합병/상장폐지 대가가 없으면
포지션·참고 mark를 남기고 신규 진입/청산을 막는다. 증권 state의 폐지일도 동일하게 처리한다.
마지막 mark를 참고 평가에 사용할 수 있지만 stale와 uncertainty를 기록한다.
미해결 출구는 나중 close가 있어도 현금 회수나 완전한 성과로 승격하지 않는다.
시뮬레이션 말단에는 pending orders / positions / receivables를 모두 보존한다.
강제 말단 청산 옵션은 구현하지 않았고 항상 false다.

## 성과·비교

일별 세션 close NAV(초기 현금 포함), 별도 terminal 평가를 사용한다. label 수익률을
이어 붙이지 않는다. 기간 수익률=terminal NAV/initial cash-1. MDD는 초기 NAV와 일별 close,
필요하면 말단 평가의 running peak 대비 하락 비율 최대값(양수)이다.
cash/holdings/미수금/mark 날짜/stale/투자 비중과 거래 왕복 횟수(체결 sell 수),
fill/rejected/expired/partial/pending 건수, 직접/embedded 비용을 출력한다.
회전율은 `(buy+sell 체결 notional 합)/(일별 NAV 산술평균)`의 편도 notional 합 정의다.
왕복 거래의 buy/sell을 모두 포함하며 2로 나누지 않는다. 일별 NAV가 없으면 초기 현금이 분모다.
투자 비중은 증권 평가액/NAV이며 미수 배당은 증권 투자 비중에서 제외한다.
평균·최대는 관측된 일별 close 비중이다. Sharpe는 이번 단계에 제공하지 않고 null과 이유다.

cash 비교와 각 비용 시나리오는 동일 달력·체결·평가·기업행동 규칙으로 실행한다.
이는 계산 비교이지 투자 전략 우월성 증거가 아니다. 완전한 합성 valuation은 PASS_SYNTHETIC,
stale/미해결 경로는 CONDITIONAL이고 주요 total_return/MDD는 null, 참고 값은 별도 필드다.
미청산이지만 정확한 말단 mark가 있으면 평가 기반 수익률과 미청산 상태를 함께 보고한다.
이때 실행 상태는 PARTIAL이다. synthetic/research 결과의 historical_performance는 BLOCKED다.

## 공개 범위·인계

Stage4 실행은 별도 data/stage4 또는 tests/generated 루트에 immutable run JSON으로 저장한다.
원본 DB·기존 dataset은 변경하지 않는다. 공개 산출물은 코드/계약/합성 fixture/집계 보고서이고
시장 원본·DB·행 단위 결과는 gitignored다. actual 자료가 없으면 새 원본을 생성하지 않고
자료 부재 BLOCKED를 보고하며 합성 검증은 계속한다. M2 8GB 실기기 검증은 별도다.
