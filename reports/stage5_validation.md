# 5단계 검증 보고서

현재 실행: 2026-10-03T04:47:34.295372+00:00 · HEAD `fb78fc7d712dcd285f9e463ec3673fe732cdc0a5`. 기존 Stage2~4 미커밋 구현을 확인하고 보존했다.

## 1. 시작 상태와 실제 변경

기존 145 테스트 통과를 재확인했다. 학습 runner·전처리·JSON artifact·CLI·신호/무신호 fixture와 테스트를 추가했다. 기존 소스 변경과 자동 생성된 build metadata는 JSON에서 분리해 기록했다. 검사에서 변화가 관측된 기존 파일: src/market_research/cli.py, src/market_research_stage1.egg-info/SOURCES.txt, src/market_research_stage1.egg-info/PKG-INFO, src/market_research_stage1.egg-info/requires.txt, src/market_research/stage4/simulation.py, src/market_research/stage4/metrics.py, src/market_research/stage4/predictions.py, README.md, pyproject.toml, requirements-lock.txt.

## 2. 실행 환경·테스트·빌드

Python 3.13.5/Linux aarch64, sklearn 1.9.1. 전체 164/164 통과. wheel 42개 모듈이 소스와 byte 일치. 로컬 wheel을 임시 경로에 offline 설치한 뒤 42개 프로젝트 모듈이 모두 wheel 경로에서 import됐고 training-config-check 종료0을 확인했다. pip check PASS. git diff --check PASS.
전체 테스트 215.42초 / 최대 RSS 237488 KiB. 실제 로그·명령은 JSON과 data/stage5/release에 있다.

## 3. 학습·전처리·미래정보 차단

고정 dataset/hash·training_selection·Stage4 날짜 분할/purge 사용. fit은 train만 받고 추론은 특징만 받는다. 전부 결측/상수 열은 train에서 drop, median/scaler는 train에서만 fit. 구조적으로 없는 필드는 거부. test 후보 선택 기능 없음. 합성·research를 strict로 승격할 수 없다.

## 4. 기준선·합성 결과

5거래일 price_return 회귀와 같은 정의의 상승분류를 독립 실행했다. zero/mean/Ridge/tree 회귀, smoothed frequency/Logistic/tree 분류. 두 합성 dataset, 각각 2 folds. seed=20261003 고정. 생성 공식과 시각은 JSON generator에 있다.

| 자료 | 과제 | fold | 모델 | train | test | MAE | Brier | fills |
|---|---|---|---|---:|---:|---:|---:|---:|
| signal | regression | 0 | zero | 89 | 25 | 0.03427959452382202 | None | 0 |
| signal | regression | 0 | mean | 89 | 25 | 0.028509625178205364 | None | 10 |
| signal | regression | 0 | ridge | 89 | 25 | 0.02851063373440369 | None | 8 |
| signal | regression | 0 | tree | 89 | 25 | 0.028936871968883778 | None | 8 |
| signal | regression | 1 | zero | 119 | 20 | 0.04311841586122406 | None | 0 |
| signal | regression | 1 | mean | 119 | 20 | 0.04165825983454377 | None | 0 |
| signal | regression | 1 | ridge | 119 | 20 | 0.04107748109300751 | None | 6 |
| signal | regression | 1 | tree | 119 | 20 | 0.0422563866314933 | None | 2 |
| signal | classification | 0 | frequency | 89 | 25 | None | 0.21881898321458762 | 0 |
| signal | classification | 0 | logistic | 89 | 25 | None | 0.26418824380047945 | 8 |
| signal | classification | 0 | tree | 89 | 25 | None | 0.22148669921979944 | 8 |
| signal | classification | 1 | frequency | 119 | 20 | None | 0.2524964141793593 | 0 |
| signal | classification | 1 | logistic | 119 | 20 | None | 0.28982559806330377 | 6 |
| signal | classification | 1 | tree | 119 | 20 | None | 0.3188633628029036 | 6 |
| null | regression | 0 | zero | 89 | 25 | 0.008975786506643428 | None | 0 |
| null | regression | 0 | mean | 89 | 25 | 0.012399531962499556 | None | 0 |
| null | regression | 0 | ridge | 89 | 25 | 0.011109356942010856 | None | 6 |
| null | regression | 0 | tree | 89 | 25 | 0.011903617900778193 | None | 8 |
| null | regression | 1 | zero | 119 | 20 | 0.014088967234629091 | None | 0 |
| null | regression | 1 | mean | 119 | 20 | 0.01975816796070721 | None | 0 |
| null | regression | 1 | ridge | 119 | 20 | 0.017830179096496625 | None | 0 |
| null | regression | 1 | tree | 119 | 20 | 0.014624482758246609 | None | 6 |
| null | classification | 0 | frequency | 89 | 25 | None | 0.3675836251660428 | 0 |
| null | classification | 0 | logistic | 89 | 25 | None | 0.3521375427871831 | 8 |
| null | classification | 0 | tree | 89 | 25 | None | 0.348053125 | 8 |
| null | classification | 1 | frequency | 119 | 20 | None | 0.44812512806502286 | 0 |
| null | classification | 1 | logistic | 119 | 20 | None | 0.32128286678904394 | 4 |
| null | classification | 1 | tree | 119 | 20 | None | 0.31361882716049383 | 4 |

