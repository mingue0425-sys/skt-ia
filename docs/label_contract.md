# 정답 계약 3.0.1

decision_at=정규장 close+60분, 진입은 다음 session open(e), h=1·5·20의 종료는
**e+h session open**이다. 월요일 시가 진입의 1일 정답은 다음 세션 시가까지다.
daily OHLC 응답의 open도 전체 일별 bar가 완료되고 query policy로 적격해진 뒤에만 읽는다.
exit open 도달이 label_available_at이나 즉시 수신을 뜻하지 않는다.

price_return_h는 as_traded open을 사용하고 검증된 split으로 보유수량을 변경해 계산한다.
배당 현금은 제외한다. up_5=(price_return_5>0), down_5pct_5=(price_return_5<=-0.05).
close_only/조정 종가를 시가로 대체하지 않는다. source·증권·통화·세션 혼합은 차단한다.

cash_total_return_h는 별도 값·상태다. 진입수량 1, 배당 재투자 없음, 분할 비율에 따른
수량 변화, 지급 현금과 **include_dividend_receivables**의 명시 선택을 반영한다.
진입일의 ex-open에 매수하면 그 배당 권리를 얻지 않는다. ex-open 직전 보유분은 종료일
ex-open에 매도하더라도 권리를 얻는다. 같은 날 split→배당(분할 후 주당 현금액) 순서가
검증돼야 한다. date-only 지급일은 exit일보다 이전이면 지급 완료로 보수 처리하고,
같은 날은 시간 미상 미수채권이다. 날짜에 정확한 지급 timestamp를 만들지 않는다.
합병·폐지 대가/복잡한 action은 미지원으로 blocked이며 마지막 가격을 사용하지 않는다.

action query가 비어 있음과 **verified complete interval coverage에 근거해 action이 없음**은
다르다. hash-bound corporate_action_coverage assertion을 별도로 요구하며 시리즈·기간과
cutoff별 assertion 공개/수신 근거를 검사한다. 실제 자료에는 이 근거가 없다.
action의 event_date가 이전 선언일일 수 있으므로 정답 query는 해당 증권의 적격 action 버전을
별도로 읽고, 보유 구간 적용은 effective_date/ex_date로 검사한다. 선언일 범위로 잘라 필요한
미래 효력 action을 잃지 않는다. action snapshot과 숫자에 사용한 action IDs를 함께 보존한다.
split-adjusted dividend amount를 당시 현금 지급액으로 간주하지 않는다. price_return이
ready여도 현금 의미/지급일이 미확인인 total return은 별도 blocked일 수 있다.

증권별 security_trading_state는 달력과 분리한다. 상장 전/폐지/정지/미확인 구간을 reason으로
보존하고 exit가 없다고 다음 관측이나 마지막 관측으로 바꾸지 않는다.

| 상태 | 조건 |
|---|---|
| ready | endpoint/검증된 coverage/state/action과 시각 조건 충족 |
| pending | simulation_asof 또는 label_asof에 exit open 미도래, label_asof의 정상 지연 범위 내 수신 대기, 처리 지연 |
| blocked | 과거 endpoint 누락, 필수 의미/ID/coverage/state/evidence 부족 |
| invalid | 시간 순서/0·음수 가격/계약 오류 |

label_asof는 정답 버전 평가 query cutoff이고 simulation_asof는 미래 미도래/과거 누락 판정
시각이다. label_asof<=simulation_asof를 요구한다. normal_label_delay_hours(default48)는
exit 세션 close 이후의 정상 대기 가정이며 샘플 끝을 영구 pending으로 만들지 않는다.
이 가정은 제공처 SLA가 아니다. 추가 processing delay도 별도 기록한다.
simulation_asof가 나중이어도 과거 label_asof의 정상 수신 대기를 과거 자료 누락으로 바꾸지
않는다. 지연 범위 판정은 평가 기준 시각 label_asof를 사용하며, 그 평가 시각에도 이미 지연
범위를 넘은 누락은 blocked다. pending 정답은 숫자 및 계산된 holdings 결과를 내보내지 않는다.

label_available_at은 사용한 가격·action·coverage·state의 policy availability 및 target open
중 최대값+명시 processing delay이다. research_assumed는 NOT_STRICT_PIT로 표시한다.
DB insertion/실제 이번 계산 시각은 메타데이터이며 과거 수신/계산의 증거로 쓰지 않는다.
새 label_asof나 정정으로 생성한 정답은 새 내용 hash/label_version ID로 추가한다.
training_asof 선택은 frozen dataset의 정답이 ready이고 그때 available인지 별도로 검사한다.

