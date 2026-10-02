# IBM 동일 구간 제공처 비교

2026-10-02에 실제 확보한 Alpha Vantage raw daily와 Yahoo historical OHLC를
2024년252일 및2026-05-01–10-01의106일, 총358거래일에서 비교했다.
숫자/근거 행은 로컬 수집 산출물 sample_quality.json의 cross_provider_comparison에 있다.
공개 저장소에는 sample_quality.summary.json 집계만 포함한다.
두 곳은 독립 서비스지만 upstream feed 독립성은 미확인이다.

각 OHLC 상대 차이 tolerance=1e-5, volume=1e-4를 넘는 필드를 **검토 후보**로
기록했다. 이는 정답 판정 기준이 아니다. 비교 정의상 Alpha는 as-traded,
Yahoo는 분할 조정 OHLC이며 IBM 이 표본에는 분할 이벤트가 관측되지 않았다.
[Alpha 공식 raw 정의](https://www.alphavantage.co/documentation/)와
[Yahoo adjusted close 정의](https://in.help.yahoo.com/kb/adjusted-close-sln28256.html)를
확인했다. 배당 포함 adjclose와 raw close를 서로 직접 비교하지 않았다.

| 필드 | tolerance 초과 수 | 조사 결과 |
|---|---:|---|
| close | 0 | 표본의 close 상대 차이는 tolerance 이내; 완전한 원천 독립 검증 아님 |
| open | 13 | Yahoo의 소수2자리 표현과 Alpha의 더 세밀한 값. float 표현오차와 반올림 가능성 |
| high | 46 | 같은 정의 차이 후보 |
| low | 33 | 같은 정의 차이 후보 |
| volume | 60 | 일부 큰 불일치로 단순 반올림으로 설명 불가 |

가격 차이 최대 약0.005015 USD였다. 가격의 상세 호가정밀도·OHLC rounding이
원인일 가능성은 **추론**이고 제공처가 모든 차이를 확인한 것은 아니다.
거래량은 일부 날짜에서 두 제공처 간 차이를 관측했으며 표본 최대 상대 차이는약16.17%였다. 서로 다른 거래 세션/거래 조건/정정 반영/
원천 feed·aggregation 가능성을 조사했지만 현재 확인한 공식 설명으로 거래량
포함 범위와 역사적 최초 버전을 확정하지 못했다. 어느 쪽도 정답으로 선택하지 않는다.

전량 수집 전 조건: 동일 세션·대상 거래·조정방식/volume split basis의 공식 정의,
원천·정정 버전 정보, 제3의 권한 있는 원자료 표본을 확보해서 다시 비교한다.

2026-07-14 IBM close가 전일 대비약25.2076% 하락한 값은 양쪽에서 관측되어
large_price_move 플래그를 유지했다. 같은 날짜의 잠정 실적 및 설명은
[IBM의 SEC 공시 Exhibit 99.1](https://www.sec.gov/Archives/edgar/data/51143/000005114326000070/ibm-20260714xex991.htm)에서
확인했다. 이는 경제적 움직임의 설명 후보이며 가격 변화의 인과관계나 원천 가격의
정확성을 입증하는 것은 아니다. 분할로 처리하거나 삭제하지 않았다.
