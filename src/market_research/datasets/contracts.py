"""Explicit numeric and timing contracts; versions participate in content hashes."""
from pathlib import Path
from ..storage import canonical, sha256

FEATURE_VERSION = "3.0.1"
LABEL_VERSION = "3.0.1"
LABEL_VERSION_ENDPOINT = "3.1.0"
DATASET_VERSION = "3.0.1"


def label_version(value=LABEL_VERSION):
    if value not in (LABEL_VERSION, LABEL_VERSION_ENDPOINT):
        raise ValueError("unsupported_label_definition_version")
    return value


def code_hash():
    return sha256(canonical({p.name: sha256(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("*.py"))}))


def feature_contract():
    return {
        "version": FEATURE_VERSION,
        "close_returns": {"horizons": [1, 5, 20, 60], "formula": "C[t]/C[t-n]-1", "observations": "n+1 consecutive sessions"},
        "volatility": {"windows": [5, 20, 60], "formula": "sample stdev(log(C[t]/C[t-1]))", "ddof": 1, "annualized": False, "annualization_constant": None},
        "moving_average_ratio": {"windows": [20, 60], "formula": "C[t]/mean(C[t-n+1:t])-1", "includes_current": True},
        "open_gap": "O[t]/C[t-1]-1",
        "intraday_range": "H[t]/L[t]-1",
        "relative_volume": "V[t]/mean(V[t-20:t-1]); denominator excludes current",
        "units": "dimensionless ratios; daily log-return stdev; session counts",
        "missing": "No fill, no compressing missing trading sessions, no normalization or winsorization.",
        "as_traded_mode": "Explicit price_change_only; raw multi-day ratios/log changes are NOT economic returns across splits.",
        "adjusted": "Same-source, same-kind, same-currency, same-session, same-meaning values only; no raw reconstruction.",
        "close_only": "OHLC/volume features remain unavailable. No field borrowing.",
    }


def label_contract(version=LABEL_VERSION):
    version = label_version(version)
    contract = {
        "version": version, "horizons": [1, 5, 20],
        "decision": "regular close + 60 elapsed minutes", "entry": "next session open e", "exit": "session e+h open",
        "price_return": "exit quantity * exit as-traded open / (initial quantity * entry open) - 1; verified splits only; cash excluded",
        "cash_total_return": "(exit shares * exit open + paid cash + explicitly included receivables) / entry investment - 1",
        "flags": {"up_5": "price_return_5 > 0", "down_5pct_5": "price_return_5 <= -0.05"},
        "corporate_actions": "Complete version-bound interval coverage required even when action list is empty; merger/delisting consideration unsupported.",
        "quantity": 1, "reinvest_dividends": False,
        "dividend_right": "held immediately before ex-date open; buying at ex-date open does not earn the dividend",
        "same_day_action_order": "split first, then as-declared dividend per post-split share; contract must explicitly select this order",
        "payment_date_precision": "date-only payment is definitely paid at exit open only if payment_date < exit_date; same-day remains receivable",
        "available_at": "max(used policy availability, observation completion, coverage/state evidence availability) + explicit processing delay",
        "states": ["ready", "pending", "blocked", "invalid"], "fill_missing_exit": False,
    }
    if version == LABEL_VERSION_ENDPOINT:
        contract.update(
            action_classification="Reviewed coverage.action_classifications binds each generic action version ID to share_split or ordinary_cash_dividend; other/unknown types blocked",
            same_day_action_order="Required for cash return only; verified splits change quantity, ordinary cash dividends excluded from price dependencies",
            halt_guard="UTC endpoint instants; [start_at,resumed_at), exact resumption boundary blocked; complete coverage including carry-in halts required",
            cash_available_at="Separate used cash-action dependencies and processing delay; cannot postpone price_return",
            cash_coverage="coverage.cash_action_version_ids explicitly enumerates required ordinary cash versions; unavailable versions block cash only",
            price_action_coverage="coverage.price_action_version_ids explicitly enumerates required non-cash versions; unsupported/unavailable versions block price",
            execution_claim="Price-based evaluation only; no order fill or official auction validity guarantee",
        )
    return contract