신호 자료에서도 Logistic은 두 fold 모두 frequency보다 Brier가 높았다. 이를 성공 판정에 맞추려고 seed/설정을 바꾸지 않았다. 이는 모델·평가·회계 연결 검사다. 합성 MAE·Brier·체결·손익은 실제 예측력 또는 수익성 증거가 아니다. 무신호 자료의 우연한 성과를 허용하고 seed를 변경하지 않았다. 회귀 .005/분류 .55 임계값과 비용을 사전에 고정했다. 서로 다른 회귀·분류 결정 규칙은 전략 비교에 영향을 준다. zero 거래 0은 정상이다. cash 기준선 포함. 보정 모델은 적용하지 않았다.

## 5. 모델 재로드·예측·Stage4 연결

28개 모델 artifact 재로드·sklearn export 대조·fixed prediction 재생·Stage4 result hash 재생 PASS. 독립 train-only 재학습에서 저장 pipeline 내용 hash 28/28 일치, holdout 예측은 rtol=atol=1e-12 대조를 통과했다. 이는 현재 pinned Linux 환경의 결과이며 플랫폼 간 byte 일치 보장은 아니다. 별도 CLI 평가의 핵심 지표/calibration과 계좌 결과도 원 실행과 일치했다. 회귀 확률/classification 수익률은 null이다. Stage4 calibration 집계를 재사용한다. 모델 완료 시각은 실제 이번 시각, training_asof/simulated_generated_at은 논리적 과거 시각이다. 원본 Stage4 run은 코드 hash 변경으로 현재 소스로 replay를 거부하며 원 Stage4 wheel/source가 필요하다.

## 6. 실제 자료 적격성과 미실행

실제 strict 적격 0개(입력 부재 시 null). 특징 {'blocked': 8}, 정답 {'blocked': 18, 'pending': 6}. 현재 로컬 입력이 있을 때 4 dataset의 training-config-check/run 총 8 요청은 종료3/BLOCKED. 실제 학습·예측력·수익률·Sharpe·적중률은 생성하지 않았다. 실제 예측력 `NOT_EVALUATED`.
실제 기존 40 dataset 전체 training_selection(allow_research=False)에서도 적격 0을 별도 확인했다. 실제 기존 40 dataset 고정 replay, Stage2 352행과 DB bytes 불변. 기존 Stage3 합성 7개 replay와 DB bytes 불변도 PASS다. 사유별 건수는 JSON real.source_reason_counts/eligibility_exclusion_counts에 있다.

## 7. 실제 데이터 병목별 다음 조치

[실행 과제](data_readiness_action_plan.md)에 역사 연구/앞으로의 observed 축적, 정확한 필드·권한·담당 입력·최소 검증·명령을 분리했다. 현재 원본·계약은 추가 확보하지 않았다.

## 8. 6단계 진입 조건

실제 권한·ID·raw 가격 의미·기업행동·상태·시각·달력·성숙 정답을 충족하는 고정 자료와 의미 있는 시간순 train/validation/test 확보 후 단순 모델 비교가 선행 조건이다. 현재 BLOCKED. 합성 신경망으로 다음 단계를 정당화하지 않는다. [인계](stage6_handoff.md).

## 9. M2 및 자원

M2 8GB 실기기는 NOT_EVALUATED. Linux ARM64 수치와 구분한다. 개별 synthetic training os.wait4 rusage 측정:
- signal/regression: 이전 완료 학습의 측정은 최종 집계 실패 전에 보관되지 않아 NOT_RECORDED; 검증된 모델/결과 hash를 확인해 재사용했다.
- signal/classification: 이전 완료 학습의 측정은 최종 집계 실패 전에 보관되지 않아 NOT_RECORDED; 검증된 모델/결과 hash를 확인해 재사용했다.
- null/regression: 이전 완료 학습의 측정은 최종 집계 실패 전에 보관되지 않아 NOT_RECORDED; 검증된 모델/결과 hash를 확인해 재사용했다.
- null/classification: 이전 완료 학습의 측정은 최종 집계 실패 전에 보관되지 않아 NOT_RECORDED; 검증된 모델/결과 hash를 확인해 재사용했다.
- 28개 train-only 합성 재학습·저장 pipeline/holdout 대조: 59.90초, 최대 RSS 291,136 KiB (약 284.3 MiB). 입력 검사·재학습·출력 대조 범위이며 전체 Stage4 실행 시간과 구분한다.
- 현재 저장 모델 추론→평가→시뮬레이션: 69.95초, 최대 RSS 255984 KiB (개별 child/그 하위 순차 작업 rusage)
- 현재 저장 모델 추론→평가→시뮬레이션: 69.85초, 최대 RSS 255984 KiB (개별 child/그 하위 순차 작업 rusage)

원 학습은 4개 독립 작업 동시 실행이었다. 측정 항목별 범위를 구분하며 합산 최대메모리/단독 benchmark가 아니다. 설치 시 scikit-learn ARM64 wheel과 의존성을 내려받았다. 유료구매·계정 생성·외부 주문·반복 스케줄·커밋·푸시는 하지 않았다. 기존 SQLite ResourceWarning은 기록했다. Codex 사용량 UI 알림은 코드 경고가 아니다.

최종 추가 확인: 2026-10-03T04:59:42.091289+00:00 · 새 공개 후보 자격정보 패턴 0개. 18개 필수 위험의 테스트/실행 근거는 JSON risk_evidence를 참조한다.
