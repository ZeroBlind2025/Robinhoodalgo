"""
Strategy 2: Volume Momentum Breakout (Section 5).

Entry rules (all must be true):
  1. RVOL > MOMENTUM_RVOL_THRESHOLD
  2. ROC_5 > MOMENTUM_ROC_THRESHOLD
  3. Price ABOVE VWAP
  4. RSI_7 < MOMENTUM_RSI_MAX
  5. Time is inside the trading window
  6. No existing position in this ticker
  7. Momentum confirmed across at least 2 consecutive 1-min bars
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import config
import clock
from data import Bar
from indicators import rsi, roc, closes
from vwap import VWAPEngine


@dataclass
class MomentumSignal:
    strategy: str
    ticker: str
    side: str  # 'buy'
    price: float
    vwap: float
    rsi: float
    roc5: float
    rvol: float
    reason: str
    stop_pct: float
    take_profit: float
    stop_price: float
    max_hold_minutes: int


def _consecutive_up_bars(bars: List[Bar], n: int = 2) -> bool:
    if len(bars) < n + 1:
        return False
    tail = bars[-n:]
    for i in range(n):
        if tail[i].close <= tail[i].open:
            return False
    return True


def check_momentum_breakout(
    ticker: str,
    price: float,
    vwap_engine: VWAPEngine,
    bars: List[Bar],
    rvol_value: float,
    has_position: bool,
    ts: Optional[datetime] = None,
) -> Optional[MomentumSignal]:
    ts = ts or clock.now_et()

    if has_position:
        return None
    if not clock.within_trading_window(ts):
        return None
    if vwap_engine.vwap <= 0:
        return None
    if len(bars) < max(config.RSI_PERIOD + 2, config.ROC_PERIOD + 2, 3):
        return None

    # 1. RVOL spike
    if rvol_value < config.MOMENTUM_RVOL_THRESHOLD:
        return None

    # 2. ROC_5 confirms acceleration
    roc5 = roc(closes(bars), period=config.ROC_PERIOD)
    if roc5 < config.MOMENTUM_ROC_THRESHOLD:
        return None

    # 3. Price above VWAP (momentum with the trend)
    if price <= vwap_engine.vwap:
        return None

    # 4. Not already overbought
    rsi_value = rsi(closes(bars), period=config.RSI_PERIOD)
    if rsi_value >= config.MOMENTUM_RSI_MAX:
        return None

    # 7. Two consecutive up bars
    if not _consecutive_up_bars(bars, n=2):
        return None

    stop_price = price * (1 - config.MOMENTUM_STOP_PCT)
    take_profit = price * (1 + config.MOMENTUM_TP_PCT)

    reason = (
        f"rvol={rvol_value:.2f}, roc5={roc5*100:.2f}%, "
        f"rsi={rsi_value:.1f}, >vwap"
    )

    return MomentumSignal(
        strategy="momentum_breakout",
        ticker=ticker,
        side="buy",
        price=price,
        vwap=vwap_engine.vwap,
        rsi=rsi_value,
        roc5=roc5,
        rvol=rvol_value,
        reason=reason,
        stop_pct=config.MOMENTUM_STOP_PCT,
        take_profit=take_profit,
        stop_price=stop_price,
        max_hold_minutes=config.MOMENTUM_MAX_HOLD,
    )


def check_momentum_exit(
    entry_price: float,
    entry_time: datetime,
    highest_since_entry: float,
    price: float,
    bars: List[Bar],
    rvol_value: float,
    ts: Optional[datetime] = None,
) -> Optional[str]:
    ts = ts or clock.now_et()

    # Take profit (fixed target)
    if price >= entry_price * (1 + config.MOMENTUM_TP_PCT):
        return "tp_fixed"

    # RSI exhaustion
    if len(bars) >= config.RSI_PERIOD + 2:
        rsi_value = rsi(closes(bars), period=config.RSI_PERIOD)
        if rsi_value >= config.MOMENTUM_RSI_EXIT:
            return "tp_rsi_exhausted"

    # Trailing stop from highest since entry
    if highest_since_entry > 0:
        trail_price = highest_since_entry * (1 - config.TRAILING_STOP_PCT)
        if price <= trail_price and highest_since_entry > entry_price:
            return "trailing_stop"

    # Fixed stop loss
    if price <= entry_price * (1 - config.MOMENTUM_STOP_PCT):
        return "stop_loss"

    # Volume fade
    if rvol_value > 0 and rvol_value < config.MOMENTUM_VOL_FADE:
        return "volume_fade"

    # Time stop
    held_minutes = (ts - entry_time).total_seconds() / 60.0
    if held_minutes >= config.MOMENTUM_MAX_HOLD:
        return "time_stop"

    return None
