# 정규화 스키마 1.0.0

원본 JSON/CSV/HTML/PDF는 `data/raw/<source>/<request_id>/<sha256>.bin`에 그대로
보관한다. `.bin`은 확장자일 뿐이며 내용은 제공처 응답 그대로다. 정규화 JSON은
`bars`, `actions`, `directory`, `documents`, `metadata`, `provenance`를 가진다.
실행 시 관측한 필드 계약은 providers/public.py, 자료형 검사는 schemas/__init__.py다.
일별 가격에는 데이터 공개 시각을 만들어 넣지 않는다.

| 객체 | 실제 관측한 제공처 필드 | 정규화 필드·자료형·의미 |
|---|---|---|
| Yahoo bars | chart.result[0].timestamp, meta.exchangeTimezoneName/currency/symbol/instrumentType, indicators.quote[0].open/high/low/close/volume, indicators.adjclose[0].adjclose | trading_date: YYYY-MM-DD(제공처 TZ로 변환), source_timestamp_utc: ISO8601(원본 bar timestamp, 공개 시각 아님); OHLC: finite float/null, volume: int/null, adjusted_close: float/null; price_semantics=vendor_split_adjusted_ohlc_not_raw, adjusted_close_semantics=split_and_distribution_adjusted_current_vintage |
| Yahoo actions | events.splits[*].date/numerator/denominator, events.dividends[*].date/amount | event_date: 날짜(공개일 아님), type: splits/dividends, numerator/denominator/amount: float; dividend amount_semantics=provider_reported_split_basis_unverified. declared/record/payment/revision/publication은 null |
| Alpha raw daily | Meta Data, Time Series (Daily)[date][1. open,2. high,3. low,4. close,5. volume] | 숫자 문자열을 유한 float/int로 파싱, price_semantics=raw_as_traded, adjusted_close=null. Last Refreshed는 날짜 수준 제공처 메타이며 publication_time으로 승격하지 않음 |
| Nasdaq current directory | nasdaqlisted: Symbol, Security Name, Market Category, Test Issue, Financial Status, Round Lot Size, ETF, NextShares; otherlisted: ACT Symbol, Security Name, Exchange, CQS Symbol, ETF, Round Lot Size, Test Issue, NASDAQ Symbol | source_fields 그대로, ticker 문자열; current_snapshot_only; common_stock_classification=unverified; permanent_security_id/listing_date/delisting_date=null. ETF=N은 보통주를 입증하지 않음. footer 생성표시는 metadata에 보관 |
| Official documents | HTTP body | document_id/URL; content_verified=false는 **자동 정규화기의 의미**. 내용 검토 결과는 reports/sample_cases.json의 별도 근거 기록. HTTP200만으로 사실 검증 완료를 주장하지 않음 |

모든 행에 raw_sha256/fetched_at_utc를 붙인다. 가격·기업행동 로컬 ID는
`temporary:<source>:<requested_symbol>`이며 영구 증권 ID도 발행사 ID도 아니다.
request symbol은 조회에 사용한 코드이며 당시 거래 코드가 아닐 수 있다.
Yahoo META의 2022년 FB 기간이 실제 반례다. Meta의 현재 instrumentType은
현재 스냅샷일 뿐 과거 보통주 분류의 증명이 아니다.

volume_semantics/provider session 범위는 명세 확인 부족으로 미검증이다.
정규화 float은 계산용 근삿값이고 정확한 응답 문자열/바이트는 원본에 남는다.
결측(null), 보고된 0거래량, 날짜 미관측은 구분한다. 거래량0으로 거래정지를
단정하거나 결측 거래일을 0 OHLCV로 채우지 않는다. 큰 움직임은 플래그만 남긴다.

manifest는 안전한 요청 필드, request_id, fetched_at_utc, recorded_at_utc,
network_download_performed, HTTP status와 시도 횟수, status, raw_path,
sha256, file_size_bytes, row_count(가격+기업행동+directory), document_count,
가족별 counts, actual_data_period(가격 관측 최소/최대일), empty_response,
body_empty, normalized_path/normalized_sha256, config_sha256, code_sha256,
schema_version, collector_version을 기록한다. 실패의 row_count/empty_response는
알 수 없으면 null이다. 문서/현재 directory의 가격 기간은 null이다.

인증 파라미터·헤더와 URL 전체는 기록하지 않는다. 문서 URL은 https, query=id만
허용하며 다른 파라미터·userinfo가 있으면 거부 후 안전한 URL만 남긴다.
환경변수 키를 응답이 되비추면 원본 저장을 보류하고 secret_echo_raw_withheld로
기록한다. 이때 원본 보관보다 비밀키 보호를 우선한 예외임을 명시한다.

동일 성공 request는 raw/normalized 해시 확인 후 재사용한다. 코드가 바뀌면
기존 raw를 오프라인 재정규화하며 수신 시각은 그대로, 처리 시각은 별도로
남긴다. --refresh는 새 HTTP 수신 기록을 보관한다. 동일 bytes는 같은 raw 파일을
재사용하고 bytes가 달라지면 별도 버전이다. 부분 실패는 성공 원본을 보존하고
실패 request만 재시도한다. 파일은 임시 파일→fsync→원자적 replace로 저장한다.
수집기는 단일 프로세스·순차 실행 계약이며 동시 프로세스의 ledger locking이나
분산 수집은 이번 범위에 포함하지 않는다.
