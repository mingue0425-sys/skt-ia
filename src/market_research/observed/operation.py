"""One bounded, restartable observed cycle reusing collector/PIT/Stage3 selection.

Single Linux process enforced with flock. A completed same-cutoff execution is
replayed, not collected again; a later cutoff can append corrections/labels.
"""
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import fcntl
import json
import sqlite3
from ..collect import collect_batch
from ..http import HTTPClient, utc_now
from ..storage import Store, canonical, sha256, write_json, code_version
from ..pit import Database, query, create_snapshot, import_artifacts
from ..pit.importer import import_calendar
from ..pit.time import timestamp, day
from ..datasets.calendar import SessionCalendar
from ..datasets.builder import generate_feature, generate_labels
from ..datasets.features import compute_features
from ..datasets.evidence import load_assertion, assertion_availability
from ..datasets.store import DatasetStore, training_selection, replay_dataset
from ..validation.quality import calendar_rows
from .scope import validate_scope, learning_reasons
from . import VERSION

MINIMUM = {'samples': 100, 'date_span_days': 180, 'train': 60, 'validation': 20, 'test': 20}


def check_config(c, now):
    if c.get('version') != VERSION: raise ValueError('observed_config_version_required')
    if c.get('input_domain', 'market') != 'market': raise ValueError('observed_operation_requires_market_domain')
    validate_scope(c['scope_contract'], c['series'])
    if c['scope_contract']['scope'] != 'forward_observed': raise ValueError('operation_requires_forward_observed_scope')
    if c.get('timezone') != 'America/New_York' or c.get('exchange') != 'XNYS': raise ValueError('explicit_supported_timezone_calendar_required')
    from importlib.metadata import version
    if c.get('calendar_library_version') != version('exchange_calendars'): raise ValueError('pinned_calendar_library_version_mismatch')
    if c.get('decision_policy') != 'regular_close_plus_60_minutes': raise ValueError('fixed_decision_policy_required')
    if c.get('cutoff_policy') not in ('explicit', 'after_processing'): raise ValueError('explicit_cutoff_policy_required')
    if c['cutoff_policy'] == 'explicit' and not c.get('cutoff'): raise ValueError('explicit_cutoff_required')
    if c.get('horizons') != [1, 5, 20]: raise ValueError('fixed_label_horizons_required')
    if not c.get('required_features') or c.get('required_fields') != ['open','high','low','close','volume']: raise ValueError('fixed_fields_and_features_required')
    from ..training.pipeline import check_features
    check_features(c['required_features'])
    if c['series'].get('price_kind') != 'ohlcv' or c['series'].get('price_semantics') != 'as_traded' or c['series'].get('currency') != 'USD': raise ValueError('observed_raw_ohlcv_usd_contract_required')
    if c.get('minimum_training') != MINIMUM: raise ValueError('predeclared_training_minimum_must_not_be_lowered')
    if not isinstance(c.get('freshness_sessions'), int) or isinstance(c['freshness_sessions'], bool) or not 0 <= c['freshness_sessions'] <= 2: raise ValueError('bounded_freshness_required')
    limits = c['http']
    if not 1 <= limits['max_attempts'] <= 3 or not 1 <= limits['timeout_seconds'] <= 20 or limits.get('alpha_spacing_seconds') != 12 or limits.get('local_daily_budget') != 25: raise ValueError('bounded_http_policy_required')
    spec = c['request']
    if spec.get('source') != 'alpha_vantage' or spec.get('kind') != 'daily' or spec.get('symbol') != c['series']['symbol'] or c['series']['source'] != spec['source']: raise ValueError('single_supported_source_security_required')
    demo = c.get('access_scope') == 'official_public_ibm_demo_diagnostic' and spec.get('auth_mode') == 'demo' and spec.get('symbol') == 'IBM' and spec.get('outputsize') == 'compact'
    if not demo:
        rights = load_assertion(c.get('collection_rights'), kind='data_usage', domain='market')
        _, reason = assertion_availability(rights, policy='observed', cutoff=now)
        if reason or not rights or rights.get('automated_collection_allowed') is not True or rights.get('local_storage_allowed') is not True or rights.get('source') != spec['source'] or rights.get('symbol') != spec['symbol']:
            raise ValueError('collection_and_storage_permission_unconfirmed')
    return {'collection_permission': 'official_demo_diagnostic_only' if demo else 'verified_scope_assertion', 'model_training_permission': 'separately_checked_not_granted_by_access'}


