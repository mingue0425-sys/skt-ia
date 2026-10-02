# 1단계 데이터 확보 가능성 조사 결과

> 저장소 공개본: 실제 수집은 원래 로컬 작업 공간에서 수행했다. `data/`와 행 단위
> `sample_quality.json`·`sample_cases.json`·`calendar_reference_review.json`은 이용 조건 미확인으로
> 커밋하지 않았다. 위치 표는 로컬 산출물의 위치이며 공개본에는 집계 품질 요약만 있다.
> 공식 문서 사례와 달력 대조의 상세 수동 검토 기록은 원래 로컬 작업 공간에 보존한다.


확인일:2026-10-02. 작업 위치:`/home/sechi/market-data-stage1`.
**수집 기반 PASS, 제한 프로토타입 CONDITIONAL, 엄격한 역사적 PIT 검증 BLOCKED**다.
학습·수익성·예측력·자동매매는 이번에 실행/평가하지 않았다.

## 실제 완료

작업 공간·부모 경로의 AGENTS.md, 기존 Python 프로젝트를 조사했다. 적용 AGENTS.md와
금융 프로젝트는 없었다. 홈의 .git 디렉터리는 정상 Git 저장소로 인식되지 않았으며
다른 프로젝트 코드를 수정하지 않고 새 디렉터리를 생성했다. 금융 요구 계약과
필수/후속/선택 필드를 먼저 작성하고 공식 제공처 문서·약관·시간 한계를 조사했다.

표준 Python collector에 제공처별 인터페이스, Yahoo/Alpha/Stooq/Nasdaq/문서 adapter,
원본·manifest·정규화 스키마, 품질 검사, CLI, 제한 retry/backoff/rate budget,
원자적 저장·실패 기록·캐시·정정 버전 보관을 구현했다. 성공 캐시의 byte hash와
정규화 hash를 확인하며, 코드 변경 시 raw 재다운로드 없이 오프라인 재정규화한다.
작은 순차 실행이고 학습·서비스·대형 DB를 추가하지 않았다.

## 실제 샘플

| 자료 | 실제 기간·크기 | 사례와 의미 |
|---|---|---|
| Yahoo AAPL/MSFT/NVDA/IBM/TSLA/META | 2024-01-02–12-31, 종목당252행 | 6개 미국 보통주 검증 샘플. OHLC를 raw로 주장하지 않음 |
| Yahoo SPY | 같은2024구간252행 | ETF 시장 proxy, 지수 자체가 아님 |
| Yahoo META 별도 | 2022-05-23–06-24,23행 | FB→META rename 경계. 현코드로 반환된 과거 FB기간12행 식별 문제 플래그 |
| Alpha Vantage IBM raw | full 응답1999-11-01–2026-10-01,6,770행; compact2026-05-11–10-01,100행 | 공개 demo 예외에서 실제 확보. full 응답 전체 보존, 품질 표본은2024년과2026년 비교 구간으로 제한 |
| Yahoo IBM 별도 | 2026-05-01–10-01,106행 | Alpha와 제공처 간 비교 |
| Yahoo events | 27개:배당26·분할1 | 배당락/분할 거래개시일 수준; 최초 선언/수정시각 없음 |
| Nasdaq 현재 directory | nasdaqlisted5,633행,otherlisted7,661행 | 현재 스냅샷, 역사Universe·보통주 전체목록 아님 |
| 공식 증거 문서 | NVDA FAQ,Meta rename 공지,MSFT 연차보고/배당공시,Activision 합병완료,NYSE2024/26,Nasdaq2026 | HTTP 원본 보존+내용 검토 별도. 정확한 경로·해시는 manifest 및 sample_cases.json |

품질 표본 합계는 **2,251 가격행·27 이벤트**(제공처/코드/날짜별, 중복 비교 구간은
정규화 aggregate에서 중복 제거), directory는13,294행이다. 2024년Yahoo 가격1,764행에
별도META23·Yahoo IBM106·Alpha IBM358행을 합한 값이다. full IBM 데이터의 나머지
과거행을 모두 calendar/lifecycle/기업행동 검증했다고 주장하지 않는다.

샘플은 수집기 검증용이고 과거 투자종목군이 아니다. 현재 생존 종목 목록은 과거
전체종목군을 대체하지 못한다. IBM 하나의1999년 이후 현재 응답은 다른 종목·폐지군의
10–15년 범위나 당시 최초값·수신기록을 입증하지 않는다.

## 사례의 실제 증거와 공백

