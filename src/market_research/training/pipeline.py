"""Train-only preprocessing and small sklearn adapters with data-only JSON inference."""
import math
import warnings
import numpy as np
from sklearn.linear_model import Ridge, LogisticRegression
from sklearn.tree import DecisionTreeRegressor, DecisionTreeClassifier
from sklearn.exceptions import ConvergenceWarning
from threadpoolctl import threadpool_limits

ALLOWLIST = frozenset(
    [f'{prefix}_{n}' for prefix in ('close_price_change', 'close_return') for n in (1, 5, 20, 60)] +
    [f'{prefix}_{n}' for prefix in ('realized_log_price_change_volatility', 'realized_log_return_volatility') for n in (5, 20, 60)] +
    ['close_to_ma_20', 'close_to_ma_60', 'open_gap', 'intraday_high_low_ratio', 'volume_to_prior20_mean'])
TRANSIENT = frozenset(('missing_session_or_collection_failure', 'insufficient_history_or_prelisting_unverified',
                       'lookback_outside_calendar', 'temporary_missing'))
MODELS = {'regression': ('zero', 'mean', 'ridge', 'tree'), 'classification': ('frequency', 'logistic', 'tree')}
PRESETS = {'zero': {}, 'mean': {}, 'frequency': {'alpha': 1.0, 'formula': '(n_up+alpha)/(n+2*alpha)'},
           'ridge': {'alpha': 1.0, 'fit_intercept': True, 'solver': 'svd'},
           'logistic': {'C': 1.0, 'fit_intercept': True, 'solver': 'lbfgs', 'max_iter': 300, 'tol': 1e-8},
           'tree': {'max_depth': 3, 'min_samples_leaf': 8}}


def check_features(names):
    if not names or len(set(names)) != len(names) or set(names) - ALLOWLIST:
        raise ValueError('nonempty_unique_feature_allowlist_required')


def project_features(feature, names):
    """Explicit projection only. Structurally absent provider fields are never imputed."""
    check_features(names)
    out = {}
    features = feature['features']
    for name in names:
        if name not in features: raise ValueError('required_feature_absent:' + name)
        item = features[name]
        value = item['value']
        if item['status'] == 'invalid': raise ValueError('invalid_feature:' + name)
        if item['status'] != 'ready':
            if item.get('reason') not in TRANSIENT: raise ValueError('required_feature_contract_unmet:' + name + ':' + str(item.get('reason')))
            if value is not None: raise ValueError('unavailable_feature_has_value:' + name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError('nonfinite_or_invalid_feature:' + name)
        out[name] = value
    return out


def matrix(records, names):
    if not records: raise ValueError('empty_input_matrix')
    if any(set(row) != set(names) for row in records): raise ValueError('missing_or_extra_feature_columns')
    a = np.array([[np.nan if r[n] is None else r[n] for n in names] for r in records], dtype=float)
    if any(isinstance(v, bool) or (v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v))) for r in records for v in r.values()):
        raise ValueError('nonfinite_or_invalid_feature')
    return a


def fit_preprocessor(records, names, scale):
    check_features(names)
    a = matrix(records, names)
    all_missing = np.all(np.isnan(a), axis=0)
    medians = [0.0 if all_missing[j] else float(np.nanmedian(a[:, j])) for j in range(len(names))]
    filled = np.where(np.isnan(a), medians, a)
    constant = np.all(filled == filled[0], axis=0)
    active = np.flatnonzero(~(all_missing | constant)).tolist()
    b = filled[:, active]
    with np.errstate(over='raise', invalid='raise'):
        try:
            means = b.mean(axis=0) if scale else np.zeros(len(active))
            scales = b.std(axis=0) if scale else np.ones(len(active))
        except FloatingPointError as exc: raise ValueError('preprocessing_numeric_overflow') from exc
    if np.any(scales <= 0) or not np.all(np.isfinite(scales)): raise ValueError('invalid_scaling_statistics')
    return {'version': '5.0.0', 'features': list(names), 'medians': medians, 'active_indices': active,
            'means': means.tolist(), 'scales': scales.tolist(), 'scaled': scale,
            'all_missing_columns': [names[j] for j in np.flatnonzero(all_missing)],
            'constant_columns': [names[j] for j in np.flatnonzero(constant & ~all_missing)],
            'missing_counts': np.isnan(a).sum(axis=0).tolist(), 'fit_count': len(records),
            'clipping': None, 'all_missing_policy': 'drop; never learn from evaluation values', 'constant_policy': 'drop'}


def transform(records, state):
    a = matrix(records, state['features'])
    with np.errstate(over='raise', invalid='raise'):
        try:
            filled = np.where(np.isnan(a), state['medians'], a)
            b = (filled[:, state['active_indices']] - state['means']) / state['scales']
        except FloatingPointError as exc: raise ValueError('transform_numeric_overflow') from exc
    if not np.all(np.isfinite(b)): raise ValueError('nonfinite_transformed_features')
    return b


