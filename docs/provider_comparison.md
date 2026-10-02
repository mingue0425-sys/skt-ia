# 제공처 조사와 접근 검증

확인일: **2026-10-02**. 아래 과거 범위·상품·요금·약관은 이 날짜의 공식 문서
설명이며, 명시한 샘플 외에는 실제 데이터 접근을 입증하지 않는다. 웹 조사와 로컬
HTTP 접근은 구분한다. 공개 다운로드 성공은 저장·학습·재배포 허가의 증명이 아니다.
문서의 최신 lastupdated/정정 기능은 역사적 당시 실제 수신 기록과 다르다.

상태: A=실제 다운로드와 검증 완료, D=공식 문서에서 확인했지만 미검증,
L=접근 제한으로 확인 불가, U=제공하지 않거나 요구사항 미충족.
각 후보의 용도별 상태를 표에 쓴다. 미확인은 허용으로 해석하지 않는다.

## 필수 데이터의 독립 후보 매핑

| 필수 항목 | 후보 1 | 후보 2 이상 | 현재 확인한 공백 |
|---|---|---|---|
| 원가격 OHLCV | Alpha Vantage raw daily (IBM A) | Massive adjusted=false (D/L), Tiingo raw (D/L), Sharadar closeunadj+환산 (D/L) | Yahoo OHLC를 원가격으로 쓰면 안 됨. 6개 보통주 모두의 raw는 미확보 |
| 배당·분할 | Yahoo events (A) | Massive splits/dividends (D/L), Alpha daily adjusted (D/L), Sharadar ACTIONS (D/L) | 선언 시각·당시 현금액·정정 버전·합병 현금흐름 부족 |
| 식별자·코드 이력 | Massive FIGI+날짜별 tickers/events 설명 (D/L) | Sharadar permaticker+ACTIONS (D/L), Nasdaq Daily List (D/L) | 검증된 영구 증권 ID 연결 없음 |
| 상장·폐지 | Massive active=false/delisted_utc (D/L) | Sharadar isdelisted/lastpricedate/actions (D/L), Alpha LISTING_STATUS (D/L), Nasdaq Daily List (D/L) | 전체 과거 종목군·폐지 가격/대가 미확보 |
| 거래일 달력 | NYSE 공식 달력 (2024/2026 A) | Nasdaq 공식 holiday/calendar (2026 A, 과거 링크 D) | 10–15년 공지 버전·전체 예외휴장 미검증 |
| 시장 비교 가격 | Yahoo SPY (A) | Massive/Tiingo ETF (D/L), Sharadar funds/SFP (D/L), Alpha ETF daily (D/L) | ETF proxy는 원래 지수·총수익 지수와 다름 |

후보는 서로 다른 서비스다. **원천 거래소/feed의 독립성은 확인하지 못했다.**
따라서 IBM 2곳 비교에서 일치해도 독립된 진실 검증이나 거래 세션 동일성을 입증하지 않는다.

## Yahoo Finance