def freeze_sample(store, feature_id, feature, labels, config, *, completed_at=None):
    items = []; rights = load_assertion(config.get('learning_rights'), kind='learning_rights', domain='market')
    rights_at, rights_reason = assertion_availability(rights, policy='observed', cutoff=config['label_asof'])
    for index, label in enumerate(labels):
        preserve = label.pop("_preserve_version",False)
        if not preserve:
            label = dict(label)
        if not preserve and completed_at:
            label = label | {'label_materialized_at':completed_at,'label_processing_time_basis':'actual_operational_record'}
            if label.get('status')=='ready': label['label_available_at']=max(label['label_available_at'],completed_at)
        if not preserve: label.update(sample_id=feature['sample_id'], learning_ready=False,
                     learning_rights_status='verified_assertion' if rights else 'unconfirmed',
                     learning_rights_available_at=rights_at, learning_rights_reason=rights_reason,
                     scope_contract=config['scope_contract'])
        if rights and not preserve: label.setdefault('evidence_dependencies', []).append(rights['evidence'])
        if not preserve: label['scoped_learning_reasons'] = learning_reasons(feature, label, 'forward_observed', completed_at or config['label_asof'])
        if not preserve: label['scoped_learning_ready'] = not label['scoped_learning_reasons']
        labels[index] = label
        lid = store.add_label(label)
        items.append({'sample_id':feature['sample_id'],'feature_version_id':feature_id,'label_version_id':lid,'horizon_sessions':label['horizon_sessions']})
    statuses = Counter(l['status'] for l in labels)
    return store.freeze(items, {'dataset_definition_version':'3.0.1','config':config,'operation_contract_version':VERSION,
        'ready_pair_count':sum(feature['sample_status']=='ready' and l['status']=='ready' for l in labels),
        'learning_ready_pair_count':0,'scoped_learning_ready_pair_count':sum(l['scoped_learning_ready'] for l in labels),
        'feature_status_counts':{feature['sample_status']:1},'label_status_counts':dict(statuses),
        'reason_counts':dict(Counter(l['reason'] for l in labels if l['reason']))})


def readiness(pit, store, snapshots, asof):
    selected = {}; counts = Counter(); strict_count = 0
    for sid in snapshots:
        report = training_selection(store,pit,sid,training_asof=asof,scope='forward_observed')
        counts.update(report['exclusion_counts'])
        strict_count += training_selection(store,pit,sid,training_asof=asof)['selected_count']
        for row in report['selected']:
            selected[(row['sample_id'],row['horizon_sessions'])] = row
    h5 = [r for r in selected.values() if r['horizon_sessions']==5]
    dates = sorted({store.payload('feature',r['feature_version_id'])['decision_at'][:10] for r in h5})
    span = (datetime.fromisoformat(dates[-1])-datetime.fromisoformat(dates[0])).days if dates else 0
    reasons = []
    if len(h5) < MINIMUM['samples']: reasons.append('minimum_100_mature_5_session_samples_not_met')
    if span < MINIMUM['date_span_days']: reasons.append('minimum_180_calendar_day_span_not_met')
    # No automatic training: class composition and purged splits must be checked by
    # the Stage5 runner on a separately frozen approved research run config.
    return {'status':'BLOCKED' if reasons or not h5 else 'REQUIRES_STAGE5_SPLIT_CLASS_AND_PERMISSION_REVIEW',
        'scoped_eligible_pairs':len(selected),'scoped_eligible_5_session_samples':len(h5),'legacy_strict_eligible_memberships':strict_count,
        'date_span_days':span,'minimum_training':MINIMUM,'minimum_reasons':reasons,'exclusion_counts':dict(counts),'trained':False}


def cycle(config, root, project_root='.', *, refresh=False, client=None, clock=utc_now, fail_after=None):
    root = Path(root).resolve(); root.mkdir(parents=True,exist_ok=True)
    with (root/'operation.lock').open('a+') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('another_observed_operation_is_running') from None
        return _cycle(config,root,project_root,refresh=refresh,client=client,clock=clock,fail_after=fail_after)


