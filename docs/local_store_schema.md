# 로컬 저장 스키마 — DB version 2

SQLite는 메타데이터와 작은 가격 표본의 기준 저장소다. 원본은 기존 파일 경로에 유지한다.
표준 라이브러리 sqlite3 외에 DB 서비스·Parquet·DuckDB 의존성을 추가하지 않았다.
단일 수집/적재 프로세스 계약이며 병렬 writer·분산 처리·15년 전량 성능을 검증하지 않았다.

`src/market_research/pit/migrations.py`의 migration 1은 엔터티, 2는 버전 연결과 검색 인덱스다.
`db-init`은 기존 migration checksum을 확인하고 남은 migration을 순서대로 적용한다.
알 수 없는 버전/변경된 checksum은 거부한다. FK를 **모든 연결에서** 켜고 busy timeout 10초,
synchronous FULL을 사용한다. 초기화/각 manifest/각 calendar/snapshot은 독립 트랜잭션이다.
버전·수신·연결·snapshot 테이블에는 UPDATE/DELETE 거부 trigger가 있다.
ingest_runs만 진행 상태를 갱신한다. 부분 실패는 파일별 격리하고, 중단된 현재 파일은 rollback한다.
재실행은 이미 적재한 manifest 내용 해시를 확인하고 원본 bytes를 다시 검사한다.
공식 참조: [SQLite foreign keys](https://www.sqlite.org/foreignkeys.html),
[Python sqlite3 transaction control](https://docs.python.org/3/library/sqlite3.html#transaction-control).

| 테이블 | 키·연결·책임 |
|---|---|
| schema_migrations | version PK, SQL checksum, 적용 시각 |
| ingest_runs / ingest_issues | 실행 ID, 실제 입력 루트·domain, importer/code 버전; manifest별 격리 이유 |
| raw_receipts | **수신 사건 ID** PK, 요청 ID, 실제 received_at, HTTP/상태, raw path/hash/size, network 수행 증거 |
| manifest_links | manifest의 byte hash PK → receipt/run; 원래 manifest JSON과 처리/수신 구분 |
| normalized_artifacts | artifact ID, 원본 hash, 정규화 byte hash, normalizer tag+code SHA/schema/importer, domain |
| artifact_receipts | artifact/receipt/manifest 복합 PK; 파싱 완료·검증 완료·usable_at·근거 |
| security_records | 내부 record ID, source의 임시 label, 별도 external security ID, issuer ID, 주식 종류, 검증 수준 |
| symbol_assertions | artifact/security FK, 코드/거래소, 유효 [from,to), temporal/evidence, 철회 상태 |
| daily_price_versions | artifact/security FK, source/date/session/가격 의미/kind별 unique; OHLCV, 통화, volume 단위와 조정 기준 |
| corporate_action_versions | artifact/security FK, event_key별 unique; 선언/효력/배당락/기준/지급일, 금액 의미, 분할 비율 |
| version_edges | 가격 또는 행동의 later/earlier FK, 공개 버전 근거 hash와 순서; 뒤늦은 최초 버전도 append로 연결 |
| trading_session_versions | calendar version/exchange/date unique; UTC open/close, 원 시간대, reference/공식 근거 구분 |
| query_snapshots / snapshot_items | 조회 내용 hash와 고정 JSON, 실제 version/artifact/receipt FK, 정책·schema/code 버전 |

모든 시각 비교용 문자열은 UTC의 고정 정밀도 ISO8601이다. 원래 received/publication/revision
표현도 보존한다. 날짜는 YYYY-MM-DD이며 null과 시각이 다르다.

## 적재 계약

기존 `1.0.0` manifest/정규화와 additive 완료 필드를 지원한다. JSON의 NaN/Infinity,
누락/잘못된 필드, OHLC 구조·음수 거래량/가격, 중복, 원본/정규화 provenance 불일치를 격리한다.
원본 상대 경로는 입력 루트 밖으로 벗어날 수 없다. HTTP 실패는 가격으로 파싱하지 않는다.
인증 필드가 포함된 요청은 격리하고 manifest 본문을 DB에 넣지 않는다.

정규화 byte hash가 없는 초기 manifest는 **파일명 canonical JSON hash**와 provenance를
검증하고 현재 byte hash를 계산한다. 이것은 당시 manifest에 byte hash가 있었다는 의미가 아니다.
파일명 hash도 없거나 다르면 격리한다. 실제 원본과 정규화 파일 없이는 적재 성공을 주장하지 않는다.

network_download_performed=false인 재처리는 명시적인 origin_receipt_id를 우선 확인하고,
없는 초기 기록은 동일 request/raw/fetched_at의 **유일하게 확인 가능한** receipt에
연결하며 새 HTTP receipt를 만들지 않는다. 원본 receipt가 없거나 여러 원본을 구별할 수 없으면 격리한다.
network flag가 없는 초기 record는 legacy_unknown으로 유지하고 observed에서 제외한다.
동일 bytes 재수신은 한 raw 파일을 참조하지만 서로 다른 receipt ID로 유지한다.
동일 receipt ID를 다른 내용/시각/상태에 재사용하면 격리한다.

기존 recorded_at_utc는 **파싱 전 manifest 초기 작성 시각**이어서 완료 시각이 아니다.
새 collector는 가격 schema/구조 검사와 정규화 저장 후 processing_completed_at_utc와
processing_validation을 기록한다. 원래 완료 시각이 없으면 parsing_completed_at=null,
현재 importer의 실제 검증 완료를 usable_at으로 기록한다. 단순 DB insertion 시각과 별도 필드다.
정확히 기록된 과거 완료 시각이 있으면 DB에 나중에 적재해도 그 시각으로 observed를 재현한다.

## 의미와 식별자

Yahoo OHLC → split_adjusted, current adjusted_close → total_return_adjusted/**close_only**.
후자는 OHL/volume=null이며 총수익률이나 재구성 OHLCV가 아니다. 원본 provider 의미 문자열을
semantics_evidence에 남긴다. Alpha raw_as_traded → as_traded. 알 수 없는 값은 unknown이다.
volume_unit/volume_adjustment_basis는 별도이며 실제 자료는 미검증을 유지한다.
기업행동 amount_semantics도 보존하고 분할 조정된 배당액을 원래 현금 지급으로 환산하지 않는다.

내부 security_record_id는 hash로 생성한 로컬 키다. 영구 증권 ID가 아니다.
CIK는 issuer_id이며 external 증권 ID로 승격하지 않는다. verified identity는 명시적인
external ID와 근거 assertion을 요구한다. 현재 실제 표본은 전부 temporary다.
현재 Nasdaq 목록은 유효 시작일 없는 현재 symbol assertion이다. 과거 유효 매핑으로 쓰지 않는다.

거래일은 reference calendar artifact hash와 library version을 기록한다. UTC/DST/조기폐장/
예외 휴장은 기존 exchange_calendars rules로 처리한다. 자동 importer가 공식 PDF를 역사 공지
버전으로 변환하거나 인증하지는 않는다. 실제 official notice의 version evidence는 여전히 없다.

## 무결성과 확장

db-audit는 SQLite integrity/FK/migration/immutable trigger, 연결·domain, price/action value hash,
raw/normalized/manifest/calendar/공개 근거 파일의 해시를 검사한다.
snapshot replay도 고정 version 값의 hash와 선택된 파일 근거를 확인한다.
대량 데이터에 대한 인덱스/성능 튜닝은 다음 범위다. 현재 DB 용량과 행 수는 검증 보고서에서 확인한다.

후속 확장 명세만 존재한다: financial_fact_version(accession/context/accepted/amended),
macro_observation_version(series/vintage/release), document_version(posted/updated/hash/license),
relation_assertion_version(entity IDs/effective/known/evidence), source_entitlement(product/scope/license).
현재 사용하지 않는 이 테이블들과 모델·수익률·거래 엔진을 구현하지 않았다.
