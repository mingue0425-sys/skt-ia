# 금융 데이터 확보 조사 — 1단계

실제 데이터를 받는 작은 Python 수집기와 확보 가능성 보고서다. 예측/학습/거래는
구현하지 않는다. [최종 판단](reports/stage1_data_feasibility.md),
[품질 요약](reports/sample_quality.summary.json), [2단계 인계](reports/stage2_handoff.md)를 먼저 본다.

최초 조사와 실제 수집은 별도 로컬 작업 공간에서 실행했다. 이 저장소에는 코드·설정·문서·
합성 테스트 fixture와 집계 검증 보고서를 포함한다. 이용 조건이 확정되지 않은 시장 데이터
원본·정규화 파일·행 단위 품질 결과는 포함하지 않는다. 상세 결과는 로컬 수집 후 생성한다.
Python3.13.5/Linux ARM64에서 실제 실행했다. Apple Silicon용 표준 Python 패키지이며
서비스·GPU·분산 프레임워크·macOS/Linux 전용 수집 API는 없다. M2 8GB 실기기는 미시험이다.

```bash
git clone https://github.com/tpcls/Stk-ia.git
cd Stk-ia
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/verify.py
.venv/bin/python -m market_research.cli collect
.venv/bin/python -m market_research.cli collect --config configs/evidence_supplement.json --output reports/execution-dividend-evidence.json
.venv/bin/python -m market_research.cli smoke --refresh --output reports/execution-smoke.json
.venv/bin/python -m market_research.cli validate
.venv/bin/python scripts/audit_artifacts.py
```

새 설치에서 이번과 같은 의존성 버전이 필요하면 Python3.13 환경에서
`.venv/bin/python -m pip install -r requirements-lock.txt` 후 `pip install -e .`를 실행한다.
lock은 OS용 wheel을 지정하지 않으며 최신 wheel의 macOS 가용성은 별도 확인한다.
Python>=3.11 설치도 허용하지만 동일 버전 lock으로 검증한 환경은3.13이다.

collect/smoke 종료코드: 0=모든 요청 성공/캐시/오프라인 재정규화, 2=일부 접근 실패를
기록하고 나머지 완료. validate:0=구조 검사 통과,1=구조 오류. 데이터 의미·PIT·권한은
별도 판정이다. 전체 collect는 FB/ATVI/Stooq/SEC/틀린 초기 달력 URL의 실패가 기록되어
현재2가 정상적으로 예상된다. 성공 원본을 다시 받지 않고 실패 요청만 재시도한다.
smoke는 AAPL/IBM demo/Nasdaq directory의3요청이며 --refresh가 있어야 실제 네트워크를
다시 확인한다. 단위 테스트는 원본 없이 오프라인으로 실행한다. validate/캐시 audit은 먼저 로컬에서
자료를 확보해야 하며, 확보한 캐시가 있으면 인터넷 없이 실행할 수 있다.

`configs/sample.json`은 검증용6개 보통주(AAPL/MSFT/NVDA/IBM/TSLA/META)와 비교용SPY,
별도 rename/merger probe 및 공식 증거 요청이다. 과거 투자Universe가 아니다.
Yahoo chart는 공식 지원 API 계약 미확인. Alpha Vantage는 공개 IBM demo 예제만 사용했다.
개인 키를 쓰려면 config의 auth_mode를 environment로 바꾸고
ALPHAVANTAGE_API_KEY 환경변수로 전달한다. 키는 config·CLI 인자에 쓰지 않는다.
이 옵션은 이용 권한을 자동 생성하지 않는다.

`data/raw`, `data/normalized`, `data/manifest`는 원본·제공처별 정규화·수신기록이다.
manifest SHA-256이 실제 파일명을 연결한다. `data/derived/alpha_ibm_sample.json`은
전체 IBM 응답에서2024년 및2026년 비교 구간만 선택한 샘플이다. 원본 full response는
그대로 보관했고 모든 과거행의 품질 검사를 완료했다고 주장하지 않는다.
`data/derived/calendar.json`은 library rules 기반 reference, 실제 시장 데이터가 아니다.
tests/fixtures는 합성 테스트 전용이며 data 디렉터리에 섞지 않는다.

timeout20초, 최대3시도, backoff1/2초와 Retry-After, 순차2초 간격(Alpha12초),
Alpha25/UTC일 로컬budget, HTTP401/403/429/404/HTML challenge/empty/schema failure 구분,
원자적 파일 저장, 성공 캐시 재사용, 수정응답 새 hash 버전, 처리/수신 시간 구분을 구현했다.
단일 수집 프로세스를 실행한다. 실패 원문을 지우거나 오류가격을 자동 삭제하지 않는다.

약관이 불명확한 공개 샘플의 장기 보관·모델 학습·재배포 허용을 추정하지 않는다.
샘플은 현재 로컬 검증에만 사용했고 data는 git에서 제외했다. 2단계 전체 구현과
모델 학습에 앞서 [제공처 비교](docs/provider_comparison.md)의 접근·권한·시각 공백을 해결한다.