def predict(pipeline, records):
    x = transform(records, pipeline['preprocessing'])
    m = pipeline['model']
    if m['kind'] == 'constant': result = np.full(len(x), m['value'])
    elif m['kind'] == 'linear':
        z = x @ np.asarray(m['coefficients']) + m['intercept']
        result = z if pipeline['task'] == 'regression' else np.array([math.exp(-max(0., -v))/(1+math.exp(-abs(v))) for v in z])
    elif m['kind'] == 'tree':
        # sklearn trees cast inputs to float32, including values at split boundaries.
        x = x.astype(np.float32)
        if not np.all(np.isfinite(x)): raise ValueError('tree_float32_overflow')
        values = []
        for row in x:
            node = 0
            for _ in range(len(m['left']) + 1):
                if m['left'][node] == -1: break
                node = m['left'][node] if row[m['feature'][node]] <= m['threshold'][node] else m['right'][node]
            else: raise ValueError('cyclic_tree_artifact')
            values.append(m['values'][node])
        result = np.array(values)
    else: raise ValueError('unknown_model_adapter')
    if not np.all(np.isfinite(result)): raise ValueError('nonfinite_model_prediction')
    if pipeline['task'] == 'classification' and np.any((result < 0) | (result > 1)): raise ValueError('probability_out_of_range')
    return result.tolist()


def fit_pipeline(records, y, names, task, model_name, seed, *, one_class='error', minimum=8):
    if task not in MODELS or model_name not in MODELS[task]: raise ValueError('model_task_mismatch')
    if len(y) != len(records) or len(y) < minimum: raise ValueError('insufficient_training_samples')
    target = np.asarray(y, dtype=float)
    if not np.all(np.isfinite(target)): raise ValueError('nonfinite_training_target')
    if task == 'classification' and not set(y) <= {0, 1}: raise ValueError('binary_target_required')
    if one_class not in ('error', 'constant'): raise ValueError('explicit_one_class_policy_required')
    state = fit_preprocessor(records, names, model_name in ('ridge', 'logistic'))
    x = transform(records, state)
    model, notes, estimator = None, [], None
    if model_name == 'zero': model = {'kind': 'constant', 'value': 0.0}
    elif model_name == 'mean': model = {'kind': 'constant', 'value': math.fsum(float(v)/len(y) for v in y)}
    elif model_name == 'frequency': model = {'kind': 'constant', 'value': (sum(y)+1)/(len(y)+2)}
    elif task == 'classification' and len(set(y)) == 1:
        if one_class == 'error': raise ValueError('one_class_training_rejected')
        model = {'kind': 'constant', 'value': (sum(y)+1)/(len(y)+2)}
        notes.append('one_class_explicit_smoothed_frequency_fallback; alpha=1')
    else:
        if not state['active_indices']: raise ValueError('no_informative_training_columns')
        if model_name == 'ridge': estimator = Ridge(**PRESETS['ridge'])
        elif model_name == 'logistic': estimator = LogisticRegression(**PRESETS['logistic'], random_state=seed)
        else:
            cls = DecisionTreeRegressor if task == 'regression' else DecisionTreeClassifier
            estimator = cls(**PRESETS['tree'], random_state=seed)
        with threadpool_limits(limits=1), warnings.catch_warnings():
            warnings.simplefilter('error', ConvergenceWarning)
            try: estimator.fit(x, target)
            except ConvergenceWarning as exc: raise ValueError('model_did_not_converge') from exc
        if model_name in ('ridge', 'logistic'):
            model = {'kind': 'linear', 'coefficients': np.asarray(estimator.coef_).reshape(-1).tolist(),
                     'intercept': float(np.asarray(estimator.intercept_).reshape(-1)[0])}
        else:
            t = estimator.tree_
            vals = t.value[:, 0, 0] if task == 'regression' else t.value[:, 0, 1]/t.value[:, 0, :].sum(axis=1)
            model = {'kind': 'tree', 'left': t.children_left.tolist(), 'right': t.children_right.tolist(),
                     'feature': t.feature.tolist(), 'threshold': t.threshold.tolist(), 'values': vals.tolist()}
    pipeline = {'version': '5.0.0', 'task': task, 'model_name': model_name, 'settings': PRESETS[model_name],
                'seed': seed, 'one_class_policy': one_class, 'notes': notes, 'preprocessing': state, 'model': model}
    portable = predict(pipeline, records)
    expected = (estimator.predict(x) if task == 'regression' else estimator.predict_proba(x)[:, 1]).tolist() if estimator is not None else portable
    if not np.allclose(portable, expected, rtol=1e-12, atol=1e-12): raise ValueError('adapter_export_prediction_mismatch')
    pipeline['export_validation'] = {'status': 'PASS', 'count': len(y), 'rtol': 1e-12, 'atol': 1e-12,
                                     'max_absolute_error': float(np.max(np.abs(np.array(portable)-expected)))}
    return pipeline
