# 데이터 요구사항

작성일: 2026-10-02. 조사에 앞서 정의한 필드 계약이다. 아래는 필요 필드이며
현재 확보했다는 뜻이 아니다. 공통: source, provider_record_id(있을 때),
schema_version, raw_sha256, fetched_at_utc, publication_time(없으면 null),
revision_time(없으면 null), effective_from/to, 시간 정확도·타임존·정의.

| 우선순위 | 데이터 | 필요한 필드와 조건 |
|---|---|---|
| 필수 | 일별 **원가격** OHLCV | 증권 ID, 당시 ticker, exchange, trading_date, currency, raw open/high/low/close, volume, 거래 세션, 단위, null/0/미거래 상태. 조정 OHLC·adj_close·조정계수는 별도 정의. 10–15년 범위는 종목별 입증 필요 |
| 필수 | 기업행동 | action_id, 증권 ID, 유형, 선언/공개일시, 효력일/배당락일/기준일/지급일, 원래 현금액·통화, split numerator/denominator, 합병 대가, 관련 증권 ID, 정정 버전. 소급 분할 조정된 배당액과 당시 현금액 구별 |
| 필수 | 식별자·코드 이력 | 검증된 증권/발행사 ID와 관계(CIK는 발행사), provider ID, ticker, exchange, share class, security type, valid_from/to, known_from/to, 출처 근거. 임시 로컬 ID는 영구 ID가 아님 |
| 필수 | 상장·폐지 | 증권 ID, 거래소, 최초 거래일, 상장일, 폐지 효력일, 최종 거래일, 이유, 거래정지 구간, 합병/청산 지급액·일자, 당시 공개 시각. 현재 active 플래그만으로 부족 |
| 필수 | 거래일 달력 | exchange, session date, open/close UTC, 현지 TZ, 조기폐장, 휴장·예외휴장, 공지 시각/버전. 금융기관 달력과 거래소 달력 구별 |
| 필수 | 시장 비교 | 지수 ID/ETF 증권 ID, 날짜, 원/수정가격·배당/총수익 정의, 세션, 통화. SPY는 비용·추적오차가 있는 ETF proxy이며 S&P 500 총수익 지수 자체가 아님 |
| 후속 | 재무 | CIK/증권 매핑, accession, form, fiscal period, concept, value, units, filed date, **acceptance 시각**, 원문, amendment/restatement 링크·최초 버전. 최신 companyfacts만으로 당시 값 복원 불가 |
| 후속 | 거시 | series_id, observation period, 최초 발표값·발표 시각, vintage/realtime_start/end, 수정값·발표 시각, 계절조정/단위/출처. vintage 날짜는 정밀 발표 시각과 다름 |
| 후속 | 당시 업종 | 분류 체계/버전, 증권 ID, industry code, valid_from/to, known_from/to. 현재 업종 소급 사용 금지 |
| 후속 | 뉴스·공시 | document_id/URL, issuer/entity, 원문 bytes/hash, posted_at, updated_at, 실제 수신 시각, TZ/정밀도, 수정/삭제 이력, 라이선스. 사건 발생 시각과 기사 게시 시각 구별 |
| 선택 | 컨센서스 | contributor, estimate period, metric, estimate, consensus construction, as_of/known_at, revision history, entitlement |
| 선택 | 관계 | entity IDs, relation type/direction, 공개 근거 document_id, asserted/effective/known intervals, 수정 이력 |
| 선택 | 옵션·호가·틱·공매도·ETF 흐름 | contract/venue ID, event/receipt 시각, session, price/size, condition, expiry/strike, 공개 지연·수정/취소, 흐름 정의·권한 |

현재 생존 종목만으로 만든 목록은 과거 전체 종목군을 대체할 수 없다.
과거 유동성 선정에는 당시 존재한 보통주, 폐지 종목, 당시 코드와 당시 거래량이
필요하다. 미래 거래량·현재 업종·현재 폐지 상태를 과거 선정에 노출하면 안 된다.
12개월 샘플과 별도 기업행동 구간은 10–15년 전체 커버리지의 증명이 아니다.
