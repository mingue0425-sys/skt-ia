"""Executable minimum schema; None denotes unknown, never a guessed timestamp."""
BAR_REQUIRED = {"source": str, "local_instrument_id": str, "requested_symbol": str,
    "trading_date": str, "price_semantics": str, "currency": str,
    "open": (int, float, type(None)), "high": (int, float, type(None)),
    "low": (int, float, type(None)), "close": (int, float, type(None)),
    "volume": (int, type(None)), "adjusted_close": (int, float, type(None)),
    "publication_time_utc": (str, type(None)), "revision_time_utc": (str, type(None))}