원본 event_type의 splits/dividends와 합성 split/dividend는 명시 alias rule v1로 해석한다.
원본 필드는 바꾸지 않고 해석 정책을 ledger에 기록한다. effective/ex 날짜 미상은 event_date로
자동 채우지 않는다. 날짜 추정이나 미검증 현금 의미로 실제 총수익을 확정하지 않는다.
진입 전 선언된 action도 효력일/배당락일이 없으면 보유 구간 밖임을 입증할 수 없어 차단한다.
유한 입력에서 수량·현금·투자액·정답 계산이 overflow/underflow되면 invalid로 반환한다.

## 별도 선택 계약 3.1.0

기본 계약은 계속 3.0.1이다. 별도 dataset builder config의
`label_definition_version="3.1.0"` 또는 계산 함수의 같은 인수로만 선택한다.
기존 운영 config/전향 실행기의 연결은 바꾸지 않는다. 정답 definition_version과
config·내용 hash가 구별되며 기존 snapshot을 덮어쓰지 않는다.

가격·현금 수익률 공식, 진입/종료, raw(as_traded) 의미는 같다. 적격성·자료 의존과
정지 판정 의미가 달라 전체 계약 호환성을 선언하지 않는다.
가격 계산은 검토로 **일반 현금배당**임이 확인된 버전만 날짜/금액/지급일/현금 순서
검사 전에 제외한다. 가격 ledger·used_action_version_ids·label_available_at에도 제외한다.
현금 계산은 기존 배당 의미·통화·지급일·같은 날 순서 검사를 유지한다.
cash_total_return_status/available_at과 used_cash_action_version_ids를 별도로 기록한다.
현금 자료 충돌·누락·처리 지연은 가격 정답을 바꾸지 않는다.

기존 hash-bound corporate_action_coverage assertion에 다음 검토 내용을 요구한다.
근거의 완전성·수신/이용 시각·시리즈·기간 검사는 계속 필요하다.

- `action_classifications`: 정확한 action version ID별 `share_split` 또는
  `ordinary_cash_dividend`. 일반 split/dividend 필드만으로 자동 분류하지 않는다.
  특별배당·분사·합병·권리배정·미분류 사건은 일반 배당으로 통과시키지 않는다.
- `price_action_version_ids`: 보유 구간에 필요한 비현금 사건의 버전 목록.
  지원하지 않는 유형 또는 필요한 버전 누락은 가격 정답을 차단한다.
- `cash_action_version_ids`: 필요한 일반 현금배당 버전 목록. 누락은 현금 정답을 차단한다.
  목록 필드 부재를 빈 목록으로 대체하지 않는다.
- 선택 `action_effective_dates`: 버전별 검토된 효력/배당락 날짜. 다른 horizon 때문에
  조회된 충돌/미수신 버전이 구간 밖임을 증명할 때만 사용한다. 선언일로 대신하지 않는다.

기존 security_trading_state assertion의 상장/폐지·verified_through 검사는 유지한다.
추가 `halt_intervals=[{start_at, resumed_at}]`와 `halt_coverage={start_at, end_at,
status:"verified_complete", includes_prior_unresolved_halts:true}`가 필요하다.
coverage는 양 endpoint를 포함하며, 시작 전에 발생한 미해결 정지도 조사한 근거여야 한다.
timezone을 가진 실제 timestamp를 UTC로 정규화하고 `[start_at,resumed_at)`로 검사한다.
시작은 포함하고 재개는 구간에서 제외하되 **재개 시각과 목표 시각이 정확히 같으면 별도로
차단**한다. 재개 시각 누락은 미해결이다. 날짜뿐인 halted_dates/구간, timezone 없는 시각,
빈 목록만 있고 완전성 근거 없는 상태는 차단한다. 시가 이후 시작한 정지는 앞선 시가를 막지 않는다.

이 판정은 가격 기반 정답 적격성이다. `endpoint_price_evaluation_only=true`,
`order_execution_verified=false`를 기록하며 공식 시가 경매 유효성이나 체결을 보증하지 않는다.
운영 자료에 위 verified assertion을 새로 만들지 않았다. 합성 검사만으로 근거를 채우지 않는다.
고정 artifact는 원래 소스 hash와 3.0.1 target_contract를 요구하므로 그대로 3.1.0에 사용할 수 없다.
