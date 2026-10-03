# 4단계 검증기·시뮬레이터 인계

3단계 특징·정답·데이터셋 엔진을 구현했다. 실행 수치/환경은 stage3_validation.json/md가
기준이다. 모델 학습·수익성·매매·4단계 전체 구현은 하지 않았다.

## 정확한 입력 인터페이스

```python
from market_research.pit import Database
from market_research.datasets import DatasetStore, replay_dataset, training_selection
from market_research.datasets.store import iter_dataset_rows

with Database("tests/generated/stage3/pit.sqlite") as pit, \
     DatasetStore("tests/generated/stage3/datasets.sqlite") as datasets:
    fixed = replay_dataset(datasets, pit, dataset_snapshot_id, verify_files=True)
    eligible = training_selection(
        datasets, pit, dataset_snapshot_id,
        training_asof="2024-06-01T00:00:00Z", allow_research=False,
    )
    for row in iter_dataset_rows(datasets, pit, dataset_snapshot_id, verify_files=True):
        feature, label = row["feature"], row["label"]
```

dataset_snapshot_id/content_sha256, sample_id, horizon_sessions, feature_version_id/
label_version_id를 보존한다. feature_snapshot_id와 label_snapshot_id/action_snapshot_id로
PIT 원본·정규화·수신·normalizer 근거를 확인한다. replay는 현재 query를 다시 실행하지 않는다.
label-update 후 새 데이터셋을 만들어도 이전 membership과 값/hash는 변하지 않는다.

feature_cutoff/decision/ready는 label_asof/simulation_asof/available과 별개다.
decision=regular close+60분, entry=다음 session open(e), exit=open(e+h), h=1·5·20이다.
ready time 기본값은 명시 처리 지연 가정이다. actual_operational_record가 없는 표본은
execution_status=conditional이고 자동 learning_ready가 아니다. 계산 가능은 역사 공개 버전·
매매 가능성·권한을 증명하지 않는다. 실제 운영 시점 기록을 임의로 소급하지 않는다.

## 반드시 검사할 상태

feature sample_status와 각 label status를 모두 검사한다. invalid/blocked/pending을 조용히
삭제하지 말고 reason별 개수를 유지한다. unavailable 특징은 null이다. 다른 의미의 OHLC나
forward/back fill로 채우지 않는다. malformed 일정은 invalid로 보존하고 query ID는 null이다.
정의 3.0.1에서는 0/음수 가격·비유한 값 등 invalid 특징이 있으면 표본도 invalid다.
나중 simulation_asof로 과거 label_asof의 대기 상태를 바꾸지 않는다. 정답 성숙 갱신은 새
평가 cutoff로 수행한다. pending 정답의 price/total/holdings 수치는 null로 유지한다.
NOT_STRICT_PIT, temporary ID, provider label, 미검증 달력/세션/거래량, 권한 미확정 자료를
엄격한 역사 검증에 승격하지 않는다. allow_research=True는 진단이며 학습 허가가 아니다.

재학습 시점마다 training_asof를 명시한다. frozen 정답이 ready여도 label_available_at/
feature_ready_at/권한 알려진 시각이 이후면 사용할 수 없다. 최신 정답으로 과거 frozen 정답을
덮어쓰지 않는다. 실제 학습은 수행하지 않는다.

price_return_h는 검증된 split 수량을 반영하고 현금배당을 제외한다. cash_total_return_h는
별도 값·상태·ledger다. 초기수량 1, 재투자 없음, ex-date 권리, 보수적 날짜 지급 판정,
미수배당 포함 여부를 보존한다. 합병·상장폐지 대가는 미지원이며 손실 0/마지막 가격으로
대체하지 않는다. as_traded close_price_change_n 특징은 분할 경계에서 경제적 수익률이 아니다.
진입 전 선언일만 알려지고 effective/ex 날짜가 없으면 보유 구간 밖으로 단정하지 않고
blocked로 반환한다. 계산 overflow/underflow는 invalid이며 수익률로 내보내지 않는다.

## 실제 자료 범위와 선행 조건

Stage2 DB를 backup한 `data/stage3/pit_market.sqlite`에 새 query snapshot만 추가했다.
최초 backup 뒤 작업 DB를 재사용하며 덮어쓰지 않는다. 원 DB/시장 원본은 변경하지 않았다. 실제 dataset DB/config/계산 진단은 `data/stage3/`에
있고 공개 저장소에는 없다. 합성은 `tests/generated/stage3/` 및 공개 손계산 fixture다.

AAPL에는 split-adjusted OHLCV와 total-return-adjusted close_only가 모두 있다. 이번 진단은
close_only 61세션의 종가 특징만 사용한다. IBM demo는 as_traded OHLCV 61세션으로 가격 변화
특징을 계산한다. 두 현재 수신 진단은 **과거 의사결정/학습 표본이 아니다**. 2026년 수신을
과거 observed로 소급하지 않아 실제 의사결정 ready 특징/정답/학습 표본은 0이다.
정책별 제외와 blocked/pending의 정확한 수는 검증 JSON에 있다.

4단계에서는 합성으로 검증기/시뮬레이터의 타입·누수·인덱스·성숙 상태·snapshot 계약을
개발할 수 있다. 실제 역사 검증 전 우선순위 조건은 다음과 같다.

1. 저장·분석·모델 학습·재배포 권한과 검증 가능한 license assertion 확보.
2. 보통주/증권 단위 영구 ID, 역사 ticker/상장·폐지·정지 상태와 당시 universe 확보.
3. source public/version-content/archive 근거와 실제 received/usable 이력 확보.
4. 원 OHLCV 통화/정규장·거래량 의미, action complete coverage/현금액/순서/지급/폐지 대가 검증.
   빈 actions 응답을 verified_no_actions로 바꾸지 않는다.
5. 공식 과거 calendar notice 적재 인터페이스·증거 보완. 현재 규칙 달력 known_at/
   historical_evidence 공백 때문에 strict schedule 근거가 없다.
6. 실제 feature processing/entry 가능성과 지연 기록. M2 실기기에서 동일 테스트, UTC/DST/
   조기폐장, migration/FK/hash, 메모리·속도를 확인. 이번 Linux ARM64 실행과 구별.
7. 이후 시간순 분할과 h=20 중첩 구간 purge/embargo, 비용/정지/폐지 처리 계약 설계.
   이번 단계는 이를 구현하거나 수익성을 주장하지 않는다.

## 재현

README의 prepare_synthetic_stage3→dataset-build→verify/replay→training-check를 따른다.
`.venv/bin/python scripts/validate_stage3.py`는 네트워크를 쓰지 않는다. 실제 DB가 없는
공개 clone에서도 오프라인 검증을 완료하고 실제 자료는 BLOCKED로 기록한다.
재수집은 기존 수집기 허용 범위에서 별도로 실행하며 성공/부분 실패를 구분한다.
