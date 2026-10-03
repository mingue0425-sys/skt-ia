# Stage6 인계 — Stage5.5 이후

현재 Linux ARM64에서 수집→PIT→현재 lookback→정상 거래일 특징/pending→성숙 정답 새 버전→training_selection 경로를 구현·검증했다. [Stage5.5 검증](stage5_5_validation.md), [차단 진단](data_blocker_matrix.md), [운영 계약](../docs/observed_operation_contract.md), [실행 과제](data_readiness_action_plan.md)를 먼저 읽는다. Stage5 기준선/합성 artifact는 기존 기록이며 이번 실제 모델로 사용하지 않았다.

실제 기존40 dataset은 네 논리 표본의 반복 버전이다. 기존 strict 학습 적격0을 유지한다. IBM 공개 demo의 새100행 수신과 현재61세션 lookback 계산은 확인했지만, 오늘의 휴장 판정으로 거래 표본/pending 추가0이다. 이는 현재 받은 과거 가격을 과거 observed 이력으로 바꾸지 않는 의도된 동작이다. 실제 예측력 NOT_EVALUATED, 실제 학습 BLOCKED, 엄격한 역사 평가 BLOCKED다. 코드·현재 자료 접근까지 모두 막힌 것은 아니다.

새 코드 `src/market_research/observed`는 scope/diagnostics/one-shot/review/CLI이며 기존 collector/query/calendar/feature/label/store를 재사용한다. 기존 strict/learning_ready 의미는 유지하고 scoped_input_eligible/scoped_learning 판정을 분리한다. fixed_security_research와 forward_observed에 전체 폐지군을 일괄 요구하지 않는다. 개별 증권 ID/가격·세션/행동/상태·권한은 요구한다. 합성 모델의 실제 운영 로딩 인터페이스·주문·스케줄러는 없다.

다음 작업은 IBM 한 증권 운영 권한/키·검토된 필드/ID·action/state assertion 확보와 실제 정상 세션에서 pending 축적이다. JSON evidence 종류/필드/hash/time은 운영 계약에 있다. 신규 raw vintage는 승인 review hash에 포함되어야 한다. source·증권·기간·필드 의미를 추정해서 true로 쓰지 않는다. late/missed/failure는 기존 성공 원본/버전/checkpoint를 유지하며 새 유효 일정으로 재실행한다.

Stage6 진입 조건은 실제 이용권·해당 scope의 입력/정답/시각 계약과 최소100성숙 표본/180일, purge 후train60/validation20/test20, 해당 target/특징/분류 클래스가 있는 frozen dataset이다. Stage5 config에는 data_scope와 mode를 명시하고 단순 모델·cash·동일 비용/사전 임계값 비교를 먼저 수행한다. 외부 test로 후보/특징/임계값을 바꾸지 않는다. 이후에도 더 복잡한 모델이 필요한 근거를 먼저 확인한다. 합성 신경망으로 다음 단계를 정당화하지 않는다.

README의 Linux 실행·복구 명령과 `scripts/validate_stage5_5.py`를 사용한다. 현재 source hash가 바뀌면 기존 Stage5 artifact의 환경/코드 검사가 재로드를 거부할 수 있다. 원 Stage5/4 wheel·source·lock·입력은 보존하고 서로 다른 코드의 재현을 구분한다. 이번 변경과 기존 사용자 미커밋 변경은 모두 로컬 상태이며 커밋·푸시하지 않았다.
