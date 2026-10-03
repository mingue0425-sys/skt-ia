"""Fictional sessions, prices and evidence; never downloads market data."""
from pathlib import Path
from market_research.storage import write_json,sha256,canonical
from market_research.validation.quality import calendar_rows
from market_research.pit import import_artifacts
from market_research.pit.importer import import_calendar
from market_research.datasets.calendar import plus_seconds,SessionCalendar
from fixture_builder import append


def assertion(root,kind,**overrides):
    value={"kind":kind,"input_domain":"synthetic","verified":True,"verification_method":"HAND_WRITTEN_SYNTHETIC_TEST_ONLY",
           "source":"synthetic","symbol":"SYN","published_at":"2023-12-01T00:00:00Z","received_at":"2023-12-01T00:01:00Z",
           "usable_at":"2023-12-01T00:02:00Z","version_content_verified":True,
           "start_date":"2024-01-01","end_date":"2024-12-31","status":"verified_complete",
           "same_day_order":"split_then_dividend_postsplit","listed_from":"2020-01-01","verified_through":"2024-12-31",
           "halted_dates":[],"delisted_date":None}
    value.update(overrides)
    path=Path(root)/"assertions"/(kind+'-'+sha256(canonical(value))+'.json')
    write_json(path,value)
    return {"path":str(path.resolve()),"sha256":sha256(path.read_bytes())}


def market_story(db,root,*,n=95,meaning="as_traded",missing=(),late_received=None,close_only=False,actions=None):
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    rows=calendar_rows("2024-01-01","2024-12-31")
    cp=root/'calendar.json'; write_json(cp,rows)
    info=import_calendar(db,cp,domain="synthetic",known_at="2023-12-01T00:00:00Z")
    for i,s in enumerate(rows[:n]):
        if i in missing: continue
        pub=plus_seconds(s["close_utc"],60)
        received=late_received or plus_seconds(s["close_utc"],120)
        usable=late_received or plus_seconds(s["close_utc"],180)
        extra={"observation_end_utc":s["close_utc"],"session_scope":"synthetic_regular_session"}
        if close_only:
            extra.update(open=None,high=None,low=None,volume=None,adjusted_close=100,
                         adjusted_close_semantics="split_and_distribution_adjusted_current_vintage")
        append(root,label='session-'+s['trading_date'],date=s['trading_date'],close=100,published=pub,received=received,usable=usable,
               meaning=meaning,extra=extra,actions=(actions or {}).get(i))
    imported=import_artifacts(db,root,domain="synthetic")
    if imported['quarantined']: raise AssertionError(imported)
    cal=SessionCalendar(db,info['calendar_version'],input_domain="synthetic")
    config={"input_domain":"synthetic","calendar_version":info['calendar_version'],"feature_policy":"historical_verified","label_policy":"historical_verified",
            "identifier_requirement":"temporary_allowed","symbol_mode":"provider_label","as_traded_mode":"price_change_only",
            "series":{"symbol":"SYN","source":"synthetic","price_semantics":meaning,"price_kind":"close_only" if close_only else "ohlcv","currency":"USD"},
            "simulation_asof":"2024-06-01T00:00:00Z","label_asof":"2024-06-01T00:00:00Z",
            "samples":[{"decision_date":rows[65]['trading_date']}],
            "action_coverage":assertion(root,"corporate_action_coverage"),"trading_state":assertion(root,"security_trading_state"),
            "include_dividend_receivables":True,"feature_processing_delay_seconds":0,"label_processing_delay_seconds":0,
            "normal_label_delay_hours":48}
    return cal,config,rows
