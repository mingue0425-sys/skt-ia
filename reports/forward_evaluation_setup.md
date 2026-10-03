# 최소 전향 평가 준비 — 2026-10-03

**준비 완료, 실제 전향 예측은 미실행.** 현재 뉴욕 현지 날짜는 토요일이다.
실제 캐시 dry-run에서 5종목 모두 두 저장 모델 추론에 성공했다. 전향 예측·성숙 표본·고유 평가 날짜는 모두 0이며 Brier/Log Loss/실제 상승 비율은 아직 null이다.
이 보고서는 과거 validation/test 지표를 이번 실행 성과로 복사하지 않는다.

구현은 [단일 스크립트](../scripts/run_forward_evaluation.py),
[고정 설정](../configs/forward_evaluation.json), [필요 경로 fixture](../tests/test_forward_evaluation.py)다.
공통 `src/`와 학습 설정은 수정하지 않았다. 기존 observed-run은 Alpha Vantage 단일 IBM 전용이므로,
EODHD 5종목의 기존 collector/PIT query/feature builder/model loader/JSON predict/label generator/metric을 연결하는 스크립트를 추가했다.
기존 저장소에는 쓰지 않고 gitignored `data/forward_evaluation/`에만 새 DB·기록을 추가한다.

## 고정 비교 대상과 실제 artifact 감사

두 artifact가 실제 존재하며 기존 `load_model(..., mode='research')`의 파일·content·pipeline·실행 메타데이터 hash, Linux aarch64 환경, 원 소스 hash 검사를 통과했다.
재생성·재학습·새 후보 탐색은 수행하지 않았다. 후보는 실험용이며 Champion·매매에 사용하지 않는다.

| 대상 | model ID / content hash | 실제 artifact 파일 SHA-256 | 고정 값/설정 |
|---|---|---|---|
| train 상승 빈도 | `6d46ecb46aa0289771b08fc93dc03dff1db17ea8beff8ea91ced5fdf7eb127a2` | `e5966c29cfd7ff731ed768f732932eedd0e39af71945cfa2b0b21a6d181b2dfb` | alpha=1, `(361+1)/(680+2)=0.530791788856305` |
| 분류 트리 후보 | `f18ca8719429b5fb5f7022d4ff3b77eab723801044a9429ec12b7fb570659f64` | `d0f1af9ff596ff6c1c28f76b026e38dbe1d7ef886fe274503505723b70a7e369` | max_depth=3, min_samples_leaf=32, seed=20261003 |

경로는 각각 `data/real_data_baseline/classification/models/<model ID>.json`,
`data/real_data_baseline/diagnosis/classification/models/<model ID>.json`이다.
artifact 실제 training_membership과 기존 불변 feature/label payload를 읽어 대조했다.
두 모델 모두 AAPL/MSFT/AMZN/TSLA/MCD, **680행·136개 고유 월요일 결정 날짜, 2021-10-04~2024-09-16**이며 상승 정답은 361개다.
원 요청 train 범위는 2021-10-01~2024-09-30, 논리적 training_asof는 2024-10-01 00:00 UTC다.
당시 실제 학습 실행 시각과 training_asof는 다르다.
공통 training_membership hash는 `39892625fb4e249cb04b51d287f2c4454d45633e693aeba552a38d7cc4aab04e`,
원 dataset은 `768f8e2ccda885674081e03b104919bc16bb1e466de105e29f4a530a5f3548d5`다.

입력 순서는 `close_price_change_5`, `close_to_ma_20`,
`realized_log_price_change_volatility_20`, `intraday_high_low_ratio`, `volume_to_prior20_mean`이다.
두 저장 전처리가 동일하며 fit_count=680, active_indices=[0,1,2,3,4], scaling/clipping 없음이다.
train 중앙값은 `[0.0010452412488697327, 0.004768321490752014, 0.016725813806283713, 0.021020351802038095, 0.9174815463224479]`다.
전처리 hash는 `9d34079b91a1a1a0d0c49c652e24bb15bf51d02cc152495a38f1aa120e7c9330`이다.
이번 경로는 필요한 5개 특징이 모두 ready인 표본만 허용하므로 누락 자료를 중앙값으로 통과시키지 않는다.
동일 projection을 두 저장 pipeline에 전달하고 확률·전처리·특징·설정을 새 결과에 따라 갱신하지 않는다.
고정 설정 canonical hash는 `090a1360915249aa3752528526a6f7be5d8ca77dce02a00a0bfe4e9f5861c29d`이며 스크립트에서 검사한다.

