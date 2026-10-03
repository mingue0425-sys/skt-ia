# 실제 자료 실행 과제 — Stage5.5, 2026-10-03

현재 Linux ARM64에서 공개 IBM demo 요청과 수신→PIT→현재 lookback 경로를 실행했다. [진단 표](data_blocker_matrix.md), [검증](stage5_5_validation.md), [운영 계약](../docs/observed_operation_contract.md)이 현재 기준이다. Stage5의 합성 결과와 실제 학습 적격 0은 각각 다른 판정이다. 실제 예측력은 NOT_EVALUATED다.

## A. 제한된 역사 연구

고정 IBM의 현재 vintage 원 OHLCV는 가격 변화·MA·변동성 산술 연구의 후보다. 기존 full 원본에는 1999-11-01~2026-10-01이 있고 이번 compact에는 2026-05-12~10-02의 100행이 있다. 현재 과거값은 당시 공개 버전이나 당시 수신 이력을 증명하지 않는다. AAPL에는 2024년 현재 조정 OHLC와 adjusted close-only 파생 자료가 있으며 as-traded 시가 계약의 대체물이 아니다. 특히 AAPL 2024-12-31 결정의 모든 종료와 12-02 결정의 20일 종료는 2025년이라 현재 로컬 범위에 없다.

`fixed_security_research`는 고정 source/symbol과 비대표성 선언을 가진 별도 계약이다. 전체 폐지군을 확보하지 않아도 해당 증권의 제한 연구를 검토할 수 있다. 그러나 해당 증권의 식별·가격/세션·action coverage·상태·성숙 정답·학습 권한은 계속 필요하다. 현재 범위에서는 계산 가능성과 학습 허용을 구분하고, NOT_STRICT_PIT/현재 vintage/선택 편향을 표시한다. 당시 실제 수신·시장 전체 대표성·생존편향 없는 선택·실제 투자 수익성은 주장할 수 없다.

시장 전체 strict 역사 검증에는 당시 증권 master·코드/상장/폐지 이력·당시 투자 종목군·폐지 대가·가격/기업행동 archive의 버전별 공개 근거가 추가로 필요하다. 기존 strict를 느슨하게 하거나 연구 결과를 strict로 승격하지 않는다. 현재 archive 제공/권한은 확인되지 않았다.

## B. 앞으로의 observed 축적

