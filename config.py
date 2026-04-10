"""
Configuration for the VWAP Momentum Scalper.

All tunable parameters are defined here (Section 11 of the build doc).
Values marked "Starting Value" in the spec are the defaults below.
Override any of them via environment variables of the same name.
"""

import os


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    return float(val) if val is not None else default


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val is not None else default


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

TICKERS = ["CRWV", "NBIS", "SMCI"]

# ---------------------------------------------------------------------------
# VWAP Reversion Strategy (Section 11.1)
# ---------------------------------------------------------------------------

VWAP_ENTRY_OFFSET = _env_float("VWAP_ENTRY_OFFSET", -0.003)   # -0.3%
VWAP_ENTRY_Z = _env_float("VWAP_ENTRY_Z", -1.5)               # z-score alternative
RSI_OVERSOLD = _env_float("RSI_OVERSOLD", 30.0)
VWAP_TP_OFFSET = _env_float("VWAP_TP_OFFSET", 0.0015)         # +0.15% above VWAP
VWAP_STOP_PCT = _env_float("VWAP_STOP_PCT", 0.005)            # 0.5%
VWAP_MAX_HOLD = _env_int("VWAP_MAX_HOLD", 30)                 # minutes

# ---------------------------------------------------------------------------
# Volume Momentum Strategy (Section 11.2)
# ---------------------------------------------------------------------------

MOMENTUM_RVOL_THRESHOLD = _env_float("MOMENTUM_RVOL_THRESHOLD", 2.5)
MOMENTUM_ROC_THRESHOLD = _env_float("MOMENTUM_ROC_THRESHOLD", 0.003)  # +0.3%
MOMENTUM_TP_PCT = _env_float("MOMENTUM_TP_PCT", 0.005)               # +0.5%
MOMENTUM_STOP_PCT = _env_float("MOMENTUM_STOP_PCT", 0.003)           # -0.3%
TRAILING_STOP_PCT = _env_float("TRAILING_STOP_PCT", 0.0025)          # 0.25%
MOMENTUM_MAX_HOLD = _env_int("MOMENTUM_MAX_HOLD", 15)                # minutes
MOMENTUM_RSI_MAX = _env_float("MOMENTUM_RSI_MAX", 70.0)              # skip if RSI already > 70
MOMENTUM_RSI_EXIT = _env_float("MOMENTUM_RSI_EXIT", 80.0)            # exit if RSI > 80
MOMENTUM_VOL_FADE = _env_float("MOMENTUM_VOL_FADE", 1.0)             # exit when RVOL falls below

# Generic shared signal helpers
MIN_RVOL = _env_float("MIN_RVOL", 0.8)
RSI_PERIOD = _env_int("RSI_PERIOD", 7)
ROC_PERIOD = _env_int("ROC_PERIOD", 5)

# ---------------------------------------------------------------------------
# Risk (Section 11.3)
# ---------------------------------------------------------------------------

# Budget is the capital allocated to this strategy. It acts as the upper
# bound on sizing even if the Robinhood account holds more cash. Starting
# live capital is small ($500) so we MUST use fractional shares.
BUDGET = _env_float("BUDGET", 500.0)
# Max dollar exposure per trade as a fraction of BUDGET.
MAX_EXPOSURE_PCT = _env_float("MAX_EXPOSURE_PCT", 0.025)   # 2.5% -> $12.50
# Minimum notional per order. Robinhood enforces a $1 floor on fractional
# orders; we keep a slightly higher cushion to avoid edge rejections.
MIN_NOTIONAL = _env_float("MIN_NOTIONAL", 1.00)

RISK_PER_TRADE = _env_float("RISK_PER_TRADE", 0.005)   # 0.5% dollar-risk
MAX_DAILY_LOSS = _env_float("MAX_DAILY_LOSS", 0.02)    # 2.0%
MAX_DAILY_TRADES = _env_int("MAX_DAILY_TRADES", 100)
MAX_CONCURRENT = _env_int("MAX_CONCURRENT", 2)
MAX_SECTOR_EXPOSURE = _env_int("MAX_SECTOR_EXPOSURE", 2)

