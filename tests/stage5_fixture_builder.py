"""Signal/null market paths; Stage3 computes labels from exactly the Stage4 tape prices."""
import random
import math
from pathlib import Path
from fixture_builder import append
from stage3_fixture_builder import market_story
from market_research.datasets import build_dataset
from market_research.datasets.calendar import plus_seconds
from market_research.pit import import_artifacts
from market_research.storage import write_json


def story(pit, store, root, *, signal=True, seed=20261003, small=False):
    root = Path(root)
    _, cfg, sessions = market_story(pit, root, n=0)
    rng = random.Random(seed)
    n = 145 if small else len(sessions)
    x = [rng.uniform(-1, 1) for _ in range(n)]
    noise = [rng.gauss(0, .006) for _ in range(n)]
    beta = .025 if signal else 0.
    opens = [100.]
    for i in range(1, n):
        opens.append(opens[-1] * math.exp(beta*(x[i-2] if i >= 2 else 0) + noise[i]))
    for i, s in enumerate(sessions[:n]):
        price = opens[i]
        append(root, label='stage5-'+s['trading_date'], date=s['trading_date'], close=price,
               published=plus_seconds(s['close_utc'], 60), received=plus_seconds(s['close_utc'], 120), usable=plus_seconds(s['close_utc'], 180),
               extra={'open': price, 'high': price*(1.02+.01*x[i]), 'low': price*.99,
                      'volume': int(10000*(1+.7*x[i])), 'observation_end_utc': s['close_utc'], 'session_scope': 'synthetic_regular_session'})
    imported = import_artifacts(pit, root, domain='synthetic')
    if imported['quarantined']: raise AssertionError(imported)
    sample_stop = 121 if small else 231
    cfg.update(samples=[{'decision_date': s['trading_date']} for s in sessions[61:sample_stop]],
               simulation_asof='2025-01-02T00:00:00Z', label_asof='2025-01-02T00:00:00Z')
    fixed = build_dataset(pit, store, cfg)
    def interval(a, b): return [sessions[a]['trading_date'], sessions[b]['trading_date']]
    split = {'kind': 'explicit', 'horizon': 5, 'min_samples': {'train': 40, 'validation': 10, 'test': 10},
             'folds': [{'train': interval(61, 150), 'validation': interval(156, 180), 'test': interval(186, 210),
                        'training_asof': sessions[156]['trading_date']+'T00:00:00Z'},
                       {'train': interval(61, 180), 'validation': interval(186, 205), 'test': interval(211, 230),
                        'training_asof': sessions[186]['trading_date']+'T00:00:00Z'}]}
    if small:
        split['min_samples'] = {'train': 8, 'validation': 3, 'test': 5}
        split['folds'] = [{'train': interval(61, 85), 'validation': interval(91, 100), 'test': interval(106, 120),
                           'training_asof': sessions[91]['trading_date']+'T00:00:00Z'}]
    first_test = 106 if small else 186
    end_index = 127 if small else 237
    base = {'version': '5.0.0', 'mode': 'synthetic', 'dataset_snapshot_id': fixed['snapshot_id'], 'dataset_content_sha256': fixed['content_sha256'],
            'target_id': 'price_return', 'horizon': 5, 'features': ['intraday_high_low_ratio', 'volume_to_prior20_mean', 'close_price_change_5'],
            'seed': seed, 'one_class_policy': 'error', 'model_selection': 'none', 'evaluation_asof': cfg['label_asof'], 'split': split,
            'regression_threshold': .005, 'classification_threshold': .55,
            'simulation': {'policy_version': '4.0.0', 'horizon': 5, 'currency': 'USD', 'initial_cash': '10000.00',
                           'start_at': sessions[first_test]['trading_date']+'T00:00:00Z', 'end_at': sessions[end_index]['trading_date']+'T23:59:00Z',
                           'allocation_fraction': '.25', 'max_prior_volume_participation': '.05',
                           'insufficient_cash_policy': 'partial_cancel', 'repeat_signal_policy': 'defer',
                           'costs': {'commission_fixed': '1', 'half_spread_bps': '5', 'slippage_bps': '5'}}}
    generator = {'domain': 'SYNTHETIC_TEST_ONLY', 'seed': seed, 'signal': signal, 'sessions': n, 'samples': sample_stop-61,
                 'formula': 'x_i iid Uniform(-1,1); epsilon_i iid N(0,.006^2), independent streams in one seeded RNG; O_0=100; log(O_i/O_(i-1))=beta*x_(i-2)+epsilon_i (x_-1=0); C_i=O_i; H_i=O_i*(1.02+.01*x_i); L_i=.99*O_i; V_i=floor(10000*(1+.7*x_i))',
                 'beta': beta, 'feature_observation': 'complete bar available close+180s before close+3600s decision',
                 'target': 'O_(i+6)/O_(i+1)-1; Stage3 actual label computation, no actions; return conditional mean depends on known x_i when beta != 0',
                 'future_noise_in_features': False, 'null_interpretation': 'when beta=0, future epsilon_(i+2:i+6) independent of feature history; finite sample chance performance allowed',
                 'calibration': 'none; not required', 'purpose': 'connection and reproducibility, never evidence of stock predictive power'}
    return base, generator