| 조사 항목 | 확인 내용 |
|---|---|
| 공식 문서 URL | [Historical download help](https://in.help.yahoo.com/kb/finance/download-historical-data-yahoo-finance-sln2311.html), [Adjusted close help](https://in.help.yahoo.com/kb/adjusted-close-sln28256.html) |
| 확인 일자 | 2026-10-02 |
| 종류·필드 | 실제 chart timestamp, quote OHLCV, adjclose, events.splits/dividends, meta.currency/timezone/symbol/instrumentType. chart API의 공식 지원 계약은 찾지 못함 |
| 과거 범위 | 실제 2024년 7개 quotes, META 2022년 구간, IBM 2026년 구간. 전 종목 10–15년 지원은 미입증 |
| 무료·유료 | 이번 chart 요청은 무인증 공개 접근 성공. 공식 historical download UI의 계정·유료 조건과 API 사용권을 동일시하지 않음 |
| 인증 | 이번 chart에 키·계정·cookie 사용 안 함 |
| 호출 제한·bulk | 공식 chart limit/bulk 미확인. 2초 간격·최대3시도. 대규모 수집에 적합하다고 주장하지 않음 |
| 원·수정 의미 | adjclose 공식 정의는 분할·배당 포함. OHLC는 이번 NVDA split 경계상 분할 조정된 형태로 관측; **raw 미충족**. 모든 특수행동의 조정 정의는 미확인 |
| 기업행동 | 실제 27개 이벤트(26 배당, 1 분할) 확인. 사건일은 공표일이 아님. 일부 배당액이 분할 기준으로 소급 표현됨 |
| 폐지·코드 이력 | META 요청에 과거 FB기간 반환; FB HTTP400, ATVI HTTP404. 영구 ID·전체 폐지 이력 없음 |
| 공개 시각 | daily timestamp는 bar 관측 시각. 정밀 publication/first receipt 없음 |
| 수정·정정 | 현재 과거 값과 현재 adjclose만 수신. 역사적 revision API 미확인 |
| 저장·분석·학습·재배포 | [Yahoo 약관 URL](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html)은 웹 조사 HTTP999로 본문 미확인. help는 다운로드 기능 설명만 확인. 네 항목의 자동수집/저장/학습/재배포 권한을 허용으로 추정하지 않음 |
| 실제 검증 상태 | 가격·events A, raw·영구ID·역사적 PIT U, chart 약관/보증 L/미확인 |
| 적합 용도 | 제한된 로컬 수집기·스키마·기업행동 오류 검증 |
| 미충족 | raw OHLCV, 안정된 공식 API, 폐지군, 영구증권ID, 최초 공개/수정 버전, 라이선스 명확성 |

## Alpha Vantage

| 조사 항목 | 확인 내용 |
|---|---|
| 공식 문서 URL | [API docs](https://www.alphavantage.co/documentation/), [limits/support](https://www.alphavantage.co/support/), [terms PDF](https://www.alphavantage.co/terms_of_service/) |
| 확인 일자 | 2026-10-02 |
| 종류·필드 | daily raw OHLCV 실제 문자열 필드 확인. daily adjusted의 raw OHLCV/adjusted close/dividend/split, LISTING_STATUS의 날짜별 active/delisted는 D |
| 과거 범위 | 문서 daily 25+년. **실제 IBM 1999-11-01–2026-10-01, 6,770행**; 기본 compact100. 다른 종목 과거 범위는 미검증 |
| 무료·유료 | compact 무료/유료, full 및 adjusted는 문서상 premium. IBM 공식 demo full 예외 성공을 일반 무료권한으로 해석하지 않음 |
| 인증 | 공식 공개 demo 사용. 실제 계정 키는 환경변수 ALPHAVANTAGE_API_KEY, 이번 개인 키/계정 생성 없음 |
| 호출 제한·bulk | 무료25요청/일; 수집기 12초 간격, 영속적 로컬25/UTC일 budget. 다른 외부 프로세스의 호출은 집계 못함. 종목별 full, 공통 bulk는 미검증 |
| 원·수정 의미 | TIME_SERIES_DAILY는 문서상 as-traded raw. adjusted endpoint는 raw OHLCV+조정 종가 및 events 별도. 이번 adjusted는 접근 미검증 |
| 기업행동 | 이번 raw endpoint에는 없음. adjusted/splits/dividends endpoints는 D |
| 폐지·코드 이력 | LISTING_STATUS 문서는 2010-01-01 이후 날짜별 상태 지원. demo 접근과 폐지 가격/코드 연결은 미검증 |
| 공개 시각 | Last Refreshed는 날짜. 거래일 close+60분 시점 availability 미입증 |
| 수정·정정 | 현재 과거 raw series만 확인; 최초 수신 버전 archive 없음 |
| 저장·분석·학습·재배포 | 약관의 짧은 원문: “personal, non-commercial use”. 개인 투자 분석·연구·testing 범위와 회사/기관 사용의 별도 서면 합의 조건 확인. 영구 보관·ML 학습·원자료 재배포를 별도 허용한다고 추정하지 않음 |
| 실제 검증 상태 | IBM raw A; 나머지 prices·adjusted/events·LISTING_STATUS D/키 권한 L; 역사적 PIT U |
| 적합 용도 | IBM raw와 Yahoo 비교; 권한 확보 후 raw 후보 |
| 미충족 | 100종목 전체권한·증권ID/코드연결·폐지 cashflow·시각/역사버전 |

실접근 차이: `outputsize=compact`를 명시한 demo URL은 HTTP200 Information로 거절,
공식 문서 예제처럼 기본값을 생략하면 compact100이 성공했다. 거절 원본도 보관했다.
수집기는 현재 공식 예제 URL을 사용한다. 반환의 실제 schema/상태로 판정한다.

## Massive (기존 Polygon 계열)

| 조사 항목 | 확인 내용 |
|---|---|
| 공식 문서 URL | [Custom bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars), [All tickers](https://massive.com/docs/rest/stocks/tickers/all-tickers), [splits/dividends update](https://www.massive.com/blog/new-splits-and-dividends-endpoints), [ticker events FAQ](https://massive.com/knowledge-base/categories/stocks) |
| 확인 일자 | 2026-10-02 |
| 종류·필드 | bars o/h/l/c/v/t/vw/n; tickers ticker/type/primary_exchange/CIK/FIGI/active/delisted_utc/last_updated_utc; split/dividend dates/ratios/cash adjustment factors는 D |
| 과거 범위 | bars 문서 2003-09-10부터; Basic2년, Starter5년, Developer10년, Advanced 전체. 개별 종목·metadata start coverage 미검증 |
| 무료·유료 | 문서 당시 Basic 무료, individual Starter29/Developer79/Advanced199 USD/월. 기관/상업 및 옵션/지수 권한은 별도이며 최종 견적 아님 |
| 인증 | API key 필요. 계정·키 권한으로 실제 조회하지 않음 |
| 호출 제한·bulk | [공식 rate FAQ](https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis): 미구독 asset class5/min, 해당 유료 class unlimited 설명. aggregates limit<=50000 및 next_url, flat files 문서; 실제 bulk/계약 미검증 |
| 원·수정 의미 | adjusted=false로 split-unadjusted; 기본 split-adjusted. dividend-adjusted aggregates는 제공하지 않는다는 [공식 설명](https://massive.com/knowledge-base/article/is-massives-stock-data-adjusted-for-splits-or-dividends) |
| 기업행동 | 새로운 stocks/v1 splits/dividends 설명 확인. 구 endpoints와 신규 fields를 혼용하지 않음 |
| 폐지·코드 이력 | tickers date/active=false/FIGI/delisted_utc, events rename 설명. ticker-events 추정 URL은 열리지 않아 endpoint 스키마 미확인 |
| 공개 시각 | t는 interval 시작, last_updated_utc는 metadata 최신 수정 기준. 최초 공표/과거 수신과 다름. EOD close+60분 보장 미확인 |
| 수정·정정 | changelog/updated field는 확인; 변경 전 원자료 조회 보장은 미확인 |
| 저장·분석·학습·재배포 | [Market Data terms](https://massive.com/legal/market-data-terms-of-service): 짧은 원문 “personal, non-business, and non-commercial purposes”. individual은 개인용, 회사·기관·타사용자 서비스는 별도 계약. [API docs](https://www.massive.com/docs)는 ML/flat files 용도를 설명하나 구체 저장기간·학습/모델 배포 라이선스를 자동 부여하지 않음 |
| 실제 검증 상태 | 공식 기능 D, API 접근 L(키/상품 권한 미확인), 역사적 최초버전 U/미입증 |
| 적합 용도 | raw/기업행동/과거 ticker/폐지 metadata의 통합 후보 |
| 미충족 | 실표본·rename ID 연결 검증·종목별 15년·원본vintage·17:00 cutoff·권한 |

## Tiingo

| 조사 항목 | 확인 내용 |
|---|---|
| 공식 문서 URL | [EOD](https://www.tiingo.com/documentation/end-of-day), [general](https://www.tiingo.com/documentation/general), [pricing](https://www.tiingo.com/about/pricing), [terms](https://app.tiingo.com/tos/) |
| 확인 일자 | 2026-10-02 |
| 종류·필드 | date, raw OHLCV, adjOpen/High/Low/Close/Volume, divCash, splitFactor; metadata ticker/name/exchangeCode/startDate/endDate |
| 과거 범위 | startDate/endDate가 종목별 실제 범위; [공식 backtesting 설명](https://www.tiingo.com/blog/backtesting-stocks/)은 1962부터 설명. 모든 종목의 깊이를 보장하지 않음 |
| 무료·유료 | Starter 평가 및 유료/redistribution plans 설명. 실제 quota/가격별 권한 미확인 |
| 인증 | token 필요. 이번 계정 생성·키 발급·API 접근 없음 |
| 호출 제한·bulk | current exact quota 미확인. CSV와 supported_tickers.zip, [cached history+latest 갱신](https://www.tiingo.com/kb/article/the-fastest-method-to-ingest-tiingo-end-of-day-stock-api-data/) 권장. event 있으면 history 재수집 권장 |
| 원·수정 의미 | raw와 CRSP 방식 split/dividend adjusted 가격 별도. divCash의 date는 exDate |
| 기업행동 | divCash/splitFactor 문서 확인. 선언/지급/정정시각은 미검증 |
| 폐지·코드 이력 | 지원 목록에 예약코드도 있어 제공 중이라는 증명이 아님. metadata endDate는 공식 폐지일이 아님; 전체 폐지군·rename 이력 미검증 |
| 공개 시각 | 문서의 원문 “5:30 PM EST” 및 evening corrections “8 PM EST”. **16:00 정규장+60분보다 늦을 가능성**. EST/ET/DST 표현과 실제 송신 시각 추가 확인 필요 |
| 수정·정정 | 저녁 수정 후 현재 history 갱신. 과거 initial/correction vintage API 보장 미확인 |
| 저장·분석·학습·재배포 | general은 Basic/Power internal/personal 및 재배포 금지. terms의 Derived Product는 대체재·복원가능성이 없어야 하는 조건부 허용; forecasts/model parameters 예시도 조건부. 원자료 저장기간·종료 후 삭제·ML 학습 범위는 실제 supplemental license까지 확인 필요 |
| 실제 검증 상태 | 명세 D, token 접근 L, cutoff 및 역사vintage 미입증 |
| 적합 용도 | raw/adjusted 병렬 표본과 오류 검사 후보 |
| 미충족 | 권한, 17:00 availability, 폐지/ID/rename, 당시 버전 |

## Sharadar / Nasdaq Data Link

| 조사 항목 | 확인 내용 |
|---|---|
| 공식 문서 URL | [Nasdaq SEP](https://data.nasdaq.com/databases/SEP/documentation), [direct stocks](https://sharadar.com/docs/stocks), [actions](https://sharadar.com/docs/actions), [tickers](https://sharadar.com/docs/tickers), [adjustment definition](https://sharadar.com/blog/posts/sharadar-stock-prices-fund-prices-and-adjustments) |
| 확인 일자 | 2026-10-02 |
| 종류·필드 | OHLCV(split adjusted), closeunadj, closeadj(split/dividend/spinoff), lastupdated. TICKERS permaticker/ticker/exchange/isdelisted/category/CUSIPs/first/last dates; ACTIONS split/dividend/rename/list/delist/merger |
| 과거 범위 | Nasdaq 설명1998부터 약21,000 active/delisted; direct stocks Dec1997, actions Jan1998. 제공 경로/상품별 차이를 실표본 확인해야 함 |
| 무료·유료 | premium. Nasdaq free sample은 계정사용자에 2018-09–12의 지정 종목; direct personal plans 별도 |
| 인증 | 계정/API key·상품권한 필요. 이번 조회/구매 없음 |
| 호출 제한·bulk | 직접 API10,000 기본limit, skip, CSV zip5/10/full; Nasdaq Tables filters/exports. 시간당/일당 실제 quota 미확인. bulk는 대형 파일이라 chunk/스트리밍 필요 |
| 원·수정 의미 | OHL raw는 `OHL * closeunadj/close`, raw volume은 `volume * close/closeunadj`의 **환산값**; closeadj를 volume에 적용하지 않음. 직접 raw OHLV 관측치와 구별 |
| 기업행동 | 상세 actions와 merger/delist reasons 문서. 사건과 공표/수신시각 구별 필요 |
| 폐지·코드 이력 | permaticker는 Sharadar 고유 share-class 불변 ID라는 문서 설명. 실제 ticker 재사용·합병/secondary class 연결은 미검증 |
| 공개 시각 | direct 문서 **17:30/23:30 US Eastern** 배포. 요구17:00보다 늦어 해당일 feature availability 미충족 가능 |
| 수정·정정 | lastupdated는 날짜이며 원본revision archive와 다름. SF1 ART/ARQ 개념만으로 정확한 당시 시각을 보장하지 않음 |
| 저장·분석·학습·재배포 | [direct personal terms](https://sharadar.com/terms)는 자연인 개인용, 회사/기관/전문 사용 금지; raw/복원가능 derivation 재배포 금지, 비복원 모델·통계 조건 설명. [Nasdaq terms](https://data.nasdaq.com/terms)는 Order Form 내 receive/use/process/store, derived/배포 별도 조건. 페이지가 2026-11-01 적용 변경도 안내하므로 현재 적용본·Order Form 확인 필요 |
| 실제 검증 상태 | 문서 D; 계정·구매 없으므로 데이터 L; 최초 공표버전/17:00 U/미입증 |
| 적합 용도 | 생존편향 제어·영구ID·폐지군·기업행동 후보. 같은 Sharadar의 두 경로는 독립 제공처 두 곳으로 계산하지 않음 |
| 미충족 | raw OHLV 환산정의 실검증·과거 알려진 업종·수신archive·기관 권한·cutoff |

## Nasdaq Trader: directory / Daily List

| 조사 항목 | 확인 내용 |
|---|---|
| 공식 문서 URL | [Directory definitions](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs), [current lookup](https://nasdaqtrader.com/trader.aspx?id=symbollookup), [Daily List specification PDF](https://www.nasdaqtrader.com/content/technicalSupport/specifications/dataproducts/dlcompletespec.pdf) |
| 확인 일자 | 2026-10-02 |
| 종류·필드 | 현재 directory의 Symbol/Security Name/ETF/Test Issue/Exchange 실측. Daily List effective/first date/new symbol/delist reason은 문서만 확인 |
| 과거 범위 | directory는 현재 거래일. Daily List most recent/archived additions/deletions/name/symbol/actions; archive 시작일·전체 범위 미확인 |
| 무료·유료 | 공개 directory 접근 성공. Daily List archive는 상품/권한 확인 필요 |
| 인증 | directory https 무인증; Daily List secure FTP 문서 |
| 호출 제한·bulk | directory 전체 txt, 하루에도 갱신; 공식 numeric quota 미확인. Daily List FTP files 문서 |
| 원·수정 의미 | 가격 없음 |
| 기업행동 | 현재 directory 없음, Daily List dividend/일부action 명세 |
| 폐지·코드 이력 | 현재 파일만으로 없음. Daily List의 new symbol/delist reason 후보 |
| 공개 시각 | directory footer file creation marker는 파일 생성표시, 행별 최초 공개/역사수신 아님 |
| 수정·정정 | 현재 스냅샷 덮어쓰기와 archive 정정 정책 차이 미확인 |
| 저장·분석·학습·재배포 | directory 자동 저장/학습/재배포의 구체 허가 미확인. [Nasdaq AI Policy](https://www.nasdaqtrader.com/content/AdministrationSupport/AgreementsData/Data_AI_Policy.pdf)는 기존 license를 대체하지 않고 명시 범위 내 이용 요구. 적용 상품과 exemption 확인 필요 |
| 실제 검증 상태 | directory 5,633+7,661행 A; 과거 security master U; Daily List D/L |
| 적합 용도 | 현재 코드·ETF/Test Issue 체크, 과거 archive의 추가 후보 |
| 미충족 | 현재목록은 보통주/과거Universe가 아님; 영구ID·역사 상태·전체 archive 권한 |

## NYSE / Nasdaq 공식 달력과 derived reference

| 조사 항목 | 확인 내용 |
|---|---|
| 공식 문서 URL | [NYSE hours](https://www.nyse.com/trade/hours-calendars), [2024 PDF](https://www.nyse.com/publicdocs/ICE_NYSE_2024_Yearly_Trading_Calendar.pdf), [2026 PDF](https://www.nyse.com/publicdocs/nyse/ICE_NYSE_2026_Yearly_Trading_Calendar.pdf), [Nasdaq calendar](https://www.nasdaqtrader.com/Trader.aspx?id=Calendar), [exchange_calendars project](https://github.com/gerrymanoim/exchange_calendars) |
| 확인 일자 | 2026-10-02 |
| 종류·필드 | holidays/early-close official notices. package XNYS schedule의 open/close UTC 및 날짜는 파생 reference |
| 과거 범위 | NYSE2024/2026 파일과 Nasdaq2026 page 실제 확보. Nasdaq 페이지 과거2021–25 링크 D. package2022–26 생성; 과거15년 공지archive 검증 아님 |
| 무료·유료 | 공개 pages/PDF 접근, package open-source; 거래소 data license와 구별 |
| 인증 | 없음 |
| 호출 제한·bulk | 소수 annual PDF/HTML; 수치제한 미확인, package 오프라인 |
| 원·수정 의미 | 가격 없음 |
| 기업행동 | 없음 |
| 폐지·코드 이력 | 없음 |
| 공개 시각 | 공지 게시/수정 시각과 package rule 생성시각 미확인. UTC session 시간은 규칙으로 계산한 값 |
| 수정·정정 | 원래 공지버전의 역사 archive 없음; 비정기 휴장·package 버전 변경 필요 |
| 저장·분석·학습·재배포 | package [Apache-2.0 LICENSE](https://github.com/gerrymanoim/exchange_calendars/blob/master/LICENSE); 거래소 calendar 문서 자체 재배포/ML 허가는 미확인 |
| 실제 검증 상태 | 공식파일 A, package DST/조기폐장/2025-01-09 closure 테스트 A; 전체15년·당시 공지vintage U |
| 적합 용도 | sample missing-date 진단; full history는 공식 exception 수집 후 사용 |
| 미충족 | 각 거래소 15년의 정규세션·당시 휴장공지 archive 및 거래정지 상태 |

최초 NYSE2024 URL(불필요한 /nyse 경로)은 실제404였다. 공식 문서로 정확한 경로를
찾아 재수집했으며 실패 기록을 지우지 않았다. XNYS reference를 Nasdaq 종목에도
초기 미국 공통 calendar reference로 비교했지만 두 거래소의 전체 운영규칙 동일성을
보장하지 않는다.

## Stooq (접근 제한 후보)

| 조사 항목 | 확인 내용 |
|---|---|
| 공식 문서 URL | [provider historical download page](https://stooq.com/db/h/), 실제 endpoint https://stooq.com/q/d/l/ |
| 확인 일자 | 2026-10-02 |
| 종류·필드 | 기대 CSV Date/Open/High/Low/Close/Volume은 adapter 가정이며 실제 schema 미검증 |
| 과거 범위 | 공식 페이지 본문 확인 실패; 제3자 주장30년을 지원된다고 쓰지 않음 |
| 무료·유료·인증 | 공개 HTTP 접근은200이나 JS 확인페이지. 실제 자료권한·유료조건 미확인 |
| 제한·bulk | 다운로드 page 조사했으나 접근 challenge. 한 번/순차 조회, 우회 안 함 |
| 원·수정 의미 | 공식 정의 미확인. adapter는 adjustment_definition_unverified로 표시 |
| 기업행동·폐지·코드 | 미확인 |
| 공개 시각·수정 | 미확인 |
| 저장·분석·학습·재배포 | 원문 약관 미확인; 허용 추정하지 않음 |
| 실제 검증 상태 | L. HTTP200 challenge를 빈 시장데이터/성공으로 취급하지 않음 |
| 적합 용도·미충족 | 이번 독립 비교 불가. 향후 접근·정의·권한 확인 전 사용 제외 |

## 후속 데이터 후보 (이번 API 수집 범위 밖)

| 후보·공식 링크 | 필드·범위·인증·제한·bulk | 시간·수정/PIT | 권한·상태·미충족 |
|---|---|---|---|
| [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [Fair Access](https://www.sec.gov/about/developer-resources) | CIK/submissions/accession/form/filing metadata, companyfacts/concept의units/period/val/accn/filed; XBRL2009 이후 문서; 무키, <=10req/s, nightlyZIP | accession 원문/amendment·acceptance 확인이 필요. latest frames와 companyfacts의 filed 날짜만으로 정확 최초 공개시각 복원 불가 | API D, 로컬 ATVI8-K403=L, 웹열람으로 본문 확인은 가능. SEC 문서의 제3자 저작권/텍스트 ML·재배포 권한 별도 확인 |
| [FRED real-time](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html), [vintage dates](https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html), [legal](https://fred.stlouisfed.org/legal/) | series별 observation/vintage/realtime_start/end, key; 최초값/수정은 series마다 범위 다름. 이번 numerical quota/bulk 미검증 | ALFRED vintage 날짜를 제공하지만 시각 수준 최초 발표/actual receipt와 다름; release schedule/agency 원문 조합 필요 | D/키접근 미검증. 원저작자별 제한 있으므로 모두 public domain으로 추정 금지 |
| [GDELT data](https://gdeltproject.org/data.html) | events/GKG/URL/처리시각 및 bulk 문서; 과거 버전별 범위 별도, 이번 raw/API 미수집 | GDELT 처리시각은 원기사 최초게시/수정시각이 아님; 기사 원문 자체의 보관/수정 이력 별도 | D, 원문권한/정정/삭제archive U/미검증. 사실 추출 학습 원문 출처로 즉시 승인하지 않음 |
| 기업 IR 원문·SEC 원문 | NVIDIA/Meta/MSFT/Activision의 개별 문서 actual capture; 개별 사례 수준 | 현재 원문은 최초버전 보장 아님; 날짜를 정밀 timestamp로 만들지 않음 | 일부 A; 뉴스 전체 corpus/라이선스/수정버전 미충족 |

컨센서스·공급망·옵션·호가·틱·공매도·ETF 자금흐름은 선택 요구 필드만 정의했고
이번 entitlement·history·사용권 실검증을 하지 않았다. 존재하거나 무료로 접근할 수
있다고 단정하지 않는다. 당시 업종은 Sharadar/SEC의 현재 값으로 대체할 수 없고
known_from/to가 있는 별도 이력이 필요하다.

## 역사적 PIT와 정보 마감의 별도 판단

가격 수정분·기업행동·당시 종목 master의 최초 알려진 버전/수신 archive가 확보되지
않았다. 모든 후보의 current historical endpoint는 그 사실만으로 역사적 PIT를
만족하지 않는다. SEC accession·ALFRED vintage는 복원의 재료지만 정밀 공개 시각,
누락/정정 연결·수신 지연의 처리까지 별도 검증해야 한다.

17:30 배포 상품의 거래일 t OHLCV를 t의16:00+60분 마감 feature로 쓰면 안 된다.
조기폐장일은 더 이른 cutoff다. delayed/EOD 상품의 문구만으로 마감60분 이내 제공을
가정하지 않는다. 장기 시스템에서는 **실제 수신 시각을 앞으로 계속 기록**하고,
과거 archive를 계약상 확보하거나 역사 검증의 제한을 분명히 해야 한다.
