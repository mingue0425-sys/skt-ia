# 시간·버전 조회 계약 — policy 2.1.0

`query(db, *, cutoff, policy, ...)`는 policy를 반드시 명시한다. 자동 fallback은 없다.
기본 identifier_requirement=verified, price_semantics=(as_traded,), symbol_mode=effective_mapping,
input_domain=market이다. 실제 임시 표본은 temporary_allowed와 provider_label을 **명시**해야 한다.
가격 의미 unknown은 요청한 경우에만 반환하고 검증된 의미로 표시하지 않는다.

| 시간/기간 | 의미 |
|---|---|
| trading_date / event_date / observation_end | 관측·사건 대상. 공개 시각이 아님 |
| effective_from/to | 사실의 유효 [시작,끝). 코드 매핑에는 날짜 단위 유효 기간 |
| published_at / publication_date | 실제 제공처 공개 claim. 원 표현·정밀도·시간대 분리 |
| revised_at | 정정 claim. 해당 정정 내용의 이용 가능 시각과 연결돼야 함 |
| received_at | 응답 실제 수신 사건 시각. 2026년 수신을 2024년으로 바꾸지 않음 |
| parsing_completed_at | 실제 원래 normalizer 완료; 초기 manifest에서 모르면 null |
| validation_completed_at / usable_at | importer 검사 완료와 policy가 사용할 실제 사용 가능 시각 및 근거 |
| ingested_at | SQLite insertion 시각. 공개·수신·사용 가능 시각을 대체하지 않음 |
| historical publication evidence | 원본 버전 hash와 공개/정정 시간·근거의 결합. 날짜/시각 정밀도 별도 |

null은 무한 과거가 아니다. 날짜에 00:00/장 마감/17:00을 붙이지 않는다.
source_timestamp_utc(예: Yahoo bar 시작)는 publication이나 전체 일별 관측 종료가 아니다.

## 세 정책

**observed**: network 수행이 명시된 성공 receipt 중 received_at<=cutoff이고 usable_at<=cutoff인
버전만 사용한다. 과거 완료 시각이 미기록이면 이번 import 완료 이후에만 사용 가능하다.
일별 가격은 future trading_date를 거부하고 observation period 종료도 검사한다.
기간 종료를 직접 증명하는 필드가 없으면 cutoff에 알려진 명시 calendar version의 정규장 경계를
참고한다. provider daily 세션 자체가 미검증인 한 `session_scope=vendor_daily_unverified`를 보존한다.
검증된 session이 필요한 호출은 session_requirement를 지정하여 이 자료를 제외한다.

**historical_verified**: 정확한 공개 timestamp와 **그 버전의 원본 hash에 결합된 근거**를
요구한다. revision이 있으면 max(publication,revision)으로 이용 가능성을 검사한다.
현재 수정된 숫자에 옛 공개 timestamp만 붙인 자료, 날짜-only, null은 제외한다.
그 당시 우리 receipt는 요구하지 않으며, 나중에 수신한 진짜 과거 archive는 근거 검증 후 조회 가능하다.
reference calendar의 역사 공지를 증명하지 못하면 해당 calendar에 의존한 가격도 제외한다.
현재 실제 가격은 공개 버전 근거가 없어 이 정책의 적격 표본이 0이다.

**research_assumed**: 규칙 파일을 별도로 지정해야 한다. 지원 규칙은 다음과 같다.

```json
{"id":"date_end_plus_delay","version":"1","timezone":"America/New_York","delay_hours":1}
```

publication_date의 **다음 local 날짜 시작**을 UTC로 바꾼 뒤 명시한 elapsed-hour 지연을 더한다.
원래 publication_timezone과 규칙 시간대가 다르면 거부한다. DST 경계는 zoneinfo로 처리한다.
원래 시간대가 없으면 null로 저장하고 이 규칙에서는 제외한다. observed는 공개 시간대가
미기록이어도 실제 receipt/usable 조건이 충족되면 조회할 수 있다.
원래 필드는 수정하지 않고 assumption과 계산된 available_at을 별도 반환한다.
timestamp가 있지만 내용 버전 근거가 부족한 경우도 명시 assumed 정책에서만 탐색 가능하다.
publication date/time 둘 다 없으면 이 규칙으로도 제외한다. trading_date로 대체하지 않는다.
allow_reference_calendar=true를 명시하면 탐색에서 현재 reference rules를 쓸 수 있으나
NOT_STRICT_PIT 한계가 유지된다. 기본으로는 cutoff에 알려진 calendar만 허용한다.
결과·snapshot/export 전체에 **NOT_STRICT_PIT**, 규칙/버전/시간대/지연과 내용 근거 부족을 남긴다.

## 근거 입력과 신뢰 경계

