"""Explicit scope checks. Legacy strict learning readiness is never rewritten."""
from ..datasets.evidence import load_assertion, assertion_availability
from ..pit.time import timestamp
from . import VERSION

SCOPES = ('strict_history', 'fixed_security_research', 'forward_observed')


def validate_scope(contract, series):
    if not isinstance(contract, dict) or contract.get('version') != VERSION:
        raise ValueError('explicit_scope_contract_5_5_required')
    if contract.get('scope') not in SCOPES:
        raise ValueError('invalid_data_scope')
    if contract.get('scope') != 'strict_history':
        if contract.get('source') != series['source'] or contract.get('symbol') != series['symbol']:
            raise ValueError('scope_single_security_binding_required')
        if contract.get('market_representative') is not False:
            raise ValueError('limited_scope_must_disclaim_market_representation')
    return contract


def learning_reasons(feature, label, scope, asof, *, rights_asof=None):
    """Multi-reason assessment, separate from legacy learning_ready boolean."""
    if scope not in SCOPES: raise ValueError('invalid_data_scope')
    asof = timestamp(asof)
    reasons = []
    if feature.get('sample_status') != 'ready': reasons.append('feature_sample_not_ready')
    ready, decision, entry = (feature.get(k) for k in ('feature_ready_at', 'decision_at', 'intended_entry_at'))
    if not all((ready, decision, entry)) or not ready <= decision < entry:
        reasons.append('feature_not_ready_by_decision')
    if (decision and decision > asof) or (ready and ready > asof): reasons.append('feature_after_training_asof')
    if label.get('status') != 'ready': reasons.append('label_not_ready')
    if not label.get('label_available_at') or label['label_available_at'] > asof: reasons.append('label_not_available_at_training_asof')
    if feature.get('input_domain') != 'market' or label.get('input_domain') != 'market': reasons.append('synthetic_not_market_training')
    if scope == 'strict_history':
        if not label.get('learning_ready'): reasons.append('legacy_strict_pit_or_rights_unmet')
    else:
        contract = feature.get('scope_contract')
        try:
            validate_scope(contract, feature['price_contract'])
            if contract['scope'] != scope or label.get('scope_contract') != contract: raise ValueError('scope_mismatch')
        except (ValueError, KeyError, TypeError): reasons.append('explicit_matching_scope_contract_missing')
        if feature.get('identifier_verification') != ['verified']: reasons.append('security_identity_unverified')
        if not feature.get('scoped_input_eligible'): reasons.append('scoped_price_session_fields_unverified')
        if scope == 'forward_observed':
            if feature.get('query_policy') != 'observed' or label.get('query_policy') != 'observed': reasons.append('forward_scope_requires_observed_receipts')
            if feature.get('feature_ready_basis') != 'actual_operational_record': reasons.append('actual_feature_processing_record_required')
        # Corporate actions and individual trading-state checks are in compute_labels;
        # no complete-market delisted universe is required for these fixed-symbol scopes.
    refs = label.get('evidence_dependencies', [])
    rights = []
    for ref in refs:
        try:
            import json
            from pathlib import Path
            if json.loads(Path(ref['path']).read_text()).get('kind') == 'learning_rights':
                rights.append(load_assertion(ref, kind='learning_rights', domain='market'))
        except (OSError, ValueError, KeyError): reasons.append('learning_rights_evidence_invalid')
    if not rights: reasons.append('learning_rights_unconfirmed')
    for right in rights:
        # An offline research license is checked now, not backdated to the logical fold.
        rights_cutoff = timestamp(rights_asof) if scope == 'fixed_security_research' and rights_asof else asof
        _, reason = assertion_availability(right, policy='observed' if scope in ('forward_observed', 'fixed_security_research') else label.get('query_policy'), cutoff=rights_cutoff)
        if reason: reasons.append('learning_rights_' + reason)
        if right.get('model_training_allowed') is not True: reasons.append('model_training_permission_missing')
        if scope != 'strict_history' and (right.get('source') != feature['price_contract']['source'] or right.get('symbol') != feature['price_contract']['symbol']):
            reasons.append('learning_rights_series_binding_missing')
    return sorted(set(reasons))
