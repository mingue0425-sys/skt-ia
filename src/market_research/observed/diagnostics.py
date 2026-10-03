"""Row/field blockers across frozen datasets, without multiplying market samples."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from ..datasets.store import dataset_metadata, training_selection, iter_dataset_rows
from ..datasets.calendar import SessionCalendar
from ..pit.time import timestamp
from .scope import SCOPES, learning_reasons
from . import VERSION


def pending_assessment(label, calendar, asof, delay_hours=48):
    asof = timestamp(asof)
    exit_at = label.get('intended_exit_at')
    if not exit_at: return 'calendar_horizon_unknown'
    if exit_at > asof: return 'future_exit_session'
    date = datetime.fromisoformat(exit_at.replace('Z', '+00:00')).date().isoformat()
    row = calendar.by_date.get(date)
    if not row: return 'calendar_horizon_unknown'
    deadline = timestamp((datetime.fromisoformat(row['close_utc'].replace('Z', '+00:00')) + timedelta(hours=delay_hours)).isoformat())
    if asof <= deadline: return 'within_normal_delivery_delay'
    return 'past_exit_requires_refresh_or_missing_outcome_assessment'


CATEGORIES = {
    'publication_version': ('publication', 'historical', 'version_content', 'calendar_not_historically'),
    'observed_timing': ('receipt', 'received', 'usable', 'actual_feature', 'after_cutoff'),
    'price_fields': ('price_semantics', 'opening_prices', 'input_field', 'currency', 'no_computable', 'session_scope', 'scoped_price'),
    'corporate_actions': ('corporate_action',),
    'security_identity_listing': ('security_identity', 'symbol_mapping', 'identifier', 'listing'),
    'tradability': ('trading_status', 'security_status', 'halted', 'trading_state'),
    'future_outcome': ('target_open', 'waiting_for_received'),
    'past_outcome_missing': ('past_endpoint', 'missing_session', 'collection_failure'),
    'rights': ('rights', 'permission'),
    'code_schema_connection': ('scope_contract', 'legacy_strict', 'no_eligible', 'feature_sample_not_ready'),
}


BLOCKER_RULES = {
    'publication_time_unknown': ('pit.query.eligible','historical_verified requires published_at and hash-bound vintage',['temporal.published_at','publication_evidence.version_raw_sha256'],False),
    'actual_receipt_unproven': ('pit.query.eligible','observed requires successful network_performed=1 receipt',['raw_receipts.network_performed','receipt_evidence'],False),
    'received_after_cutoff': ('pit.query.eligible','received_at must be <= feature_cutoff',['historical actual receipt before decision'],False),
    'processing_after_cutoff': ('pit.query.eligible','usable_at must be <= cutoff',['actual processing completion before cutoff'],False),
    'calendar_not_historically_verified': ('pit.query.calendar_bound / datasets.builder.generate_feature','historical policy requires contemporary calendar evidence',['historical_notice_verified','calendar.publication_at'],False),
    'opening_prices_unavailable_close_only': ('datasets.labels.compute_labels','open-to-open target rejects close_only',['as_traded entry.open','as_traded exit.open'],False),
    'price_semantics_not_requested': ('pit.query.query','current adjusted OHLC cannot satisfy as_traded contract',['as_traded OHLC'],False),
    'security_identity_unverified': ('datasets.builder.generate_feature / observed.scope.learning_reasons','temporary identity is not verified security linkage',['security_identity.external_security_id','security_identity.evidence'],False),
    'security_trading_status_unverified': ('datasets.labels.compute_labels','exchange calendar cannot prove individual security tradability',['security_trading_state covering entry/exit'],False),
    'corporate_action_coverage_unconfirmed': ('datasets.labels.compute_labels','price return requires interval-complete action coverage',['corporate_action_coverage','actual split/cash events'],False),
    'target_open_not_yet_reached': ('datasets.labels.compute_labels','exit open exceeds evaluation clock',['future exit session/received endpoint bar'],False),
    'past_endpoint_missing_from_local_source_range': ('observed.diagnostics.diagnose','past endpoint date absent in original local source series',['missing entry/exit dated prices'],False),
    'learning_rights_unconfirmed': ('datasets.builder.build_dataset / observed.scope.learning_reasons','access does not grant model_training_allowed',['hash-bound source/symbol learning_rights'],False),
    'legacy_strict_pit_or_rights_unmet': ('datasets.builder.build_dataset','legacy learning_ready requires historical feature+label policies',['explicit new scoped contract for forward use, plus all evidence'],False),
    'no_eligible_feature_inputs': ('datasets.builder.generate_feature','upstream query selected zero inputs',['see query exclusion reasons'],False),
    'no_computable_price_features': ('datasets.features.compute_features','no selected numeric inputs',['eligible lookback versions'],False),
    'feature_sample_not_ready': ('datasets.store.training_selection','feature status must be ready',['see field/query blockers'],False),
    'feature_not_ready_by_decision': ('observed.scope.learning_reasons','actual feature ready must precede decision',['eligible inputs and actual feature completion'],False),
    'label_not_ready': ('datasets.store.training_selection','only ready labels may train',['see target/field/action/state blockers'],False),
    'label_not_available_at_training_asof': ('observed.scope.learning_reasons','mature label availability must be proven',['label_available_at from all dependencies'],False),
}


def diagnose(pit, store, asof):
    snapshots = [r[0] for r in store.conn.execute('SELECT snapshot_id FROM dataset_snapshots ORDER BY snapshot_id')]
    features, labels, links = {}, {}, defaultdict(set)
    membership_count = 0; snapshot_results = []; pairs = set()
    for sid in snapshots:
        _, items = dataset_metadata(store, sid)
        # Fixed replay verifies original dependencies. It never regenerates old rows.
        for row in iter_dataset_rows(store, pit, sid):
            fid, lid = row['feature_version_id'], row['label_version_id']
            features[fid] = row['feature']; labels[lid] = row['label']; links[(fid, lid)].add(sid)
            pairs.add((row['sample_id'], row['horizon_sessions'])); membership_count += 1
        selected = training_selection(store, pit, sid, training_asof=asof)
        snapshot_results.append({'snapshot_id': sid, 'membership_count': len(items), 'strict_selected_count': selected['selected_count'], 'exclusion_counts': selected['exclusion_counts']})
    details = []; reason_members = defaultdict(set); scope_counts = Counter(); pending = []
    for (fid, lid), sids in links.items():
        f, l = features[fid], labels[lid]
        reasons = set(f.get('sample_reasons', [])) | ({l['reason']} if l.get('reason') else set())
        reasons |= set(learning_reasons(f, l, 'strict_history', asof))
        if f.get('query_exclusion_counts'): reasons |= set(f['query_exclusion_counts'])
        if l.get('query_exclusion_counts'): reasons |= set(l['query_exclusion_counts'])
        # These prerequisites are hidden behind the first-error short circuit.
        if f.get('identifier_verification') != ['verified']: reasons.add('security_identity_unverified')
        if not f.get('calendar', {}).get('historical_notice_verified'): reasons.add('calendar_not_historically_verified')
        import json
        from pathlib import Path
        kinds={json.loads(Path(ref['path']).read_text()).get('kind') for ref in l.get('evidence_dependencies', [])}
        if 'corporate_action_coverage' not in kinds: reasons.add('corporate_action_coverage_unconfirmed')
        for reason in reasons: reason_members[reason].add((f['sample_id'], l['horizon_sessions']))
        for scope in SCOPES:
            if not learning_reasons(f, l, scope, asof): scope_counts[scope] += 1
        if l['status'] == 'pending':
            cal = SessionCalendar(pit, l['calendar']['version'], input_domain=l['input_domain'])
            pending.append({'label_version_id': lid, 'sample_id': f['sample_id'], 'horizon': l['horizon_sessions'], 'frozen_evaluation_at': l['evaluation_at'], 'frozen_reason': l['reason'], 'current_assessment': pending_assessment(l, cal, asof)})
        cal = SessionCalendar(pit,l['calendar']['version'],input_domain=l['input_domain'])
        target_date = next((r['trading_date'] for r in cal.rows if r['open_utc']==l.get('intended_exit_at')),None)
        entry_date = next((r['trading_date'] for r in cal.rows if r['open_utc']==l.get('intended_entry_at')),None)
        endpoints = []
        pc = l['price_contract']
        for name,date in (('entry',entry_date),('exit',target_date)):
            raw = pit.conn.execute("SELECT count(*),sum(open IS NOT NULL),sum(json_extract(temporal_json,'$.published_at') IS NOT NULL),sum(evidence_json != ?),min(session_scope),min(volume_adjustment_basis) FROM daily_price_versions WHERE source=? AND requested_symbol=? AND trading_date=? AND price_kind=? AND price_semantics=?",('{}',pc['source'],pc['symbol'],date,pc['price_kind'],pc['price_semantics'])).fetchone()
            endpoints.append({'endpoint':name,'date':date,'local_version_count':raw[0],'non_null_open_version_count':raw[1] or 0,'publication_timestamp_version_count':raw[2] or 0,'publication_evidence_version_count':raw[3] or 0,'session_scope':raw[4],'volume_adjustment_basis':raw[5]})
        if l.get('intended_exit_at') and l['intended_exit_at']<=timestamp(asof) and any(e['local_version_count']==0 for e in endpoints):
            reasons.add('past_endpoint_missing_from_local_source_range')
            reason_members['past_endpoint_missing_from_local_source_range'].add((f['sample_id'],l['horizon_sessions']))
        details.append({'sample_id': f['sample_id'], 'symbol': f['price_contract']['symbol'], 'decision_at': f['decision_at'], 'horizon': l['horizon_sessions'], 'feature_version_id': fid, 'label_version_id': lid, 'snapshot_ids': sorted(sids), 'feature_status': f['sample_status'], 'label_status': l['status'], 'reasons': sorted(reasons), 'feature_query_exclusion_counts': f.get('query_exclusion_counts', {}), 'label_query_exclusion_counts': l.get('query_exclusion_counts', {}), 'original_endpoint_evidence': endpoints, 'blockers':[{'reason':r,'function':BLOCKER_RULES.get(r,('see row query/payload condition',))[0], 'condition':BLOCKER_RULES.get(r,('',r))[1], 'missing_fields_or_evidence':BLOCKER_RULES.get(r,('','',[]))[2], 'code_can_resolve_alone':BLOCKER_RULES.get(r,('','','',False))[3], 'additional_evidence_required':True,'code_connection_change_applicable':r=='legacy_strict_pit_or_rights_unmet'} for r in sorted(reasons)], 'price_contract': f['price_contract']})
    categories = {category: set().union(*(members for reason, members in reason_members.items() if any(p in reason for p in patterns))) if any(any(p in r for p in patterns) for r in reason_members) else set() for category, patterns in CATEGORIES.items()}
    return {'version': VERSION, 'evaluated_at': timestamp(asof), 'snapshot_count': len(snapshots), 'snapshot_membership_count': membership_count,
            'sample_count': len({f['sample_id'] for f in features.values()}), 'distinct_sample_horizon_count': len(pairs),
            'referenced_feature_version_count': len(features), 'stored_feature_version_count': store.conn.execute('SELECT count(*) FROM feature_versions').fetchone()[0],
            'referenced_label_version_count': len(labels), 'labels_by_horizon': dict(Counter(str(l['horizon_sessions']) for l in labels.values())),
            'feature_version_status_counts': dict(Counter(f['sample_status'] for f in features.values())), 'label_version_status_counts': dict(Counter(l['status'] for l in labels.values())),
            'strict_eligible_distinct_sample_horizon_count': 0 if not scope_counts['strict_history'] else len({(d['sample_id'], d['horizon']) for d in details if not learning_reasons(features[d['feature_version_id']], labels[d['label_version_id']], 'strict_history', asof)}),
            'scope_eligible_version_pairs': {s: scope_counts[s] for s in SCOPES},
            'reason_unique_sample_horizon_counts': {r: len(m) for r, m in sorted(reason_members.items())},
            'category_unique_sample_horizon_counts': {r: len(m) for r, m in categories.items()},
            'category_overlap_counts': {a+' & '+b: len(categories[a] & categories[b]) for i,a in enumerate(categories) for b in list(categories)[i+1:] if categories[a] & categories[b]},
            'pending_versions': pending, 'snapshot_results': snapshot_results, 'rows': details,
            'counting_note': 'Counts of versions/memberships are not independent market samples; multi-reason counts overlap.'}
