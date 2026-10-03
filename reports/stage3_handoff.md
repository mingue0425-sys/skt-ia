# 3단계 특징·정답 생성기 인계

범위: 2단계에서 저장·조회·snapshot 인터페이스와 검증을 완료했다. 특징·정답 생성,
모델·수익성·자동매매는 아직 구현하지 않았다. 엄격한 역사 데이터 준비는 **BLOCKED**다.
최신 실제 수치/명령/환경은 stage2_validation.json·md를 기준으로 한다.

## 다음 단계의 입력 인터페이스

```python
from market_research.pit import Database, query, create_snapshot, replay_snapshot

with Database("data/stage2.sqlite") as db:
    result = query(
        db, cutoff="2026-10-02T12:00:00Z", policy="observed",
        symbols=["IBM"], sources=["alpha_vantage"],
        start="2026-05-01", end="2026-10-01",
        identifier_requirement="temporary_allowed", symbol_mode="provider_label",
        price_semantics=("as_traded",), input_domain="market",
    )
    # 이 예시는 임시 표본 처리용이다. 실제 cutoff는 실행한 수신/usable_at 이후로 지정한다.
    assert result["selected_count"] > 0
    assert result["conflict_count"] == 0
    meta = create_snapshot(db, result)
    fixed = replay_snapshot(db, meta["snapshot_id"], verify_files=True)
    input_rows = fixed["result"]["data"]
```

운영/엄격 검증용 기본 호출은 identifier_requirement=verified, symbol_mode=effective_mapping,
price_semantics=(as_traded,), policy를 명시한다. 현재 실제 자료로는 이러한 엄격한 호출이 비어
있을 수 있다. API가 assumed로 자동 fallback하지 않는다.

특징 생성 입력은 **snapshot_id + content_sha256 + result.request + 선택 version IDs**다.
각 행의 provenance(raw/normalized/receipt/normalizer/code/schema), availability, validation,
observation_bound(calendar version), symbol_mapping을 함께 보존한다. source와 가격 의미를
합치지 않는다. price_kind=close_only인 Yahoo adjclose는 OHLC/V 입력으로 쓰지 않는다.
query를 다시 실행하는 것과 저장된 snapshot의 고정 재현은 별도 명령이다.

생성기는 EMPTY/CONFLICT/requirement failure와 NOT_STRICT_PIT를 먼저 검사한다.
엄격 검증에는 temporary ID·provider label·unknown semantics·unverified volume/session·
reference-only historical calendar·부족한 historical content evidence를 허용하지 않는다.
상충 제공처를 사용하려면 공식 정의를 조사한 명시 정책을 별도 버전으로 구현해야 한다.
query의 excluded_versions/conflicts는 진단 메타데이터이며 모델 특성이 아니다.

## 실제 자료와 합성 자료

- 실제 원본: 최초 발견한 `/home/sechi/market-data-stage1/data`와 이번 작은 재수집의
  `data/stage2_collection`. 원본을 수정하지 않았다. DB는 gitignored `data/` 아래에 있다.
- 실제 DB: `data/stage2-validation.sqlite`(최종 검증), `data/stage2.sqlite`(초기 CLI 검증).
  실제 고정 snapshot ID/hash는 공개 집계 보고서에 있으며 payload/가격행은 DB에만 있다.
- 합성: `tests/fixtures/stage2_expected.json`, `tests/fixture_builder.py`, `tests/test_pit.py`.
  CLI 합성 산출물은 `tests/generated/`이며 market domain과 다른 파일/DB로 생성한다.
- 모든 실제 price/security ID는 임시 source label이다. 현재 Nasdaq assertion은 상장 당시
  유효 매핑/보통주 분류/역사 투자 대상 목록이 아니다.

Yahoo OHLC는 split_adjusted이고 adjclose는 현재 분할·배당 조정 close_only다.
Alpha IBM demo OHLCV만 as_traded 의미를 확인했으며 다른 증권 full 접근은 미검증이다.
기업행동의 날짜들은 별도이고 provider_reported_split_basis_unverified 배당액을 당시 현금
지급으로 취급하지 않는다. raw 복원·총수익 계산을 구현하지 않았다.

## 시간과 근거

수신 사건 ID는 내용 hash와 다르다. 동일 bytes 재수신과 offline reparse가 구별된다.
새 collector 완료 시각은 명시적이다. 초기 미기록 값은 null이고 이번 import 검증 완료를
actual usable_at으로 기록한다. 2026년 수신/처리를 2024년으로 소급하지 않는다.
historical_verified는 source publication과 **그 내용 버전**의 증거를 요구한다.
실제 샘플에는 이 근거가 없어 조회 0행이며 synthetic의 성공을 실제 역사자료 PASS로 해석하지 않는다.
규칙 달력과 공식 notice version은 다르다. 17:00 혹은 정규장 마감+60분은 예측 가정이며
자료 제공 시각의 증거가 아니다. 가격 구간 완료 경계도 provider 세션 범위가 검증돼야 확정된다.

## 허용 범위와 먼저 해결할 조건

1. **허용**: 합성 자료로 특징/정답 생성기의 타입·시점/누수·snapshot 입력 계약을 개발한다.
   권한이 확인된 제한된 실제 sample의 데이터 처리 연구는 별도 CONDITIONAL 범위다.
2. **차단**: 현재 샘플을 생존편향 없는 10–15년 역사 종목군이나 확률예측/수익률 검증으로 사용하지 않는다.
3. 저장·학습·재배포 권한, 100종목 raw OHLCV 및 폐지 가격, 영구 증권 ID/코드 유효 이력,
   실제 보통주 분류와 당시 알려진 universe, volume/session 정의를 먼저 확보한다.
4. 최초 public/revision **내용 버전**과 정확한 release/available/received 근거를 확보한다.
   현재 수정 응답에 옛 날짜를 붙이는 방식은 허용하지 않는다.
5. 당시 기업행동 현금액·분할 비율·합병 대가, 검증된 달력 notice/거래정지/상장 경계,
   cutoff 내 실시간 제공 가능성을 확인한다. 불완전한 actions로 총수익 라벨을 강행하지 않는다.
6. 다음 시가 및 1·5·20거래일 정답은 증권/세션/기업행동의 검증된 의미에 연결한다.
   특성 조회 cutoff와 사후 평가용 outcome 조회는 구별하고 미래 정정을 과거 feature에 소급하지 않는다.
7. 오래된 source-code archive 부족과 원본 파일 경로 이관을 해결한 뒤 전량 수집 규모를 늘린다.

## 재현

README의 오프라인 CLI 예제와 실제 smoke→calendar→validate_stage2 명령을 실행한다.
public clone에는 시장 원본/DB가 없다. source 제한으로 collect가 2를 반환하면 성공 자료와
실패 이유를 함께 평가한다. 없는 파일을 있다고 가정하지 않는다.
M2 실기기에서는 동일 Python 버전/의존성 설치, 전체 offline suite, zoneinfo/DST/조기폐장,
FK/migration/snapshot hash, 샘플 import/peak memory/소요시간을 확인한다.
이번 실행은 Linux ARM64이며 M2 실기기 PASS를 주장하지 않는다.
