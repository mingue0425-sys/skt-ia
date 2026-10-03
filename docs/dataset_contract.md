# 데이터셋 계약 3.0.1

PIT metadata의 migration 1:2는 유지한다. 별도 DatasetStore SQLite migration 1이
samples, feature_versions, label_versions, dataset_snapshots, dataset_items를 관리한다.
외래키·고유 제약·transaction·immutable update/delete guard와 migration checksum을 사용한다.
PIT query snapshot 연결은 ID/hash로 저장하고 replay 시 원래 PIT DB와 raw/normalized 파일을
검증한다. 별도 DB 간 SQLite FK는 없으므로 이 연결 검사가 반드시 필요하다.

build_dataset(pit_db, dataset_store, config, batch_size=1)은 명시 series와 decision_date 목록을
받는다. 한 표본의 61-session feature window를 끝내 저장한 후 별도 label_asof query로
21-session 미래 범위를 읽는다. 전체 가격행/특징행을 메모리에 복제하지 않고 membership ID만
모은다. batch_size는 상한 검증용이며 현재 구현은 항상 한 표본씩 처리한다.
M2 8GB 성능을 보장하지 않으며 실기기 검증은 별도다.

표본에는 sample_id, 내부 증권 ID/수준, decision_at, feature_cutoff, 실제/가정 ready_at,
entry/각 horizon exit, feature/label/action snapshots, query/assumption policy, status/reason,
가격·정의·code·calendar 버전과 이용 조건을 보존한다. 출처 간 임시 ticker 연결은 금지한다.
research_calculable, historical_pit_eligible, execution_status, learning_ready는 별개다.
synthetic와 권한 미확정 자료는 learning_ready로 승격하지 않는다.
실제 운영 특징 준비 근거가 아닌 기본 가정 일정은 execution_status=conditional이며 자동
learning_ready가 아니다. 권한 근거의 이용 가능 시각도 training_asof 조건에서 확인한다.
잘못된 개별 일정은 invalid 표본/정답으로 보존하고 다른 표본을 계속 처리한다. 이 경우
query를 수행하지 않아 feature/label/action snapshot ID는 null이다.

freeze는 metadata+정렬된 membership(feature ID/label ID)의 canonical SHA-256이다.
정렬은 sample_id,horizon_sessions,feature_version_id,label_version_id이다. created_at은 별도여서
동일 입력/config/code/증거 버전 재생성 시 hash가 같다. 정정·새 normalizer·미래 자료는 기존
dataset을 변경하지 않는다. 새 build의 query 제외 진단이나 코드 버전이 바뀌면 새 hash일 수 있다.

replay_dataset는 고정 ID를 검증하며 query를 다시 실행하지 않는다. iter_dataset_rows는
한 membership씩 특징·정답을 내보내며 CLI JSONL export는 원자적 저장이다.
training_selection(..., training_asof=..., allow_research=False)는 frozen 버전 중 ready/이용 가능/
진입 전 준비/strict PIT/권한 조건을 충족하는 것만 반환한다. allow_research=True는 명시적
진단 선택이며 RESEARCH_DIAGNOSTIC_NOT_TRAINING으로 표시한다. 실제 학습은 없다.

coverage/state/rights 외부 assertion은 path+hash, kind/domain/verification_method/시간·기간을
확인한다. 파일 integrity는 제공처 근거의 진위·법적 권한을 자동 증명하지 않는다. 시장 domain에
합성 assertion을 사용할 수 없다. 원본/assertion 이동 시 경로 보존/이관이 필요하다.

CLI 정상 계산·빈 읽기·snapshot 무결성 성공은 0, 잘못된 계약/명시 require-data 실패는 1이다.
blocked/pending 표본은 삭제하지 않고 JSON status/reason으로 보존한다. dataset-build의 PASS는
저장 작업 완료이고 ready 학습 표본이 있다는 뜻이 아니다. 기존 collect의 부분 실패 2는 유지한다.

관련 문서: feature_contract.md / label_contract.md / time_query_policies.md.

3.0.1은 invalid 수치의 표본 전파, 날짜 미상 action의 보수적 차단, 과거 label_asof의 대기
판정을 보완한다. DB schema는 변경하지 않는다. 3.0.0의 고정 dataset은 원래 ID/값 그대로
replay하며 새 정의로 다시 계산하지 않는다.
