# 2단계 인계

> 저장소 공개본: 실제 수집은 원래 로컬 작업 공간에서 수행했다. `data/`와 행 단위
> `sample_quality.json`·`sample_cases.json`·`calendar_reference_review.json`은 이용 조건 미확인으로
> 커밋하지 않았다. 위치 표는 로컬 산출물의 위치이며 공개본에는 집계 품질 요약만 있다.
> 공식 문서 사례와 달력 대조의 상세 수동 검토 기록은 원래 로컬 작업 공간에 보존한다.


확인일: 2026-10-02. 엄격한 역사적 PIT 검증은 **BLOCKED**, 제한된 데이터 처리
프로토타입은 **CONDITIONAL**이다. 이번에 전체 DB·모델·Fusion·거래 시스템을 구현하지 않았다.

## 검증한 출처와 데이터 의미

Yahoo chart에서 6개 보통주와 SPY의 2024년 OHLCV·adjclose·events, META 코드 변경
구간과 IBM 2026년 구간을 확보했다. OHLC는 분할 조정된 형태로 취급하며 원가격이
아니다. adjclose는 현재 분할·배당 조정 버전이다. 요청 코드가 당시 거래 코드라는
보장이 없고 chart endpoint의 공식 지원 계약·이용 권한은 미확인이다.

Alpha TIME_SERIES_DAILY의 공식 IBM demo에서 문서상 as-traded 원가격 OHLCV를
1999-11-01부터 2026-10-01까지 6,770행 확보했다. compact 100행도 확보했다.
날짜별 숫자 문자열 스키마를 실제 관측했다. IBM demo 성공은 다른 종목의 full 무료
접근을 입증하지 않는다. 전체 응답은 보존했지만 품질 검사는 2024년과 2026년 비교
구간 358행으로 제한했다.

Nasdaq directory의 현재 목록 13,294행을 확보했다. ETF/Test Issue 등의 원본 필드는
보존했지만 보통주 여부, 상장·폐지 날짜, 과거 코드, 영구 증권 ID는 검증하지 못했다.
NYSE 2024·2026 및 Nasdaq 2026 공식 달력 원본을 받았다. exchange_calendars의
XNYS 일정은 규칙으로 생성한 참고 자료이며 당시 공지를 수신한 기록이 아니다.

NVDA 분할, MSFT 배당, FB→META 코드 변경, ATVI 합병 사건은 공식 문서와 대조했다.
ATVI 가격과 로컬 SEC 8-K 다운로드는 실패했다. 폐지 가격·최종 거래일·합병 대가 수신의
연결은 미검증이다. 정확한 사례와 근거 해시는 sample_cases.json에 있다.

## 원본과 실제 스키마 위치