- 정상 거래:6개 보통주와SPY의2024년 reference sessions252일 모두 관측했다.
- 분할:NVDA Yahoo 이벤트2024-06-10의10:1을 [NVIDIA 공식 FAQ](https://investor.nvidia.com/files/doc_downloads/2024/06/nvidia-2024-stock-split_faq_investors.pdf)와 대조했다. 실제 효력은6/7 마감 후, 분할 기준 거래개시6/10이다. 분할 전후 가격 연속성으로 과거분할 조정 형태를 확인했다(행 단위 값은 로컬 상세 결과에 보존). 독립 **원가격** NVDA 전후 구간이 없어 raw/adjusted 완전 대조는 건너뛰었다.
- 현금배당:MSFT Yahoo2/14=$0.75와 [Microsoft 배당 발표](https://news.microsoft.com/source/2023/11/29/microsoft-announces-quarterly-dividend-21/)의 배당락2/14·기준2/15·지급3/14를 확인했다. 페이지 날짜11/29와 본문 선언11/28은 구분하고 정확한 publication timestamp를 만들지 않았다.
- 코드변경:[Nasdaq 공지](https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2022-125)는 FB→META 효력2022-06-09를 확인한다. META 요청은 전후 가격을 반환하나 FB 직접 조회400이어서 검증된 영구ID 연결은 없다.
- 합병:[Microsoft 완료 발표](https://blogs.microsoft.com/blog/2023/10/13/welcoming-the-legendary-teams-at-activision-blizzard-king-to-team-xbox/)로 ATVI2023-10-13 합병 사건을 확인했다. [SEC8-K](https://www.sec.gov/Archives/edgar/data/718877/000110465923108985/tm2328253d1_8k.htm)는 웹에서$95 대가/거래정지 요청을 확인했으나 로컬 다운로드403. ATVI 가격404로 폐지 가격·최종거래일·수익률 연결 검증은 미완료다. 합병일을 검증된 최종거래/폐지 효력일로 대체하지 않았다.

NVDA의 현재 응답에서 2024-03-05 배당액은 0.004로 관측됐다. 위 공식 FAQ의
분할 전 분기 배당 0.04와 10:1 분할을 고려하면 분할 기준의 소급 표현으로 해석할
가능성이 있다. 이는 현재 응답과 문서에 기반한 추론이며, 0.004를 당시 실제 지급한
원래 현금액으로 사용하지 않는다. 제공처의 배당 기준 정의는 추가 확인이 필요하다.

## 실제 실행 검증

환경:Python3.13.5, Linux aarch64. exchange_calendars4.13.2, pandas3.0.6,
numpy2.5.3, tzdata2026.4; 전체 lock은 requirements-lock.txt에 있다.
macOS/M2 실기기 실행이나8GB 환경의 전체100종목 수집 benchmark는 수행하지 않았다.

| 실행 명령 | 결과 | 근거·범위 |
|---|---|---|
| `.venv/bin/python -m pip install -e .` | 성공 | 로컬 패키지 설치 실제 수행 |
| `.venv/bin/python scripts/verify.py` | **31/31 통과**, 실패0·건너뜀0 | reports/unit_test_results.json; 합성 fixture·오프라인만 |
| `.venv/bin/python -m market_research.cli collect` | 부분 실패 종료2 | execution-collect*.json과manifest; FB400/ATVI404/Stooq challenge 및 나머지 성공을 구분 |
| `.venv/bin/python -m market_research.cli smoke --refresh --output reports/execution-smoke.json` | **3/3 실제 HTTP200 성공**, 종료0 | AAPL·IBM demo·Nasdaq현재directory, cache로 대체하지 않음 |
| `.venv/bin/python -m market_research.cli validate` | 구조 **PASS**, 종료0 | 필드/형/날짜/중복/OHLC/volume/reference calendar 검사; sample_quality.json |
| `.venv/bin/python scripts/audit_artifacts.py` | **PASS** | 모든 보관 원본·정규화 해시, partial 파일0, 알려진 키·canary 검사, 네트워크 금지 상태 캐시 replay3요청. 원본 byte·mtime·manifest 수 불변 |

31개 테스트는 인증 vs empty,200JSON 권한거부/limit,missing/type/schema/array length,
중복과충돌,OHLC/음수/결측/0,달력·상장경계,큰움직임보존,동일재실행,수정응답,
partial resume,atomic interruption,cache corruption,code-change reprocessing,
secret redaction/echo,timeout/backoff/Retry-After,영속budget,DST/early close/비정기휴장 등을 검증한다.
합성 상장경계 테스트의 성공은 실제 상장폐지 날짜 확보를 의미하지 않는다.

실제 품질 결과:필수 schema/date/duplicate/OHLC/음수 volume 오류0; sample window의
reference session 누락0;26개 배당 조정식 내부 관계 tolerance5e-5 이내;
분할1개는 원자료 event 및 조정형태 관측. 제공처간358일 비교는152필드(거래량60,
가격92) 검토 플래그로 **REVIEW**다. 어느 쪽도 정답으로 선택하지 않았다.
약25.2% IBM 급락은2개 제공처 플래그로 보존했다. 상세는 cross_provider_review.md.

미실행/건너뜀:실제 전체 listing lifecycle boundary,독립raw NVDA split reconciliation,
historical initial receipt/version,전종목10–15년 완전성. 단위 테스트 통과와 실제
다운로드·역사PIT 성공을 동일시하지 않는다.

공식 달력의 2024년 NYSE 휴장·조기폐장과 2026년 Nasdaq 휴장·조기폐장 25개를
reference 일정과 대조했고 모두 일치했다. 범위와 원본 해시는 calendar_reference_review.json에 있다.

## 출처별 상태

| 출처·요구 | 네 상태 중 판정 | 미확인·미충족 |
|---|---|---|
| Yahoo sample prices/events | 실제 다운로드와 검증 완료 | 공개 API 보증/권한,raw/폐지군/영구ID/정밀시각/과거버전 미충족 |
| Alpha IBM raw | 실제 다운로드와 검증 완료 | demo 외 다른종목 권한·full premium·수정archive·cutoff 미확인 |
| Nasdaq current directory | 실제 다운로드와 검증 완료 | 역사Universe/보통주확정/코드이력은 제공하지 않거나 요구사항 미충족 |
| 공식 calendars/IR documents | 실제 다운로드와 검증 완료(일부 원문별) | 처음 틀린NYSE경로404는 정확경로로 해결; 당시문서버전·publication timestamp 없음 |
| Massive/Tiingo/Sharadar 기능 | 공식 문서에서 확인했지만 미검증 | API key/상품권한 미확인,계정·구매 미실행. 상세스키마·모든필드 실증 미완료 |
| Stooq/ATVI 가격/SEC로컬원문 | 접근 제한으로 확인 불가 | HTMLchallenge/404/403; 응답을 성공자료로 취급하지 않음 |
| historical PIT/universe/전체 raw | 제공하지 않거나 요구사항 미충족(현재 확보자료 기준) | 현재 history/adjusted/lastupdated는 당시 알려진 최초버전과 다름 |

공식 문서 URL·확인일·필드·범위·인증·limit/bulk·조정정의·폐지·시각·정정·저장/
분석/학습/재배포 조건은 docs/provider_comparison.md에 후보별로 기록했다.
Yahoo 약관 본문은 접근 실패; 이 이유로 이용 허가를 추정하지 않았다.
Alpha/Massive individual 및 Sharadar direct 개인 라이선스와 회사/기관 사용은 구별한다.
Tiingo/Sharadar17:30 제공은16:00+60분보다 늦을 수 있어 당일features 마감 충족을
단정할 수 없다. 이번 단계에서는 외부 계약 동의·키 발급·계정 생성·구매를 하지 않았다.

## 네 가지 최종 판단

| 판단 | 판정 | 근거·선행 조건 |
|---|---|---|
| 1.수집 기반 실행 | **PASS** | 설치/실다운로드/CLI/31테스트/manifest/캐시/부분실패 재실행 검증 |
| 2.제한 프로토타입 | **CONDITIONAL** | 데이터 처리·품질 파이프라인은 가능. 원가격은IBM만 확실하고 6개 공통raw/권한/cutoff가 부족; 학습은 이용권·의미 확인 후 |
| 3.생존편향·수정 관리 역사검증 | **BLOCKED** | 폐지군·permanent ID·당시코드/업종·최초 공개/수신 버전·일관raw가 없으며 현재adjustment vintage로 대체 불가 |
| 4.추가 비용·권한·자료 | **CONDITIONAL** | 일반 full/행동/과거master 권한과 보관·학습/모델배포 계약범위 확인 필요. 후보는 문서상 존재하나 미구매이며 historical vintage를 제공하는지도 실증 필요 |

## 재현과 다음 단계

README의 설치 명령 후 아래를 실행한다. 기존 성공 파일은 재사용하고 full collect는
접근 실패를 기록한 종료2가 예상된다. 네트워크 성공을 다시 확인할 때만 --refresh를 쓴다.

```bash
cd skt-ia
.venv/bin/python scripts/verify.py
.venv/bin/python -m market_research.cli collect
.venv/bin/python -m market_research.cli collect --config configs/evidence_supplement.json --output reports/execution-dividend-evidence.json
.venv/bin/python -m market_research.cli smoke --refresh --output reports/execution-smoke.json
.venv/bin/python -m market_research.cli validate
.venv/bin/python scripts/audit_artifacts.py
```

2단계는stage2_handoff.md의 계약/식별자/버전 조건부터 해결하는 범위로 진행할 수 있다.
현재 데이터로 생존편향 없는 확률예측 역사검증을 시작할 수 있다는 판정은 아니다.
2단계 전체 구현에는 착수하지 않았다.