현재 최소 접근 후보는 **Alpha Vantage의 공식 공개 IBM compact demo**다. 실제 새 HTTP 요청 한 번이 성공했다. 일반 계정/다른 종목/장기 축적/학습 권한까지 확인한 것은 아니다. TIME_SERIES_DAILY는 원 OHLCV로 설명되고 compact가 마지막100행을 제공한다. raw daily에는 기업행동이 포함되지 않는다. [공식 daily 명세](https://www.alphavantage.co/documentation/). 개인 분석/연구/testing과 회사 등 상업 사용의 별도 서면 합의 조건은 구분해야 한다. 저장 기간·ML/모델 사용 허용의 구체 범위는 책임자의 검토가 필요하다. [공식 약관](https://www.alphavantage.co/terms_of_service/).

한 번의 observed 실행은 구현됐다. 오늘은 뉴욕 토요일이므로 현재 cutoff의 금요일 lookback 산술만 생성하고 거래 표본·pending 정답은 생성하지 않았다. 다음 유효 실행은 실제 거래일 마감 이후, close+60분 결정 이전이다. 제공처의 해당 시각 응답 신선도는 아직 입증되지 않았다. 늦으면 MISSED_DECISION/STALE_DATA이며 기존 임계 시각을 소급 바꾸지 않는다. 일정 변경이 필요하면 사전 별도 계약으로 설계·검증한다.

처음 수신한 과거 100행은 미래 결정의 lookback으로만 사용한다. 앞으로의 actual receipt/processing 기록을 쌓고 신규 1/5/20-session 정답을 pending으로 등록한다. 종료 세션의 완성 일별 bar·action/state 자료가 수신되어야 새 ready 버전을 만든다. 실제 모델이 없으므로 prediction_not_available다. 샘플/수신 로그를 예측 기록이라고 부르지 않는다.

## 장애물별 다음 행동

| 장애물·영향 | 정확한 부족 자료/권한 | 해결 후보·근거 | 코드 작업 판정 | 사용자 최소 입력 / 구매·계약 여부 | 충족 검증 |
|---|---|---|---|---|---|
| 실제 자동 접근·보관·ML 권한 | 사용 주체/목적, 자동 수집·로컬 보관 조건, model_training_allowed의 검토 근거 | Alpha [약관](https://www.alphavantage.co/terms_of_service/), 기존 [조사](../docs/provider_comparison.md) | collection/data_usage와 learning_rights를 분리하여 검사 구현 | 개인/회사 목적 확정; 실제 키는 환경변수; 해당 목적 문서/서면 허용을 JSON assertion+원문 hash로 제공. 회사 사용 등은 별도 계약 필요 여부를 공급자와 확인. 자동 구매/계정 생성 없음 | source=alpha_vantage/symbol=IBM, verified/method/received/usable, collection+storage 허용과 ML 허용을 각각 검사; 미확정 fit 거부 |
| 가격·세션·증권 의미 | IBM의 verified security ID/share class 및 연결, 정규장 정의, volume 단위/조정 기준 | 기존 IBM raw·정규화 파일과 daily 명세; 공급자 증권 master/정확한 필드 사전 | hash-bound price_contract_review를 새 normalization에 적용 구현; 의미를 자동 승격하지 않음 | 원본 raw hash 목록, 증권 ID 증거, 해당 기간 필드 사전/거래소·통화 확인. 형식은 운영 계약 review 행 참조 | 원본 보존, 해당 raw vintage/기간/증권만 overlay; unsupported/CIK-only/기간 밖/미승인 hash 거부; 입력 snapshot/evidence 재생 |
| 기업행동 완전성 | entry~exit의 분할/현금배당/특수분배/합병 사건과 구간 완전성 | Alpha adjusted/dividends/splits는 [문서상 후보](https://www.alphavantage.co/documentation/), 기존 IR 공식 개별 공지; 실제 키 기능 미검증 | 기존 actions importer/holdings 계산 재사용 | 날짜/type/원현금·비율/증권/receipt/evidence 정규화 events와 complete interval assertion 제공. 사건 하나로 무사건 구간을 승인하지 않음. 상품권한/계약 필요 여부 확인 | 빈 사건 목록도 complete coverage 필요; split 전후 수량·cash를 독립 대조; total target 자동대체 없음 |
| 개별 거래/상장 상태 | IBM listed_from/verified_through/halt/delist 및 endpoint 상태 | 공급자 listing/state 자료; Alpha LISTING_STATUS는 문서 후보, 이번 접근 미실행 | 기존 security_trading_state 검사 연결 | 해당 기간의 증권 상태 JSON/원자료 근거; 전체 폐지군은 단일 증권 scope에 요구하지 않음 | entry/exit를 상태 구간이 포함; 정지/폐지 가격 자동 대체 거부 |
| 과거 observed 불일치 | 과거 decision 이전의 actual receipt/usable 없음 | 기존 receipt와 새 실제 수신 | 현재 lookback에는 과거 공개 근거를 요구하지 않는 경로 검증; 과거 소급 불가 | 당시 수신 archive가 없다면 과거 observed 주장 포기하고 명시 research 또는 앞으로 축적 | 과거 cutoff0/current lookback61; late receipt/processing 제외 |
| 과거 공개/version·전체 시장 universe | 당시 공개와 해당 원본 hash, 공지 달력/증권군·폐지군/코드이력/폐지 대가 | Massive raw 옵션 [명세](https://massive.com/docs/rest/stocks/aggregates/custom-bars), Sharadar [주식 문서](https://sharadar.com/docs/stocks), 기존 provider 조사. 당시 vintage archive 가능 여부는 미확인 | 입력/hash 검사는 가능; 역사나 권한 창설 불가 | 공급자에게 실제 archive 기능·형식·보관/학습 권한 확인 및 작은 증거 표본 요청; 새 가격/상품 기능을 추측하지 않음 | 공개 timestamp+version_raw_sha256과 정정 lineage; 당시 universe/delist 표본 대조 후 strict gate 재검사 |
| AAPL 원시가/2025 endpoint 부재 | as-traded OHLC, 2025년 entry/exit 자료, 해당 행동/상태 | 허용된 raw 공급자 계정/CSV 내보내기. Yahoo 현재 split-adjusted open은 대체 불가 | 필드/의미/endpoint별 진단 구현 | 원시가 계약에 맞는 날짜/O/H/L/C/V/USD·source/security/session/vintage/receipt 포함 파일. 현재 가격연구만 하면 별도 close target 계약 필요 | 구조적으로 없는 필드 대치 금지; target 날짜 존재·의미·해당 scope 이용권 검사 |
| 미래 정답·규모 부족 | 종료 가격/행동 수신, 충분한 날짜/성숙 정답/클래스 | observed 단일 명령 반복은 사용자가 설정한 후 운영 | pending/전달 지연/과거 누락 분리, 새 성숙 버전 생성·selector 연결 완료 | 먼저 수동 정상 세션 실행 및 이용/필드 증거를 확보. 즉시 fit할 작은 표본으로 최소치 낮추지 않음 |100성숙 표본/180일, purge 후60/20/20, 분류 양쪽 클래스, 고정 특징·target·권한 검사를 통과 |

## 실행·복구와 최소 사용자 행동

1. 첫 운영 범위를 IBM 한 증권/개인 또는 회사/보관·ML 목적으로 확정하고 해당 권한 근거를 제공한다. 공개 demo 진단을 일반 운영 권한으로 바꾸지 않는다.
2. 계정 키가 있다면 ALPHAVANTAGE_API_KEY로 주입하고, config의 auth_mode=environment·access_scope=account_operation과 collection_rights를 채운다. 키는 JSON·명령 인자·로그에 넣지 않는다.
3. 위 price_contract_review/trading_state/action_coverage/learning_rights를 원문 또는 공급자 내보내기로 제공한다. 담당자 검토가 없는 verified=true 예제를 실제 근거로 쓰지 않는다. 실제 미승인 raw hash는 review 단계에서 차단된다.
4. 뉴욕 실제 거래일 마감~결정 사이에 한 번 실행하고 freshness·receipt·feature_ready_at·pending을 확인한다. README 명령과 운영 계약의 실패/재실행 규칙을 사용한다. 스케줄은 아직 켜지 않는다.
5. 충분한 성숙 자료가 모인 뒤 최신 membership을 단일 frozen dataset으로 명시적으로 묶고 Stage5 research+forward_observed 설정에서 고정 작은 모델 비교를 실행한다. 자동 학습·실전 예측 활성화는 구현하지 않았다.

외부 자료의 검증을 맡을 담당자와 권한을 자동 지정하거나 외부에 연락하지 않았다. 실제 예측력·수익률·Sharpe는 생성하지 않았다. 실제 적격 자료와 단순 모델 비교 전에는 Stage6 신경망 개발로 진입하지 않는다.
