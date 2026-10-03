# Observed 운영 계약 5.5.0

이 계약은 앞으로의 실제 수신을 연결한다. 기존 `strict`/`historical_verified`의 의미와 `learning_ready` 값은 변경하지 않는다. 새 scope로 기존 부적격 자료를 승격하지 않는다. 운영 명령은 수집·특징·정답 축적만 수행하며 모델 로딩·학습·주문·스케줄러를 실행하지 않는다.

## 범위와 시간

| scope | 사용 가능 시각 | 필요한 범위 | 주장 제한 |
|---|---|---|---|
| strict_history | 원본 버전에 결합된 당시 공개/정정 근거 | 해당 입력·정답의 기존 strict 조건; 시장 전체 연구에는 별도로 당시 종목군·폐지군·식별/행동/달력 이력 필요 | 행 단위 strict 통과 자체가 시장 대표성을 증명하지 않음 |
| fixed_security_research | 명시적 observed/historical/research_assumed 정책 | 고정 source/symbol, 검토된 가격/식별/세션/필드, 해당 기간 행동·상태·학습 권한 | 시장 대표성·생존편향 없는 선택·당시 실제 수신을 주장하지 않음 |
| forward_observed | 실제 HTTP receipt와 usable 시각, 실제 특징 계산 완료 | 동일 단일 증권 계약, observed 특징·정답, 실제 완료가 결정 시각 이전, 별도 권한 | 지금 수신한 과거 가격으로 당시 observed 표본을 소급 생성하지 않음 |

`scope_contract={version:"5.5.0",scope,source,symbol,market_representative:false}`를 새 특징에 명시한다. 특징과 정답의 계약이 일치해야 한다. 전 시장 폐지군은 두 제한 scope의 행 조건이 아니다. 해당 증권의 폐지/정지·식별·기업행동을 생략할 수 있다는 뜻은 아니다.

현재 수신한 과거 가격의 trading_date는 lookback 대상 날짜다. available_at은 실제 receipt/usable 이후다. `query(policy="observed",cutoff=...)`는 이 둘을 구분하므로 현재 lookback에는 과거 공개 timestamp가 필수 조건이 아니다. cutoff 이후 수신·파싱 자료는 제외한다. 새 API 없이 기존 query/feature/label/snapshot 엔진을 재사용한다.

## 결정 일정과 한 번의 실행

기본 설정은 `configs/observed_ibm_demo.json`이다. XNYS, America/New_York, `exchange_calendars=4.13.2`, 2026–2027 reference schedule을 고정한다. 최초 생성한 달력 JSON을 보존하고 내용·library·domain으로 calendar_version을 기록한다. 이후 config의 calendar_path/calendar_version으로 별도 기존 달력을 고정할 수 있다. Reference calendar는 과거 공식 공지 archive나 개별 종목 거래 상태의 대체물이 아니다.

결정 일정은 실제 거래일의 regular close+60분, 진입은 다음 거래 세션 시가다. 실행 날짜는 실제 뉴욕 현지 날짜이며 진단용 decision_date를 지정할 수 있어도 이미 놓친 결정은 `MISSED_DECISION`이다. 휴장일은 `NON_TRADING_DAY`, 장 마감 이전은 `SESSION_NOT_CLOSED`다. 미래 날짜/cutoff를 실제 운영 기록으로 만들지 않는다. cutoff는 명시된 실제 UTC 시각 또는 importer/달력 처리 후 실제 clock이다. 특징 계산 완료가 결정 시각을 넘으면 표본을 만들지 않는다.

마감 이후 결정 이전에 준비된 표본은 `SAMPLE_PREPARED`다. 기록에는 실제 feature_ready_at과 예정 decision_at이 별도로 남는다. 이는 투자 예측이나 주문이 아니다. 주말에도 마지막 완료 세션까지 현재 lookback 진단은 만들 수 있지만 거래 표본·정답은 만들지 않는다. 주말 실행을 금요일 당시 입력이나 월요일 미래 수신으로 표시하지 않는다.

한 실행은 다음 순서다.

1. 단일 source/symbol·권한·필드·고정 최소치와 HTTP 설정 검증.
2. 기존 collector로 성공 캐시 또는 새 bounded HTTP 요청. raw·normalized·receipt 보존.
3. 검토된 가격/식별 assertion이 있으면 별도 normalization version 생성. 원본 수정 없음.
4. PIT import, 고정 달력, 실제 cutoff의 observed snapshot과 현재 lookback 산술 진단.
5. 유효 거래 일정이면 실제 특징 완료를 기록하고 1/5/20-session 정답을 pending으로 등록.
6. 기존 표본 중 종료 시각이 도래한 horizon만 다시 계산. 특징 ID·미래 pending 버전·기존 dataset snapshot은 유지하고 새 성숙 정답/dataset 버전을 저장.
7. 현재 각 표본의 최신 snapshot을 `training_selection(scope="forward_observed")`로 검사. `prediction_not_available`, 모델/주문/스케줄 0으로 보고.

가격수익률과 cash_total_return은 Stage3 계산을 그대로 쓴다. 원 시가·수량 변화·action coverage·개별 상태를 검증하며 조정 종가/다른 target으로 대체하지 않는다. 실제 계산 완료 시각을 새 label_materialized_at/label_available_at에 반영한다. 과거 cutoff에서 새 버전은 선택되지 않는다.

## 권한·필드와 검토 입력

공식 IBM compact demo 예제는 한 번의 공개 접근·수집 연결 진단 범위다. 일반 계정·다른 종목·장기 무인 수집·학습 권한을 부여하지 않는다. 일반 운영은 `auth_mode=environment`, ALPHAVANTAGE_API_KEY, hash-bound collection_rights가 있어야 네트워크 수집을 허용한다.