# Minimum seconds to wait after closing a position on a ticker before
# allowing a new entry on the same ticker. Prevents buy-stop-buy-stop
# oscillation when price hugs a signal level.
TICKER_COOLDOWN_SECONDS = _env_int("TICKER_COOLDOWN_SECONDS", 120)

# Minimum seconds a momentum position must be held before the
# volume_fade exit can fire. RVOL decays quickly after a spike — the
# spec's "exit when RVOL<1" rule cuts trades too early without this.
MOMENTUM_MIN_HOLD_SECONDS = _env_int("MOMENTUM_MIN_HOLD_SECONDS", 90)

# Account type: "cash" or "margin". PDT (FINRA Rule 4210) only applies to
# margin accounts — cash accounts can day trade freely, bounded only by
# settled-cash availability (T+1). Set ACCOUNT_TYPE=cash to bypass the
# PDT tracker entirely.
ACCOUNT_TYPE = _env_str("ACCOUNT_TYPE", "cash").strip().lower()

# Times are Eastern Time, 24-hour
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0
MARKET_OPEN_BUFFER = _env_int("MARKET_OPEN_BUFFER", 15)   # minutes after open
TRADE_CUTOFF_HOUR = 15
TRADE_CUTOFF_MINUTE = 30
FLATTEN_HOUR = 15
FLATTEN_MINUTE = 50

# ---------------------------------------------------------------------------
# Polling (Section 11.4)
# ---------------------------------------------------------------------------

POLL_INTERVAL = _env_int("POLL_INTERVAL", 10)              # seconds
ORDER_TIMEOUT = _env_int("ORDER_TIMEOUT", 30)              # seconds
STALE_QUOTE_THRESHOLD = _env_int("STALE_QUOTE_THRESHOLD", 15)  # seconds
POSITION_CHECK_INTERVAL = _env_int("POSITION_CHECK_INTERVAL", 30)
ACCOUNT_CHECK_INTERVAL = _env_int("ACCOUNT_CHECK_INTERVAL", 300)

# ---------------------------------------------------------------------------
# Execution / environment
# ---------------------------------------------------------------------------

PAPER_MODE = _env_bool("PAPER_MODE", True)
PAPER_STARTING_CASH = _env_float("PAPER_STARTING_CASH", 500.0)

RH_USERNAME = _env_str("RH_USERNAME", "")
RH_PASSWORD = _env_str("RH_PASSWORD", "")
RH_MFA_SECRET = _env_str("RH_MFA_SECRET", "")       # legacy, usually unused now

PICKLE_NAME = _env_str("RH_PICKLE_NAME", "railway_session")

LOG_LEVEL = _env_str("LOG_LEVEL", "INFO")
TRADE_LOG_PATH = _env_str("TRADE_LOG_PATH", "trades.jsonl")
STATE_PATH = _env_str("STATE_PATH", "state.json")
RVOL_BASELINE_PATH = _env_str("RVOL_BASELINE_PATH", "rvol_baseline.json")

# Number of days of history used to build the RVOL baseline
RVOL_BASELINE_DAYS = _env_int("RVOL_BASELINE_DAYS", 15)

# ---------------------------------------------------------------------------
# HTTP status server (dashboard backend)
# ---------------------------------------------------------------------------

SERVER_HOST = _env_str("SERVER_HOST", "0.0.0.0")
# Railway injects PORT; fall back to 8000 for local runs
SERVER_PORT = _env_int("PORT", _env_int("SERVER_PORT", 8000))
DASHBOARD_TOKEN = _env_str("DASHBOARD_TOKEN", "")
DASHBOARD_ORIGIN = _env_str("DASHBOARD_ORIGIN", "*")
ENABLE_SERVER = _env_bool("ENABLE_SERVER", True)
