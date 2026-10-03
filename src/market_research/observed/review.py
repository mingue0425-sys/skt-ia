"""Apply an explicit reviewed contract to a NEW normalization version.

This verifies scope/hash/time, not the reviewer/provider's authenticity. No default
assertion is generated and original raw/normalized files are never edited.
"""
import copy
import json
import re
from pathlib import Path
from ..storage import sha256, canonical, write_json, Store
from ..datasets.evidence import load_assertion, assertion_availability
from ..http import utc_now
from ..pit.time import timestamp


def apply_review(collection_root, record, reference, asof):
    if reference is None: return None
    root = Path(collection_root)
    review = load_assertion(reference, kind='price_contract_review', domain='market')
    _, reason = assertion_availability(review, policy='observed', cutoff=timestamp(asof))
    if reason: raise ValueError('price_contract_review_'+reason)
    spec = record['request_parameters']
    if review.get('source')!=spec['source'] or review.get('symbol')!=spec['symbol']: raise ValueError('price_review_series_mismatch')
    if review.get('price_semantics')!='as_traded' or review.get('currency')!='USD' or review.get('session_scope')!='verified_regular_session' or review.get('volume_unit')!='shares' or review.get('volume_adjustment_basis')!='as_traded': raise ValueError('price_review_semantics_incomplete')
    identity=review.get('security_identity',{})
    if not identity.get('external_security_id') or not identity.get('evidence') or identity.get('verification')!='verified': raise ValueError('reviewed_permanent_security_identity_required')
    if re.match(r'(?i)^(temporary:|cik[:\s])',identity['external_security_id']): raise ValueError('temporary_or_issuer_id_not_security_id')
    start,end=review.get('start_date'),review.get('end_date')
    if not start or not end or start>end: raise ValueError('price_review_covered_interval_required')
    body=(root/record['normalized_path']).read_bytes()
    if sha256(body)!=record['normalized_sha256']: raise ValueError('review_original_normalization_hash_mismatch')
    out=copy.deepcopy(json.loads(body));rows=out['bars']
    # Review binds the actual downloaded vintage; no broad source-wide promotion.
    if record['sha256'] not in review.get('version_raw_sha256s',[]): raise ValueError('review_downloaded_version_not_covered')
    rows=[r for r in rows if start<=r['trading_date']<=end]
    if not rows: raise ValueError('review_has_no_covered_prices')
    for row in rows:
        row.update(session_scope=review['session_scope'],volume_unit=review['volume_unit'],volume_adjustment_basis=review['volume_adjustment_basis'],
                   security_identity=identity,local_instrument_id='reviewed:'+identity['external_security_id'])
        row['contract_review_evidence']=review['evidence']
    out['bars']=rows
    out['provenance']['contract_review_evidence']=review['evidence']
    digest=sha256(canonical(out));path=Path('normalized')/spec['source']/record['request_id']/(digest+'.json')
    write_json(root/path,out)
    new=record|{'network_download_performed':False,'origin_receipt_id':record.get('receipt_id',record.get('origin_receipt_id')),
        'normalized_path':str(path),'normalized_sha256':sha256((root/path).read_bytes()),'recorded_at_utc':utc_now(),
        'processing_completed_at_utc':utc_now(),'contract_review_evidence':review['evidence'],'row_count':len(rows),
        'processing_validation':'reviewed_contract_overlay_5_5_0',
        'actual_data_period':{'start':min(r['trading_date'] for r in rows),'end':max(r['trading_date'] for r in rows)},
        'counts':record.get('counts',{})|{'bars':len(rows)}}
    new.pop('receipt_id',None)
    Store(root).save_record(new)
    return {'reviewed_normalized_sha256':new['normalized_sha256'],'rows':len(rows),'review_evidence':review['evidence']}