Evidence reference 형식은 `{path:"절대 또는 실행 기준 로컬 JSON 경로",sha256:"파일 내용 SHA-256"}`다. assertion의 공통 필드는 kind, input_domain=market, verified=true, verification_method, source, symbol, received_at, usable_at이다. `verified`는 담당자가 실제 근거를 검토한 뒤 설정한다. hash와 파일 형식은 진위나 법적 허가를 자동 증명하지 않는다. 예제 값으로 권한을 만드는 기능은 없다.

| config 참조 / kind | 추가 필수 자료·필드 | 최소 검증 |
|---|---|---|
| collection_rights / data_usage | automated_collection_allowed=true, local_storage_allowed=true; 검토한 계정/목적·보관·이용 문서 | source/symbol·실제 수신/검토 완료<=실행 시각; 회사 사용이면 해당 서면 허용 근거 |
| price_contract_evidence / price_contract_review | start_date/end_date, version_raw_sha256s, price_semantics=as_traded, USD, session_scope=verified_regular_session, volume_unit=shares, volume_adjustment_basis=as_traded, security_identity={verification:verified,external_security_id,evidence} | 실제 downloaded raw hash·정확한 증권/기간·필드 정의 확인. issuer CIK만으로 증권 ID 대체 불가 |
| learning_rights / learning_rights | model_training_allowed=true와 실제 보관/ML 허용 검토 근거 | source/symbol과 policy별 권한 availability<=training_asof; demo 접근 성공으로 대체 불가 |
| trading_state / security_trading_state | listed_from, verified_through, halted_dates, delisted_date | entry/exit를 포함하는 개별 증권 상태; 거래소 달력만으로 대체 불가 |
| action_coverage / corporate_action_coverage | start_date/end_date,status=verified_complete,same_day_order와 실제 완전성 검토 | 해당 가격 구간의 split/distribution/cash 사실. 빈 events는 complete coverage 증거 없이 승인 불가 |

Corporate action 사건은 기존 importer 정규화 actions 배열(date/type/원현금·비율/증권/실제 시각/evidence)로 보존한다. 가격과 동일 source의 검증된 연결이 필요하다. 개별 IR 공지 하나를 구간 전체 무사건 증명으로 쓰지 않는다. 원 OHLCV는 날짜/O/H/L/C/V/USD 필드를 검증한다. 누락 open을 median으로 대치하지 않는다. 검토 overlay는 승인된 raw hash만 처리하므로 새 응답 hash가 승인 목록 밖이면 명시적으로 중단한다. 새 assertion 파일로 추가 검토를 기록하고 원 파일을 덮어쓰지 않는다.

## 학습 연결

`training_selection(...,scope="forward_observed" 또는 "fixed_security_research")`는 다중 제외 사유를 반환한다. 기본 selector는 기존 strict 조건 그대로다. research 진단용 allow_research와 새 scope를 동시에 요청하면 거부한다. 신규 scoped dataset은 Stage5 run config에 같은 data_scope와 mode=research를 명시해야 한다. strict로 자동 승격하지 않는다. 모델 특징 목록은 저장된 required_features와 같아야 한다.

최소 활성화 기준은 **100개 성숙 표본, 180일 이상의 날짜 범위, purge 후 train>=60/validation>=20/test>=20**이다. 작은 데모에 맞춰 낮출 수 없다. 이는 통계적 충분성 보장이 아닌 사전 운영 gate다. 클래스·특징·권한·target·분할 검사는 별도로 필요하다. 실제 분류는 양쪽 클래스가 필요하다. 운영 command는 gate를 충족해도 자동 학습하지 않으며, 승인된 frozen 입력과 사전 Stage5 설정을 별도로 실행한다. 합성 artifact를 실제 모델로 로딩하는 인터페이스는 없다.

## 실패·멱등성·복구

Linux flock으로 한 root에 단일 writer를 강제한다. run_id는 설정+뉴욕 결정 날짜+요청 cutoff hash다. 완료된 동일 실행은 저장 result hash와 dataset/query 원본 hash를 확인한 뒤 replay한다. --refresh도 완료된 동일 실행을 다시 다운로드하지 않는다. 새 cutoff는 별도 실행이다. after_processing의 같은 설정·같은 결정 날짜 재실행도 완료된 첫 실행을 재생하므로 그날 새 수신/성숙 평가가 필요하면 실제 새 explicit cutoff를 지정한다.

collection/raw·normalized·manifest, pit.sqlite, datasets.sqlite, calendar.json, samples/*.json을 유지한다. executions/*.json은 단계별 checkpoint와 실패 시각을 기록한다. 성공 수집 뒤 import/후속 오류는 해당 collection checkpoint부터 재개한다. HTTP/파싱 실패는 실패 manifest를 보존하고 다음 실행에 수집을 재시도한다. journal을 삭제하여 완료된 missed 결정을 소급 복구하지 않는다. 새로운 유효 일정/cutoff를 사용한다.

종료코드: 0=성공적인 실행/정상 대기/휴장/replay, 2=수신·파싱 실패, 3=missed/입력 부재/필수 필드·신선도·특징 미충족, 1=설정·권한·무결성·DB 오류. status와 training_readiness를 따로 읽는다. 0이 학습 준비를 뜻하지 않는다. timeout<=20초, 최대3시도, Alpha12초 간격, 기존25/UTC일 local budget과 Retry-After 규칙을 사용한다. 다른 root/프로세스/계정의 호출은 이 budget으로 집계할 수 없으므로 운영 계정 한 writer와 중앙 호출 관리가 필요하다. cron/systemd는 활성화하지 않는다.
