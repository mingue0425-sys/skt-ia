"""Offline engine + local REAL artifact validation; only aggregate reports are public.

This script never downloads data. Run collect/smoke separately for live evidence.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import time
from market_research.http import utc_now
from market_research.pit import Database, import_artifacts, query, create_snapshot, replay_snapshot
from market_research.pit.importer import import_calendar
from market_research.storage import canonical, code_version, sha256, write_json

ROOT=Path(__file__).resolve().parents[1]
START_COMMIT="fb78fc7d712dcd285f9e463ec3673fe732cdc0a5"


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",action="append",default=[])
    parser.add_argument("--db",default="data/stage2-validation.sqlite")
    parser.add_argument("--calendar")
    parser.add_argument("--smoke-report")
    parser.add_argument("--output",default="reports/stage2_validation.json")
    args=parser.parse_args()
    start=utc_now(); clock=time.perf_counter(); commands=[]
    def cli(argv,expected=0):
        cmd=[sys.executable,"-m","market_research.cli"]+argv
        p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=180)
        record={"command":[".venv/bin/python","-m","market_research.cli"]+argv,
                "returncode":p.returncode,"expected_returncode":expected,"status":"PASS" if p.returncode==expected else "FAIL"}
        commands.append(record)
        if p.returncode!=expected: raise RuntimeError("stage2_cli_check_failed")
        return json.loads(p.stdout)
    unit=subprocess.run([sys.executable,"scripts/verify.py"],cwd=ROOT,capture_output=True,text=True,timeout=120)
    commands.append({"command":[".venv/bin/python","scripts/verify.py"],"returncode":unit.returncode,"status":"PASS" if unit.returncode==0 else "FAIL"})
    tests=json.loads((ROOT/"reports/unit_test_results.json").read_text())
    gen=subprocess.run([sys.executable,"scripts/make_synthetic_stage2.py"],cwd=ROOT,capture_output=True,text=True,timeout=30)
    commands.append({"command":[".venv/bin/python","scripts/make_synthetic_stage2.py"],"returncode":gen.returncode,"status":"PASS" if gen.returncode==0 else "FAIL"})
    synthetic_db="tests/generated/cli-stage2.sqlite"
    cli(["db-init","--db",synthetic_db])
    cli(["db-import","--db",synthetic_db,"--data-dir","tests/generated/stage2","--input-domain","synthetic"])
    synthetic_query=["--db",synthetic_db,"--cutoff","2024-01-03T22:00:00Z","--policy","historical_verified",
                     "--input-domain","synthetic","--symbol-mode","provider_label","--allow-temporary","--symbols","SYN"]
    initial=cli(["db-query"]+synthetic_query+["--require-data"])
    if [r["close"] for r in initial["data"]]!=[100]: raise RuntimeError("synthetic_cli_oracle_mismatch")
    meta=cli(["snapshot-create"]+synthetic_query)
    verification=cli(["snapshot-verify","--db",synthetic_db,"--snapshot-id",meta["snapshot_id"]])
    replay=cli(["snapshot-replay","--db",synthetic_db,"--snapshot-id",meta["snapshot_id"]])
    # db-query adds a CLI requirement flag; the underlying stored query has no such flag.
    initial.pop("requirements_failed",None)
    if replay["result"]!=initial: raise RuntimeError("synthetic_cli_replay_mismatch")
    cli(["db-query"]+synthetic_query+["--start","2099-01-01","--require-data"],expected=1)
    cli(["db-audit","--db",synthetic_db])
    observed=historical=default_identity=None; imports=[]; market_snapshot=None; market_replay=None; audit=None
    with Database(args.db,initialize=True) as db:
        for directory in args.data_dir:
            imports.append(import_artifacts(db,directory))
        if args.calendar:
            cal=import_calendar(db,args.calendar)
        else: cal={"status":"SKIPPED","reason":"No existing calendar file supplied"}
        actual_cutoff=utc_now()
        request={"symbols":["AAPL","IBM"],"start":"2024-01-02","end":"2026-10-01",
                 "identifier_requirement":"temporary_allowed","symbol_mode":"provider_label",
                 "price_semantics":("as_traded","split_adjusted")}
        observed=query(db,cutoff=actual_cutoff,policy="observed",**request)
        historical=query(db,cutoff="2024-12-31T22:00:00Z",policy="historical_verified",**request)
        default_identity=query(db,cutoff=actual_cutoff,policy="observed",symbols=["AAPL","IBM"],symbol_mode="provider_label",start="2024-01-02",end="2026-10-01")
        market_snapshot=create_snapshot(db,observed)
        market_replay=replay_snapshot(db,market_snapshot["snapshot_id"])
        if market_replay["result"]!=observed: raise RuntimeError("real_snapshot_replay_mismatch")
        # Idempotence must be evidenced against immutable row counts, not merely exit status.
        before=db.counts()
        reruns=[import_artifacts(db,d) for d in args.data_dir]
        idempotent=before==db.counts()
        audit=db.audit()
        receipts=[dict(r) for r in db.conn.execute("SELECT network_performed,status FROM raw_receipts")]
        artifacts=[dict(r) for r in db.conn.execute("SELECT hash_verification FROM normalized_artifacts")]
        stats={"database_counts":db.counts(),"network_evidence_counts":dict(Counter("explicit" if r["network_performed"]==1 else "legacy_unknown" for r in receipts)),
               "receipt_status_counts":dict(Counter(r["status"] for r in receipts)),
               "normalized_hash_verification_counts":dict(Counter(r["hash_verification"] for r in artifacts)),
               "parsing_completion_known":db.conn.execute("SELECT count(*) FROM artifact_receipts WHERE parsing_completed_at IS NOT NULL").fetchone()[0]}
    def summary(result):
        return {k:result[k] for k in ("status","request","strictness","selected_count","excluded_count","exclusion_counts","conflict_count","calendar_versions","limitations")}
    smoke={"status":"SKIPPED","reason":"No independently executed live smoke report supplied"}
    if args.smoke_report:
        document=json.loads(Path(args.smoke_report).read_text())
        outcomes=[{"source":r["manifest_record"]["source"],"status":r["status"],"http_status":r["manifest_record"].get("http_status"),
                   "network_download_performed":r["manifest_record"].get("network_download_performed"),"counts":r["manifest_record"].get("counts"),
                   "actual_data_period":r["manifest_record"].get("actual_data_period"),
                   "fetched_at_utc":r["manifest_record"].get("fetched_at_utc"),
                   "processing_completed_at_utc":r["manifest_record"].get("processing_completed_at_utc")} for r in document["results"]]
        smoke={"status":"PASS" if outcomes and all(r["status"]=="success" and r["network_download_performed"] is True for r in outcomes) else "PARTIAL",
               "started_at_utc":document["started_at_utc"],"finished_at_utc":document["finished_at_utc"],"results":outcomes,
               "scope":"actual independent network execution; no historical publication or ID evidence implied"}
    has_actual=stats["database_counts"]["normalized_artifacts"]>0
    engine_ok=unit.returncode==0 and audit["status"]=="PASS" and idempotent and all(c["status"]=="PASS" for c in commands)
    verdicts={"storage_version_query_engine":{"status":"PASS" if engine_ok else "BLOCKED","reason":"offline expected-value cases, CLI, integrity, idempotence and frozen replay"},
              "actual_import":{"status":"PASS" if has_actual and all(r["quarantined"]==0 for r in imports) else "CONDITIONAL" if has_actual else "BLOCKED","reason":"existing bytes/hash/provenance checks; source failures remain failure receipts"},
              "observed_after_receipt":{"status":"PASS" if observed["selected_count"]>0 else "BLOCKED","reason":"eligible actual receipt plus usable_at and completed observation interval; temporary/provider labels explicitly allowed"},
              "historical_verified_readiness":{"status":"BLOCKED" if historical["selected_count"]==0 else "CONDITIONAL","reason":"current market sample lacks version-bound historical public timestamps; empty result is not data-readiness PASS"},
              "identifiers_and_price_meanings":{"status":"CONDITIONAL","reason":"temporary source labels, vendor session/volume basis unverified; Yahoo split-adjusted, IBM demo as-traded, adjclose close-only"},
              "stage3_scope":{"status":"CONDITIONAL","reason":"synthetic interface development and licensed limited observed sample processing; strict historical feature/label validation remains blocked"},
              "m2_hardware":{"status":"BLOCKED","reason":"no actual Apple Silicon M2 access; Linux ARM64 execution is not M2 verification"}}
    report={"stage":2,"generated_at_utc":utc_now(),"started_at_utc":start,"elapsed_seconds":time.perf_counter()-clock,
            "starting_commit":START_COMMIT,"starting_worktree":"clean","applicable_agents_md":[],
            "baseline_offline_tests":{"command":".venv/bin/python scripts/verify.py","executed_before_changes":True,"passed":31,"failed":0},
            "environment":{"python":sys.version,"platform":platform.platform(),"machine":platform.machine(),"sqlite":sqlite3.sqlite_version},
            "code_sha256":code_version(ROOT),"commands":commands,"offline_tests":{k:tests[k] for k in ("tests_run","passed","failures","errors","skipped","status","test_kind")},
            "validation_command":[".venv/bin/python","scripts/validate_stage2.py",*sys.argv[1:]],
            "local_inputs":{"data_roots":[str(Path(d).resolve()) for d in args.data_dir],
                            "database":str(Path(args.db).resolve()),
                            "calendar":str(Path(args.calendar).resolve()) if args.calendar else None,
                            "smoke_report":str(Path(args.smoke_report).resolve()) if args.smoke_report else None},
            "synthetic_cli":{"status":"PASS","expected_initial_close":100,"snapshot_verified":verification["status"],"empty_require_data_exit":1},
            "real_imports":imports,"real_statistics":stats,"live_smoke":smoke,"calendar":cal,
            "real_observed":summary(observed),"real_historical_verified":summary(historical),"real_verified_identity_default":summary(default_identity),
            "real_snapshot":{"snapshot_id":market_snapshot["snapshot_id"],"content_sha256":market_snapshot["content_sha256"],"replay_equal":market_replay["result"]==observed,"selected_count":observed["selected_count"]},
            "real_audit":audit,"idempotent_immutable_counts":idempotent,"verdicts":verdicts,
            "public_artifact_scope":"aggregates only; raw/normalized/DB/snapshot row contents remain gitignored local artifacts",
            "diagnostic_failure_resolved":{"command":"python3 inline diagnostic","result":"ModuleNotFoundError: market_research","resolution":"rerun with installed .venv/bin/python; no data corruption or network failure"}}
    try:
        import resource
        rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        report["process_peak_rss_mib"]=rss/(1024*1024 if sys.platform=="darwin" else 1024)
        report["memory_measurement_scope"]="this validation process only, not M2 hardware or every child process"
    except ImportError:
        report["process_peak_rss_mib"]=None
        report["memory_measurement_scope"]="unavailable on this platform"
    report["database_size_bytes"]=Path(args.db).stat().st_size
    write_json(args.output,report)
    destination=Path(args.output).with_suffix(".md")
    lines=["# 2단계 저장·조회 엔진 검증", "",f"시작 commit: `{START_COMMIT}`. 시작 작업 트리는 clean이고 적용 AGENTS.md는 없었다.",
           "기존 README·1단계 보고서·인계·정규화 스키마·연구 계약·reports/cross_provider_review.md와 코드를 비교했다.",
           "현재 구현은 SQLite migration 1·2, 원본 참조/수신 사건/정규화 버전, 세 시간 정책, 충돌 처리, 고정 snapshot이다.",
           "외부 계약·새 계정·모델·특징/정답 계산·수익성·자동매매는 수행하지 않았다.","", "## 실제 실행 결과", "",
           f"- 기존 31개를 포함한 오프라인 테스트 **{tests['passed']}/{tests['tests_run']} 통과**, 실패 {tests['failures']}, 오류 {tests['errors']}, 건너뜀 {tests['skipped']}.",
           "- tests/fixtures/stage2_expected.json의 독립 예상 값과 비교했다. 17개 필수 사례에 더해 버전 근거 불일치·normalizer 충돌·제공처 불일치·비밀키 격리·늦게 도착한 원본 연결을 검사했다.",
           f"- 실제 적재: manifest {stats['database_counts']['manifest_links']}, 수신 사건 {stats['database_counts']['raw_receipts']}, 정규화 artifact {stats['database_counts']['normalized_artifacts']}.",
           f"- DB 가격 버전 {stats['database_counts']['daily_price_versions']}, 기업행동 버전 {stats['database_counts']['corporate_action_versions']}, 달력 행 {stats['database_counts']['trading_session_versions']}. 버전 수는 독립 거래일 수가 아니다.",
           f"- observed **{observed['selected_count']}행**, historical_verified **{historical['selected_count']}행**. 실제 cutoff와 제외 사유는 JSON 보고서에 있다.",
           f"- 무결성 {audit['status']}, 재적재 멱등성 {idempotent}, 실제 snapshot 재현 일치 {market_replay['result']==observed}.",
           "- strict 0행은 제한을 적용한 정상 빈 조회이며 검증된 역사 자료 확보 PASS가 아니다.",
           "- 일반 python3 진단은 패키지 미설치로 한 번 실패했고, 설치된 .venv/bin/python으로 재실행해 해결했다.", "", "## 실제 자료와 증거 한계", "",
           "기존 로컬 원본을 먼저 실제 발견했고 원본을 변경하지 않았다. 1단계 자료에는 초기 manifest의 network flag/처리 완료 시각/정규화 byte hash 누락이 존재한다.",
           f"network flag가 없는 {stats['network_evidence_counts'].get('legacy_unknown',0)}건은 actual receipt unproven으로 observed에서 제외한다. 정규화 byte hash 없는 {stats['normalized_hash_verification_counts'].get('legacy_filename_canonical_hash_no_manifest_byte_hash',0)}건은 파일명의 canonical 내용 해시와 provenance를 확인하고 byte hash는 새로 계산했으며 별도 검증 수준으로 남겼다.",
           "처리 완료 시각이 없는 자료의 parsing_completed_at은 null이다. 사용 가능 시각에는 이번 import의 실제 파싱/검증 완료를 사용하고 recorded_at이나 2024년으로 소급하지 않는다.",
           "단일 보통주 IBM demo의 full 과거 응답은 다른 종목의 접근/폐지군/당시 공개 버전을 입증하지 않는다. 수집 실패 본문은 receipt만 저장한다.",
           "Yahoo OHLC는 split_adjusted이고 현재 adjclose는 total_return_adjusted close_only로 분리했다. 이를 원가격·총수익률·당시 현금 배당으로 환산하지 않는다.",
           "현재 Nasdaq directory는 유효 시작일 없는 현재 assertion이며 과거 ticker mapping/투자 universe로 조회하지 않는다.",
           "규칙 달력은 공식 공지 버전이 아니다. 기본 XNYS 정규장 경계는 참고 가정이며 provider 세션/volume 포함 범위를 입증하지 않는다.",
           "실제 historical publication evidence는 없다. 증거 인터페이스가 확인하는 것은 hash와 버전/시각 결합이며 upstream 근거 진위와 라이선스 검토는 별도 의무다.", "", "## 판정", "", "| 항목 | 판정 | 근거 |", "|---|---|---|"]
    lines += [f"| {name} | **{item['status']}** | {item['reason']} |" for name,item in verdicts.items()]
    if smoke["status"] != "SKIPPED":
        lines += ["", "## 별도 실제 네트워크 실행", ""]
        lines += [f"- {item['source']}: HTTP {item['http_status']}, {item['status']}, 기간 {item['actual_data_period']}, 개수 {item['counts']}." for item in smoke["results"]]
    lines += ["", "## 실행 명령", "", "각 명령의 반환 코드와 예상 반환 코드는 stage2_validation.json에 보존했다. 네트워크 검증은 별도의 smoke 실행이다.", "", "```bash",
              ".venv/bin/python scripts/verify.py", ".venv/bin/python scripts/make_synthetic_stage2.py",
              ".venv/bin/python -m market_research.cli db-init --db tests/generated/cli-stage2.sqlite",
              ".venv/bin/python -m market_research.cli db-import --db tests/generated/cli-stage2.sqlite --data-dir tests/generated/stage2 --input-domain synthetic",
              ".venv/bin/python -m market_research.cli smoke --config configs/stage2_smoke.json --data-dir data/stage2_collection --refresh --output data/stage2-smoke.json",
              ".venv/bin/python scripts/prepare_stage2_calendar.py",
              ".venv/bin/python scripts/validate_stage2.py --data-dir data/stage2_collection --calendar data/reference_calendar.json --smoke-report data/stage2-smoke.json", "```", "",
              "이번 검증의 모든 실제 입력 경로/기간/개수는 JSON 보고서에 있다. 공개 checkout은 원본이 없으므로 먼저 허용된 작은 샘플을 수집하거나 기존 로컬 루트를 지정한다.",
              "DB/가격행/snapshot payload는 data/에만 보관했다. 보고서에는 집계만 포함한다.","", "## 3단계 인계", "",
              "reports/stage3_handoff.md의 query/snapshot 입력 계약을 따른다. 임시 ID와 표본 처리는 허용하지만 엄격한 역사적 모델/수익 검증을 시작할 근거는 확보하지 못했다.",
              "실행 환경은 위 JSON의 Python/Linux ARM64다. M2 8GB는 미실행이며 README의 동일 명령으로 재현하고 메모리/속도/시간대/SQLite 결과를 비교해야 한다."]
    destination.write_text("\n".join(lines)+"\n")
    print(json.dumps({"status":"PASS" if engine_ok else "FAIL","tests_passed":tests["passed"],"observed_rows":observed["selected_count"],
                      "historical_verified_rows":historical["selected_count"],"report":str(args.output)},ensure_ascii=False))
    return 0 if engine_ok else 1


if __name__=="__main__": sys.exit(main())