정규화 행의 publication_evidence={path,sha256}가 별도 근거 파일을 참조한다.
근거 JSON은 kind(synthetic_version_record/vendor_version_archive/verified_timestamped_version),
version_raw_sha256, published_at 또는 publication_date, revised_at, verified=true,
verification_method를 가져야 한다. importer가 파일 hash·내용 버전·시각 일치를 확인한다.
synthetic_version_record는 synthetic domain에서만 허용한다. 합성 원본은 시장 원본과 다른 루트다.
이 인터페이스는 근거 assertion의 진위를 자동 보증하지 않는다. 실제 archive와 검토 근거의
authenticity/라이선스 확보는 제공처 검증 작업이다. 이번 실제 자료에는 해당 근거가 없다.

## 순서와 충돌

같은 source/internal security/date/session/가격 의미/kind에서 적격 버전을 모은다.
historical/assumed는 해당 버전의 available publication/revision 시각을 쓴다. observed는 모든
적격 후보에 버전 결합 근거가 있으면 source publication/revision 순서, 그렇지 않으면 실제
receipt 순서를 쓰고 그 근거를 결과에 남긴다. 늦게 받은 최초 버전이 근거 있는 정정을 되돌리지 않는다.
DB ingest/파일 이름/normalizer tag의 사전식 순서로 다른 값을 선택하지 않는다.
근거가 불완전한 경우의 receipt 순서는 운영 응답 순서이며 제공처 정정 chronology의 증명이 아니다.
동일 선택 시각에 상충값이면 unresolved_version_conflict로 **그 키를 제외**한다.
동일 값일 때만 hash로 결정론적 대표를 선택하고 equivalent IDs를 모두 남긴다.
현재 지원하지 않는 vendor revision sequence나 provider priority를 암묵적으로 사용하지 않는다.

normalizer fork의 상충값도 같은 규칙을 따른다. 알려진 철회 버전이 선택되면 그 키는 반환하지
않지만 옛 cutoff의 조회를 바꾸지 않는다. 이전 버전의 pointer와 append-only version_edges를 보존한다.
정정이 먼저 도착해도 나중에 발견한 최초 버전을 edge로 연결하며 cutoff에 적격한 lineage만 반환한다.
제공처별 시리즈는 별개로 반환한다. 같은 의미의 수치 불일치는 cross_provider_disagreement이며
평균/자동 덮어쓰기/임시 코드의 영구 ID 연결을 하지 않는다.

코드 매핑은 effective interval과 cutoff까지 알려진 assertion을 동시에 검사한다.
코드 재사용의 disjoint interval은 허용하되 서로 다른 증권/거래소의 겹침은 conflict로 제외한다.
provider_label 모드는 요청 코드 기반 표본 조회이며 역사적 매핑이나 영구 ID 연결이 아니다.
최신 source 실패와 이전 성공 receipt를 모두 유지한다. 조회에 latest_access_failures와 cutoff 기준
receipt age/stale flag를 반환하며, 후대 수신을 쓰는 historical 정책에서 음수 age를 만들지 않는다.

## 결과와 snapshot

결과에는 request/policy/version, 선택 version/receipt/raw hash/정규화 hash, normalizer/schema,
시간/식별자/가격 의미 상태, 제외 개수와 ID, unresolved conflicts, assumptions/limitations가 있다.
status는 OK/EMPTY/CONFLICT이다. EMPTY는 정상 읽기 결과이고 데이터 준비 PASS가 아니다.

snapshot은 결과의 canonical JSON과 내용 SHA-256을 고정하고 created_at은 별도 저장한다.
정렬은 source/security_record_id/date/meaning/version_id이다. 코드/정책/normalizer/calendar hash도
포함한다. 고정 replay는 **query를 호출하지 않는다**. 새 동일-cutoff query는 새 자료·진단·코드로
달라질 수 있다. 고정 snapshot의 원래 version과 hash 재현은 이와 별도다.
현재 파일 경로는 로컬 절대 경로를 참조하므로 원본 이동 전 경로 보존/이관 전략이 필요하다.
일부 1단계 normalizer의 SHA만 있고 당시 소스 archive는 없다. 저장된 정규화 snapshot 재현과
모든 옛 normalizer 자체 재실행 가능성은 구별한다.

CLI 정상 읽기/EMPTY/snapshot 저장·무결성 검증은 0, 명시 requirement 실패·오류는 1,
db-import의 일부 격리는 JSON PARTIAL/2다. source 실패 receipt만 있는 import PASS는
다운로드 PASS가 아니다. collect의 부분 실패 2도 성공으로 바꾸지 않았다.
--require-data와 --require-unambiguous를 조회/snapshot에 사용한다. argparse의 잘못된 문법은
표준 usage error/2로 처리한다. JSON의 status와 명령 종류를 함께 해석한다.
