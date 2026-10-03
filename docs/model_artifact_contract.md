# 모델 artifact 계약 5.0.0

실행 코드 없는 JSON만 저장/로드한다. pickle/joblib/cloudpickle 역직렬화는 사용하지 않는다. sklearn은 학습 adapter이며 추론은 저장된 수치 상태를 읽는다.

## 식별과 내용

model_id는 canonical content SHA-256이며 `<model_id>.json` 이름을 사용한다. producer=market_research.training, version=5.0.0, pipeline 및 pipeline_sha256, provenance, code_sha256, environment가 content에 포함된다.

pipeline에는 task/model_name/settings/seed/one_class_policy/notes, train-only preprocessing의 feature 순서/median/active index/mean/std/drop·missing 진단, constant 값 또는 linear 계수/절편 또는 tree children/feature/threshold/leaf value, export 검증 결과·rtol/atol가 있다. 분류 트리는 class 1 확률을 저장한다. sklearn와 같은 float32 입력 비교를 사용하므로 트리 경계 재현을 맞춘다.

provenance에는 target ID·정의 버전/entry·exit·미수금 정책, horizon, 특징, training_asof, dataset ID/hash, train membership 및 그 hash, label/feature version ID, split/fold/run config, 자료 이용 상태·시각·증권 ID·검증 수준·가격 계약/query/운영 상태가 있다. 입력 파일 hash는 dataset과 snapshot 계약을 통해 연결하고 학습 입력을 임의 최신 자료로 다시 조회하지 않는다. seed, 모든 소스 Python+pyproject hash, Python/OS/아키텍처·주요 library 버전을 기록한다.

실제 created_at/training_started_at/training_completed_at은 execution_metadata이며 별도 hash로 보호한다. model content ID에서는 실제 실행 시각을 분리한다. 논리적인 과거 training_asof와 실제 지금의 학습 완료 시각은 서로 다른 사실이다. 같은 수치 모델을 재생성할 때 기존 artifact의 최초 실제 실행 기록을 보존한다.

## 로드 검증

호출자는 프로젝트가 만든 training 결과의 model_id·artifact root·mode를 제공한다. root 내부 경로와 파일명, 최대 5MB, producer/version, content·pipeline·execution hash, 환경, 현재 코드 hash를 검사한다. mode가 다르면 거부하고 synthetic/research를 strict로 승격하지 않는다. 해시는 무결성 검사이며 외부 발급자의 서명이나 라이선스 진위 증명은 아니다. 허용 root/model_id는 검토한 로컬 결과에서 가져온다. 임의 pickle 파일을 가져오는 CLI는 없다.

추론은 현재 고정 dataset ID/hash에도 binding한다. 현재 model-predict 범위는 그 모델의 frozen validation/test 특징이며 다른 snapshot에 배포하는 일반 운영 기능이 아니다. 새 dataset 배포는 특징 단위/의미·target·권한·asof·호환성 검사를 설계한 뒤 추가해야 한다. strict offline 모델을 과거 operational 예측으로 변조하는 기능도 없다.

환경·코드가 바뀌면 원 소스/wheel/lock으로 재현하거나 새 모델 버전을 만든다. Stage4 원래 run은 Stage5 코드 변경 후 replay를 거부한다. 원 Stage4 source/wheel과 생성 당시 환경을 보관한다.

## 검증 범위

fit 후 sklearn 출력과 JSON adapter 출력을 train 행에서 비교한다(rtol=atol=1e-12). JSON 저장/재로드 예측 대조, 전체 holdout fixed prediction 재생, Stage4 result hash 재생을 수행한다. 예측 generated_at은 재계산 시각이 달라져 재현 비교에서 제외하고 나머지 연결·시각·출력 필드는 동일하게 검사한다. 저장된 원 예측 자체의 hash도 확인한다.

pipeline_sha256는 고정 환경에서 재학습 수치 상태를 비교할 때 사용할 수 있다. 모델 재학습의 전체 byte hash 일치를 플랫폼 간 보장하지 않는다. 실제 완료 시각, 수치 라이브러리·BLAS·플랫폼의 차이와 수치 허용오차를 구분한다. 이번 검증은 **저장 모델 재로드 및 fixed 출력 재현**이며 macOS 재학습 동일성 검증이 아니다.

result_content_sha256는 자신을 제외한 전체 결과 canonical hash다. results/<hash>.json에 보관하고 result.json은 최근 포인터다. prediction 파일 내용 hash·각 Stage4 run의 config/prediction/result/code/environment hash도 연결한다. model/Stage4 run ID가 같은데 내용이 다르면 거부한다. 기존 입력·dataset·snapshot·원본 파일을 덮어쓰지 않는다.