| 위치 | 내용 |
|---|---|
| data/raw/{source}/{request_id}/{sha256}.bin | 제공처 응답 그대로. 실패 본문도 보존하며 비밀키를 되비춘 응답은 보관 보류 |
| data/manifest/*.json | 안전한 요청 필드, HTTP 상태, 수신·처리 시각, 해시, 크기, 행·문서 수, 실제 가격 기간, 코드·설정·스키마 버전 |
| data/normalized/{source}/{request_id}/{hash}.json | bars/actions/directory/documents 구분, 가격 의미와 임시 ID 명시 |
| data/derived/alpha_ibm_sample.json | IBM 전체 응답에서 선택한 2024년 및 2026년 비교 표본 358행 |
| data/derived/calendar.json | 2022–2026 XNYS 규칙 기반 UTC 일정, 1,254거래일 |
| data/versions/{code_sha256}.json | 최종 소스·설정·의존성 버전과 파일 내용. 개발 중 이전 해시는 참조만 남았으며 소스 스냅샷을 보관했다고 주장하지 않음 |
| docs/normalization_schema.md | 실제 제공처 필드, 자료형, null과 날짜·시각의 의미 |
| reports/sample_quality.json | 자료별 정확한 경로·해시·기간·행 수와 제공처 간 일별 차이 |
| reports/sample_cases.json | 실제 사건·가격과 수동 원문 검토 근거 및 공백 |
| reports/calendar_reference_review.json | 공식 달력에서 확인한 25개 휴장·조기폐장 항목 대조 |
| reports/unit_test_results.json, artifact_audit.json, execution-*.json | 명령·실행 환경·오프라인 테스트·실제 네트워크·캐시 근거 |

조회할 때 제공처별 동일 요청의 성공 버전을 명시한다. 최신 refresh가 실패하면
이전 성공 자료와 최신 접근 실패를 함께 기록한다. 서로 겹치는 요청의 상충값을
임의로 정답으로 선택하지 않는다.

## 관측한 시간과 가정한 시간

| 필드 | 의미 |
|---|---|
| trading_date | 제공처 날짜 또는 bar timestamp를 제공처 시간대로 변환한 거래일 |
| source_timestamp_utc | 실제 Yahoo bar timestamp. 최초 공개·이용 가능 시각이 아님 |
| fetched_at_utc | 이번 HTTP 수신 UTC 시각. 과거 당시 수신으로 사용하지 않음 |
| recorded_at_utc | 로컬 처리·manifest 작성 시각. 코드 변경 후 재처리와 실제 수신을 구별 |
| publication/revision_time_utc | 확인하지 못했으므로 null. 날짜에 임의의 17:00 등을 붙이지 않음 |
| event_date | Yahoo 분할은 분할 기준 거래 개시일, 배당은 배당락일. 선언·법적 효력·지급일과 구별 |
| calendar open/close UTC | 라이브러리 규칙과 시간대로 계산한 참고 일정. 실제 공지 시각은 없음 |
| cutoff | 연구 가정: 실제 정규장 마감+60분. DST·조기폐장을 반영하되 자료 수신 가능성의 증명은 아님 |

Microsoft 배당 공시의 페이지 날짜 11/29와 본문의 선언일 11/28을 구분한다.
어느 날짜도 정확한 최초 공개 시각으로 승격하지 않는다. Tiingo·Sharadar의 17:30
배포 설명은 17:00 마감과 맞지 않을 수 있다. 과거 자료가 그 당시 cutoff에 알려졌다고
주장하려면 최초 공개 버전이나 실제 당시 수신 기록이 필요하다.

## 식별자와 기업행동의 미해결 문제

`temporary:source:ticker`는 검증된 영구 증권 ID가 아니다. CIK도 발행사 ID여서
주식 종류를 식별하지 못한다. META 조회에서 변경 전 FB 기간 12행을 현재 코드로
반환한 사례는 유효기간이 있는 증권 ID 매핑의 필요성을 보여준다. 코드 재사용,
거래소 변경, 합병 후 대체 증권·현금 대가, 보통주 필터를 추가 검증해야 한다.

NVDA 분할 전후 독립 원가격 표본이 없고 배당액의 분할 기준과 정정 이력도 불완전하다.
가격 조정계수를 거래량에 같은 방향으로 적용하면 안 된다. Sharadar의 closeunadj를
사용한 OHLV 환산값은 직접 관측한 원가격과 구별해야 한다. ATVI 합병일과 거래정지
요청일은 검증된 마지막 거래일·법적 폐지일을 대체하지 못한다. IBM 거래량 불일치의
원천·세션·정정 문제는 cross_provider_review.md를 이어서 조사한다.

## 전량 수집 전 해결할 조건

1. 저장기간, 개인·기관 분석, ML 학습, 모델·원자료 배포, 계약 종료 시 삭제 조건이 명시된 권한을 확인한다. 구매·새 계정·계약 동의는 상품과 조건을 구체화한 뒤 사용자의 별도 승인 범위에서 진행한다.
2. 폐지 주식까지 포함한 10–15년 증권 목록, 영구 ID, 코드 변경 이력과 당시 보통주 분류를 실자료로 검증한다. 현재 생존 목록을 과거 투자 대상 목록으로 대체하지 않는다.
3. 종목별 원가격 OHLCV, 거래량 단위·세션, 분할·배당·합병·대가 정의를 확보하고 독립 후보의 사건 경계 표본과 비교한다.
4. 최초 공개·정정 버전의 available_at과 실제 received_at을 확보한다. 엄격한 역사적 PIT가 불가능한 필드는 연구에서 제외하거나 제한을 명시한다.
5. 실제 장 마감+60분 내 제공 여부를 측정한다. 17:30 배포 상품은 더 이른 출처를 확보하거나 후속 연구 계약에서 시점 가정을 명시적으로 수정해야 한다.
6. 거래소별 15년 일정의 예외 휴장·조기폐장, 거래정지와 상장 경계를 검증한다. 추가 작은 표본이 통과한 후에 수집 규모를 늘린다.

## PIT 테이블 초안 — 이번에 구현하지 않음

| 테이블 | 키와 필드 초안 |
|---|---|
| source_entitlement | source/product/scope, license_version, 저장·학습·배포 조건과 유효기간 |
| raw_receipt | receipt_id/request_id/source, received_at, HTTP 상태, 원본 hash/path, 코드·설정·스키마 버전 |
| security / issuer | 검증된 security_id, share_class, issuer_id/CIK, 거래소·증권 종류, 매핑 출처 |
| symbol_history | security_id/symbol/exchange, effective_from/to, known_from/to, 공표·수신 근거 |
| listing_history | security_id, 상태·사건·이유, 최초·최종 거래일, 유효·알려진 기간, 거래정지 구간 |
| trading_session | exchange/date, open/close UTC, 공지 버전, known_from/to |
| daily_price_version | security_id/date/session/source, 원가격 OHLCV, 통화·가격·거래량 정의, vintage, 공개·수신 시각과 알려진 기간 |
| corporate_action_version | security_id/action_id/type, 선언·효력·배당락·기준·지급일, 현금·통화·분할 비율·대체 증권, 버전·수신 근거 |
| adjustment_version | security_id/date, factor/kind/basis/vintage, 연결 action IDs와 알려진 기간. 원가격과 분리 |
| financial_fact_version | CIK/accession/form/concept/context/unit/period/value, accepted_at, amended_by, 수신과 알려진 기간 |
| macro_observation_version | series/관측 기간/vintage/value/unit, release_at/received_at, 알려진 기간 |
| classification_version | security_id, 분류 체계·버전·코드, 유효·알려진 기간 |
| document_version | source/document/entity, 원문 hash/path/license, posted/updated/received 시각과 정밀도, 이전 버전 |
| relation_assertion_version | entity IDs/direction/type, 근거 문서, 주장·효력·알려진 기간 |
| universe_snapshot | decision_date, 출처 증권 IDs, 당시 자격·보통주 분류, 당시 알려진 원자료로 계산한 후행 유동성, 규칙 버전 |

조회 규칙 초안: `C=실제 정규장 마감+60분`에 대해 검증된 available_at<=C인 버전만
선택하고, 실제 당시 수신 기록이 있으면 received_at<=C도 요구한다. 현재 2026년
수신을 과거 2015년의 수신으로 간주하지 않는다. 공개 시각이 날짜뿐이면 정확한 시각을
알 수 없으므로 거부하거나 별도 계약한 보수 규칙을 사용한다. null은 무한 과거가 아니다.

증권별 유효기간과 알려진 기간을 함께 조회한다. 미래 정정으로 과거 행을 덮어쓰지 않고
미래 분할·배당을 반영한 현재 수정주가를 당시 특성으로 소급 사용하지 않는다. 예측에
사용한 정보와 사후 평가용 현금흐름은 서로 다른 조회 기준이 필요하다. 다음 시가와
1·5·20거래일은 증권·거래소 일정에 연결하되 이번에는 거래 규칙과 수익 라벨을 구현하지 않았다.

## 우선순위순 다음 작업

1. 후보 상품의 권한·약관·17:00 이용 가능성·과거 버전 지원을 실자료로 확인하고 결정 기록을 작성한다.
2. 영구 증권 ID, 폐지 종목, 코드 재사용·변경, 당시 보통주 분류의 추가 표본을 검증한다.
3. 분할 원가격·수정가격, 배당 현금액 기준, 합병 대가, 거래량·세션 차이와 달력 예외를 해결한다.
4. SEC 원문 accession·acceptance·정정과 ALFRED vintage·발표 시각을 후속 표본으로 확인한다. 당시 업종과 뉴스 원문 이용권을 조사한다.
5. 조건 통과 후 작은 로컬 테이블로 PIT 조회를 검증하고 다음 단계의 확장 범위를 정한다.

## 재현 명령

```bash
cd skt-ia
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/verify.py
.venv/bin/python -m market_research.cli collect
.venv/bin/python -m market_research.cli collect --config configs/evidence_supplement.json --output reports/execution-dividend-evidence.json
.venv/bin/python -m market_research.cli smoke --refresh --output reports/execution-smoke.json
.venv/bin/python -m market_research.cli validate
.venv/bin/python scripts/audit_artifacts.py
```

전체 collect의 부분 실패 종료 2와 구조 검사 PASS·역사적 PIT BLOCKED를 구별한다.
오프라인 테스트는 원본 없이도 가능하지만 validate/audit에는 확보한 파일이 필요하다.
공식 PDF 수동 검토에서 pdftotext/pdftoppm을 사용했으나 수집·검증 CLI의 런타임
의존성은 아니다. 보관 원문과 사례의 해시 근거로 문서 검토를 재현할 수 있다.