def _cycle(c, root, project_root, *, refresh, client, clock, fail_after):
    started = timestamp(clock()); permissions = check_config(c,started)
    local_date = day(c.get('decision_date') or datetime.fromisoformat(started.replace('Z','+00:00')).astimezone(ZoneInfo(c['timezone'])).date().isoformat())
    run_id = sha256(canonical({'config':c,'decision_date':local_date,'requested_cutoff':c.get('cutoff')}))
    journal_path = root/'executions'/f'{run_id}.json'
    journal = json.loads(journal_path.read_text()) if journal_path.exists() else {'version':VERSION,'run_id':run_id,'started_at':started,'config_sha256':sha256(canonical(c)),'steps':{}}
    if journal.get('completed'):
        if journal.get('result_sha256') != sha256(canonical(journal['result'])): raise ValueError('operation_result_hash_mismatch')
        with Database(root/'pit.sqlite') as pit, DatasetStore(root/'datasets.sqlite') as store:
            for sid in journal['result'].get('dataset_snapshot_ids',[]): replay_dataset(store,pit,sid)
            for sid in journal['result'].get('query_snapshot_ids',[]):
                from ..pit import replay_snapshot
                replay_snapshot(pit,sid)
        return journal['result'] | {'idempotent_replay':True,'network_download_this_execution':False}
    def checkpoint(name, value):
        journal['steps'][name] = value; write_json(journal_path,journal)
        if fail_after == name: raise RuntimeError('injected_failure_after_'+name)
    try:
        if 'collection' not in journal['steps']:
            http = c['http']; collection_store = Store(root/'collection')
            client = client or HTTPClient(timeout=http['timeout_seconds'],max_attempts=http['max_attempts'],budget=collection_store.daily_budget)
            collected = collect_batch([c['request']],root/'collection',project_root,canonical(c),refresh=refresh,client=client)
            checkpoint('collection',collected)
        collected = journal['steps']['collection']; m = collected[0]['manifest_record']
        if m.get('status') != 'success':
            checkpoint('failed_collection',{'category':m.get('status')})
            # Failed attempt stays in immutable manifest; next retry redoes only collection.
            journal['steps'].pop('collection',None);write_json(journal_path,journal)
            return {'status':'COLLECTION_FAILED','category':m.get('status'),'run_id':run_id,'prediction_status':'prediction_not_available','completed':False}
        from .review import apply_review
        if c.get('price_contract_evidence') and 'contract_review' not in journal['steps']:
            checkpoint('contract_review',apply_review(root/'collection',m,c['price_contract_evidence'],clock()))
        with Database(root/'pit.sqlite',initialize=not (root/'pit.sqlite').exists()) as pit, DatasetStore(root/'datasets.sqlite') as store:
            imported = import_artifacts(pit,root/'collection',domain='market')
            checkpoint('import',imported)
            if imported.get('quarantined'): raise ValueError('normalized_artifact_quarantined')
            calendar_path = Path(c['calendar_path']) if c.get('calendar_path') else root/'calendar.json'
            if not calendar_path.exists(): write_json(calendar_path,calendar_rows(c['calendar_start'],c['calendar_end']))
            calendar_info = import_calendar(pit,calendar_path,domain='market')
            checkpoint('calendar',calendar_info)
            cal = SessionCalendar(pit,calendar_info['calendar_version'])
            if c.get('calendar_version') and c['calendar_version']!=cal.version: raise ValueError('pinned_calendar_version_mismatch')
            cutoff = timestamp(c['cutoff']) if c['cutoff_policy']=='explicit' else journal['steps'].get('cutoff',timestamp(clock()))
            if cutoff > timestamp(clock()): raise ValueError('future_cutoff_not_observed')
            checkpoint('cutoff',cutoff)
            closed_dates = [r['trading_date'] for r in cal.rows if r['close_utc'] <= cutoff]
            if not closed_dates: raise ValueError('no_closed_session_before_cutoff')
            anchor = closed_dates[-1]
            series = c['series']
            result = query(pit,cutoff=cutoff,policy='observed',symbols=[series['symbol']],sources=[series['source']],
                start=cal.window(anchor,61)[0],end=anchor,identifier_requirement='verified' if c.get('price_contract_evidence') else 'temporary_allowed',symbol_mode='provider_label',calendar_version=cal.version,price_semantics=('as_traded',))
            qs = create_snapshot(pit,result)
            diagnostic = compute_features(result,cal,anchor_date=anchor,price_kind='ohlcv',as_traded_mode='price_change_only',expected_semantics='as_traded',expected_currency=series['currency'])
            diagnostic.pop('selected_inputs')
            diagnostic.update(scope='CURRENT_LOOKBACK_DIAGNOSTIC_NOT_A_TRADING_SAMPLE',anchor_date=anchor,cutoff=cutoff,snapshot_id=qs['snapshot_id'],query_exclusion_counts=result['exclusion_counts'],input_rows=result['selected_count'],publication_required=False,training_ready=False)
            checkpoint('lookback_diagnostic',diagnostic)
            status = 'NON_TRADING_DAY' if local_date not in cal.index else 'ASSESSING'
            snapshots=[]; sample_result=None
            label_config = {k:c[k] for k in ('series','scope_contract','required_features')}
            label_config.update(input_domain='market',calendar_version=cal.version,feature_policy='observed',label_policy='observed',
                identifier_requirement='verified' if c.get('price_contract_evidence') else c.get('identifier_requirement','temporary_allowed'),symbol_mode='provider_label',as_traded_mode='price_change_only',
                label_asof=timestamp(clock()),simulation_asof=timestamp(clock()),normal_label_delay_hours=48,
                **{k:c[k] for k in ('action_coverage','trading_state','learning_rights','price_contract_evidence') if c.get(k)})
            if status=='ASSESSING':
                plan=cal.plan(local_date); now=timestamp(clock())
                if cutoff > plan['decision_at'] or now > plan['decision_at']: status='MISSED_DECISION'
                elif cutoff < cal.by_date[local_date]['close_utc']: status='SESSION_NOT_CLOSED'
                elif not result['data']: status='NO_ELIGIBLE_INPUTS'
                elif any(r.get(field) is None for r in result['data'] for field in c['required_fields']): status='REQUIRED_FIELDS_MISSING'
                elif not diagnostic['last_observation_date'] or cal.elapsed(diagnostic['last_observation_date'],local_date)>c['freshness_sessions']: status='STALE_DATA'
                else:
                    spec={'decision_date':local_date,'feature_cutoff':cutoff,'feature_ready_at':cutoff,'feature_ready_basis':'actual_operational_record'}
                    feature=generate_feature(pit,cal,spec,label_config)
                    feature['feature_ready_at']=timestamp(clock())
                    if feature['feature_ready_at'] > plan['decision_at']: status='MISSED_DECISION'
                    elif feature['sample_status']!='ready': status='FEATURE_CONTRACT_UNMET'
                    else:
                        feature['operational_log']={'run_id':run_id,'actual_completed_at':feature['feature_ready_at']}
                        fid=store.add_feature(feature)
                        sample_path=root/'samples'/f'{feature["sample_id"]}.json'
                        # Same logical sample with a later config/correction must not rewrite
                        # already used inputs. Later source revisions stay in PIT only.
                        if sample_path.exists():
                            saved=json.loads(sample_path.read_text());fid=saved['feature_version_id'];feature=store.payload('feature',fid)
                        else:
                            write_json(sample_path,{'feature_version_id':fid,'sample':spec,'config':label_config})
                        labels=generate_labels(pit,cal,spec,label_config)
                        frozen=freeze_sample(store,fid,feature,labels,label_config,completed_at=timestamp(clock())); snapshots.append(frozen['snapshot_id'])
                        sample_result={'sample_id':feature['sample_id'],'feature_version_id':fid,'feature_status':feature['sample_status'],'scoped_input_eligible':feature.get('scoped_input_eligible',False),'feature_ready_at':feature['feature_ready_at'],'scheduled_decision_at':plan['decision_at'],'label_status_counts':dict(Counter(l['status'] for l in labels)),'dataset_snapshot_id':frozen['snapshot_id']}
                        status='SAMPLE_PREPARED'
            checkpoint('current_sample',{'status':status,'sample':sample_result})
            updates=[]
            # Only past samples whose exit session has occurred are refreshed. Old IDs
            # remain frozen; future horizons retain their original pending versions.
            for path in sorted((root/'samples').glob('*.json')):
                saved=json.loads(path.read_text()); f=store.payload('feature',saved['feature_version_id'])
                if sample_result and f['sample_id']==sample_result['sample_id']: continue
                cfg=saved['config']|{k:c[k] for k in ('action_coverage','trading_state','learning_rights','price_contract_evidence') if c.get(k)}
                cfg.update(label_asof=label_config['label_asof'],simulation_asof=label_config['simulation_asof'])
                plan=cal.plan(saved['sample']['decision_date'])
                if not any(r and r['open_utc']<=cfg['label_asof'] for r in plan['exits'].values()): continue
                new_labels=generate_labels(pit,cal,saved['sample'],cfg)
                # Freeze combines updated mature horizons with unchanged future versions.
                old_rows=list(store.conn.execute('SELECT payload_json FROM label_versions WHERE sample_id=? ORDER BY created_at DESC,label_version_id DESC',(f['sample_id'],)))
                old_by_h={}
                for row in old_rows:
                    old=json.loads(row[0]);old_by_h.setdefault(old['horizon_sessions'],old)
                chosen=[l if l['intended_exit_at'] and l['intended_exit_at']<=cfg['label_asof'] else (old_by_h[l['horizon_sessions']] | {'_preserve_version':True} if l['horizon_sessions'] in old_by_h else l) for l in new_labels]
                frozen=freeze_sample(store,saved['feature_version_id'],f,chosen,cfg,completed_at=timestamp(clock())); snapshots.append(frozen['snapshot_id'])
                updates.append({'sample_id':f['sample_id'],'snapshot_id':frozen['snapshot_id'],'status_counts':dict(Counter(l['status'] for l in chosen))})
            checkpoint('mature_label_updates',updates)
            # Latest memberships per stored sample, then union by sample/horizon.
            # All snapshots may contain older ready labels; selector enforces asof.
            latest_by_sample = {}
            for row in store.conn.execute('SELECT snapshot_id FROM dataset_snapshots ORDER BY created_at DESC,snapshot_id DESC'):
                sample_ids = [v[0] for v in store.conn.execute('SELECT DISTINCT sample_id FROM dataset_items WHERE snapshot_id=?',(row[0],))]
                for sid in sample_ids: latest_by_sample.setdefault(sid,row[0])
            all_snapshots=sorted(set(latest_by_sample.values()))
            ready=readiness(pit,store,all_snapshots,timestamp(clock()))
            report={'version':VERSION,'run_id':run_id,'status':status,'started_at':started,'finished_at':timestamp(clock()),
                'permissions':permissions,'cutoff':cutoff,'decision_date':local_date,'calendar':cal.metadata,
                'collection':{'status':collected[0]['status'],'http_status':m.get('http_status'),'network_download_performed':m.get('network_download_performed'),'receipt_id':m.get('receipt_id',m.get('origin_receipt_id')),'received_at':m['fetched_at_utc'],'processing_completed_at':m.get('processing_completed_at_utc'),'raw_sha256':m['sha256'],'rows':m['row_count'],'actual_data_period':m.get('actual_data_period')},
                'lookback_diagnostic':diagnostic,'sample':sample_result,'mature_label_updates':updates,'dataset_snapshot_ids':snapshots,'query_snapshot_ids':[qs['snapshot_id']],
                'training_readiness':ready,'prediction_status':'prediction_not_available','real_predictive_power':'NOT_EVALUATED',
                'models_trained':0,'orders_enabled':False,'schedules_enabled':False,'code_sha256':code_version(project_root),'idempotent_replay':False}
            journal.update(completed=True,result=report,result_sha256=sha256(canonical(report)));write_json(journal_path,journal)
            write_json(root/'latest.json',report)
            return report
    except (ValueError,OSError,RuntimeError,sqlite3.Error) as exc:
        journal.update(last_failure={'at':timestamp(clock()),'category':str(exc) if isinstance(exc,ValueError) else type(exc).__name__})
        write_json(journal_path,journal)
        raise