학습은 연구용 현재 과거 버전인 `research_assumed / NOT_STRICT_PIT`다.
원가격 특징의 분할 경계, 현재 버전 분할 조정 volume, 공급자 indicative OHLC, 역사적 거래 상태·기업행동 완전성 및 존속 5종목 선정 한계를 유지한다.
새 입력의 실제 수신 기록이 과거 학습 자료의 한계를 제거하지 않는다. 합성 모델은 사용하지 않는다.

## 실제 코드의 시간·target 계약

- 선택: `run_real_data_baseline.py`의 달력 세션 중 `weekday()==0`만 선택한다. 휴장 월요일은 해당 주를 건너뛰며 화요일로 이동하지 않는다.
- 달력: XNYS, America/New_York, `exchange_calendars=4.13.2`. 원 고정 `calendar.json`을 재사용하며 파일 hash는 `8889ebad2fefa4d355f7f38bb303e8388dc9e4be51ec005bc3828309f49a5df9`다. 제공 범위는 2026-12-31까지이며 target이 범위 밖이면 차단한다.
- 결정: 기존 `SessionCalendar.plan()`대로 해당 월요일 regular close+60분. 기존 학습 feature_cutoff는 decision_at이다.
- 운영 cutoff: 기존 observed의 after_processing 방식으로 실제 수집·처리·import 완료 시각을 feature_cutoff로 기록한다. **close ≤ feature_cutoff ≤ feature_ready_at ≤ generated_at ≤ decision_at < target_start_at**을 요구한다. 이 시각은 매 실행 실제 clock이며 과거/미래 시각을 지정하는 옵션은 없다. cutoff를 늦춰 decision deadline을 연장하지 않는다.
- 입력: 해당 월요일까지 일별 OHLCV, 최대 필요한 21세션(기존 builder 조회 61세션). `observed` query에서 실제 receipt와 usable이 cutoff 이하인 버전만 선택한다. 월요일 bar가 없거나 required 특징이 부족하면 해당 종목을 차단한다. 금요일 가격을 월요일 최신 자료로 쓰지 않는다.
- target: 저장 provenance 및 기존 `generate_labels/compute_labels`의 **next session open(e) → e+5 session open**, target_id=price_return/horizon=5, `up_5=(price_return>0)`. 원시 시가를 사용하고 검증된 분할은 보유 수량에 반영하며 배당 현금은 제외한다. 종가 target으로 바꾸지 않는다.
- 허용 실행: 월요일 마감 이후 close+60분 이전에 특징·추론·기록 완료. decision deadline 또는 target 시작을 넘기면 missed. 사후 예측을 과거 생성 시각으로 저장하지 않는다. 주말·휴장·장 마감 전·미래 decision 요청은 현재 마지막 종료 세션에 대한 dry-run이며 전향 파일/target pending을 만들지 않는다.

다음 후보 **2026-10-05는 정상 세션**이며, 고정 달력과 [NYSE 공식 2026 달력](https://www.nyse.com/publicdocs/nyse/ICE_NYSE_2026_Yearly_Trading_Calendar.pdf)을 확인했다.
거래소 달력은 개별 증권 정지 여부나 당일 임시 공지의 증거를 대신하지 않는다.

| 사건 | UTC | 뉴욕 EDT | 한국 KST |
|---|---|---|---|
| 월요일 마감 / 실행 창 시작 | 2026-10-05 20:00 | 10-05 16:00 | 10-06 05:00 |
| 권장 단일 실행 시작 | 2026-10-05 20:20 | 10-05 16:20 | 10-06 05:20 |
| decision_at / 완료 deadline | 2026-10-05 21:00 | 10-05 17:00 | 10-06 06:00 |
| target 진입 시가 | 2026-10-06 13:30 | 10-06 09:30 | 10-06 22:30 |
| e+5 종료 시가 | 2026-10-13 13:30 | 10-13 09:30 | 10-13 22:30 |
| 종료 일별 bar 관측 완료 | 2026-10-13 20:00 | 10-13 16:00 | 10-14 05:00 |

10월 12일도 이 달력에서 주식시장 거래일이므로 종료는 10월 13일이다.
정답 종료 시가 도달만으로 ready가 되지 않는다. 종료 일별 bar 완료·실제 수신/처리·필수 정답 근거 이후에만 평가한다.

## 단일 실행·보존·정답 갱신

```bash
cd /home/sechi/Stk-ia
# 지금 가능한 네트워크 없는 입력/모델 연결 검사
.venv/bin/python scripts/run_forward_evaluation.py --dry-run --cache-only
# 다음 유효 창에 수동 1회 실행: 고정 모델 확인 → 수집/캐시 → 예측/pending → 과거 성숙 정답 갱신
.venv/bin/python scripts/run_forward_evaluation.py
# 성숙 결과의 새 수신/정정을 수동 1회 확인; 예측 생성은 하지 않음
.venv/bin/python scripts/run_forward_evaluation.py --update-only --refresh
```

일반 실행은 기존 EODHD 공식 demo의 5종목 daily/splits/dividends/identity만 요청한다.
새로 완료된 마지막 세션까지 2026-07-01 이후 자료를 수집하며 성공 캐시도 실제 receipt/processing 시각을 그대로 사용한다.
`--refresh`는 원본을 보존하며 요청마다 최대 1회·timeout 20초의 기존 HTTP 경로를 사용한다. 스케줄·서비스는 활성화하지 않았다.
오늘 수신한 과거 가격은 오늘 lookback으로 사용 가능하나 과거 cutoff 입력으로 소급 사용하지 않는다.
과거 decision을 요청하면 missed 또는 기존 저장 예측의 재생만 반환한다. 이번 스크립트에는 과거 예측 계산 기능을 추가하지 않았다.
별도 사후 다운로드/과거 모델 추론은 retrospective_replay이며 이 전향 저장소·지표에 넣지 않는다.

기록은 다음 기존 구조에 연결된다.

| 위치 | 보존 내용 |
|---|---|
| `collection/raw`, `normalized`, `manifest` | 실제 HTTP receipt, 처리 시각, 공급자 원본과 새 normalization version; 동일 ISIN/current identity 대조 |
| `pit.sqlite` | 실제 observed query/가격 버전/고정 달력/feature snapshot과 hash |
| `datasets.sqlite` | 기존 DatasetStore의 불변 feature/label 버전; 미래 5일 pending, 성숙·정정 정답 새 버전 |
| `predictions/<pair ID>.json` | 두 모델의 prediction_id, 증권 ISIN·symbol, decision_at/cutoff/generated_at, target ID/시각, 모델·artifact hash·기준선 값/설정 ID, feature snapshot ID/hash, 입력·수신/처리 근거, probability_up, prospective/pending 구분 |
| `label_updates/<pair ID>/` | 원 예측과 성숙 정답 버전 연결, 평가 시각·상태·사유·근거 hash |
| `executions/` | 봉인된 실행별 dry_run/blocked/missed/부분 실패, snapshot·처리 근거, 짧은 집계 결과 |

한 root는 Linux flock으로 단일 writer만 허용한다. 두 예측은 종목별 한 파일로 저장한다.
동일 모델·증권·decision의 동일 특징/입력 버전은 원 파일·generated_at을 반환하며, 상충 입력은 `PREDICTION_INPUT_CONFLICT`로 기록한다.
마감 후 재실행에서도 이미 저장된 예측은 원 snapshot을 확인해 재생하며 새 예측을 생성하지 않는다.
실패한 종목의 이유는 따로 남고 성공 종목의 파일은 유지된다.
봉인 파일은 내용 hash를 확인하며 원 예측·label 버전은 덮어쓰지 않는다. `latest.json`만 실행 요약 포인터로 갱신한다.
로컬 생성 시각·hash는 재현 기록이며 독립적인 제3자 시각 인증이 아니다.

성숙 후 두 모델 모두 같은 원 예측 쌍·ready 정답에서 기존 `stage4.metrics.evaluate`로 비교한다.
고유 결정 날짜 수, 전체/종목별 성숙 표본 수, Brier, natural-log Log Loss(epsilon=1e-15), 실제 상승 비율,
label pending/blocked/invalid 수와 누락 예측·실패·missed 수만 요약한다.
누락 예측/실패는 같은 decision·종목의 최신 실행 상태로 집계하므로 재시도 횟수를 표본 수로 세지 않는다.
아직 평가 시도가 없는 미래 결정일은 누락으로 세지 않는다.
5종목×1날짜는 1개 시장 의사결정 날짜다. 모델 교체·재학습·승패 판정이나 소수 주간의 일반화 결론은 없다.

## 이번 실제 실행과 필요한 검사

실제 실행은 `--dry-run --cache-only`다. 새 HTTP 다운로드 없이 오늘 실제 수신/처리된 기존 가격을
2026-10-02 anchor의 현재 lookback으로 조회했다. **dry-run 5종목 성공, 두 확률씩 생성, 전향 파일 0개**다.
고정 모델·현재 Linux ARM64 환경·calendar hash를 실제 loader로 확인했다.
실행 시각·각 feature snapshot ID/hash·확률·actual input availability는 로컬 `executions/`에 보존했다.
`--cache-only --update-only`도 실행했으며 실제 기존 전향 예측이 없어 갱신 대상은 0개다.

필요 경로만 검증한 명령:

```bash
.venv/bin/python -m unittest discover -s tests -p test_forward_evaluation.py -v
git diff --check
```

3개 작은 fixture 검사 PASS: 고정 모델/설정 거부, 같은 특징의 두 추론, cutoff 이후 실제 수신 및 입력 guard,
주말/휴장·target 시작 이후 missed, 동일 입력 재생·상충 입력 거부,
TSLA 실패 중 다른 4종목 보존, pending→5개 성숙 쌍/1개 고유 날짜 연결,
사후 가격 정정→새 label 버전/원 예측 및 구버전 유지.
fixture HTTP·가격·증거·clock은 임시 디렉터리에서만 사용했으며 실제 미래 수신/예측 성공이 아니다.
전체 학습·회계·snapshot 검증은 재실행하지 않았다.
기존 `data/real_data_baseline/` 파일 179개를 전후 hash로 확인해 변경 0개이며 시작 시 git 미커밋 변경은 없었다.

## 남은 실제 데이터 조건

첫 전향 실행에는 **10월 5일 bar 및 21세션 required 특징**이 허용 창 안에 실제 수신·처리돼야 한다.
현재 캐시는 10월 2일까지이므로 그 자체로 10월 5일 예측을 만들 수 없다.
공급자 설명은 [EODHD 일별 자료 명세](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes)를 따른다.
이번 토요일 다운로드 기록으로 월요일 close+60분 준수나 공급자 갱신 지연을 검증한 것은 아니다.
창을 놓치거나 늦은 bar만 받은 종목은 blocked/missed로 보존하고 소급 허용하지 않는다.
실제 지연이 반복되면 관측 receipt/usable과 기존 decision 계약의 변경 필요를 보고해야 한다.

정답용 기간 기업행동 complete coverage와 개별 종목 거래 상태는 **현재 미확보**다.
기존 10월 2일까지의 research_assumed evidence를 연장하거나 빈 split/dividend 응답을 완전성 증명으로 바꾸지 않았다.
아직 미래인 target은 pending이며, 종료 후에도 근거가 없으면 기존 generator가 blocked와 이유를 남긴다.
외부 근거가 실제로 확보·검토되면 gitignored `data/forward_evaluation/label_evidence.json`에 다음 참조만 추가한다.
이는 모델·전처리·특징·확률·예측을 수정하는 설정이 아니다.

```json
{
  "AAPL": {
    "action_coverage": {"path": "/actual/reviewed/coverage.json", "sha256": "actual-file-hash"},
    "trading_state": {"path": "/actual/reviewed/state.json", "sha256": "actual-file-hash"}
  }
}
```

다른 4종목도 같은 source/symbol별 참조가 필요하다. 정확한 assertion 필드는 기존
[정답 계약](../docs/label_contract.md)과 [observed 근거 계약](../docs/observed_operation_contract.md)을 따른다.
`verified_complete` coverage는 보유 기간의 split/특수행동 완전성 검토·효력일/동일일 순서를 포함하고,
state는 상장·정지·폐지·verified_through를 포함한다. 실제 observed receipt/usable와 hash가 결합돼야 한다.
예제의 빈 값·가짜 검증값으로 승인하지 않는다. 결과 누락은 기존 generator의 정상 수신 지연(종료 close+48시간)과 과거 endpoint 누락을 구분한다.
원본이나 저장 모델이 없는 공개 checkout은 모델/파일 오류로 종료하며 합성 대체를 하지 않는다.
