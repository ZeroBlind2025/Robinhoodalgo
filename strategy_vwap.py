"""
Strategy 1: VWAP Reversion (Section 4).

Entry rules (all must be true):
  1. Price is at least VWAP_ENTRY_OFFSET below VWAP, or price has touched the
     VWAP lower band (-1σ or -2σ).
  2. RSI_7 < RSI_OVERSOLD.
  3. RVOL > MIN_RVOL.
  4. Time is inside the trading window.
  5. No existing position in this ticker.
  6. Price is not making new intraday lows on increasing volume (don't catch
     falling knives): current_low > low_of_last_3_bars.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import config
import clock
from data import Bar
from indicators import rsi, closes
from vwap import VWAPEngine


@dataclass
class VWAPSignal:
    strategy: str
    ticker: str
    side: str  # 'buy'
    price: float
    vwap: float
    z_score: float
    rsi: float
    rvol: float
    reason: str
    stop_pct: float
    take_profit: float
    stop_price: float
    max_hold_minutes: int


def check_vwap_reversion(
    ticker: str,
    price: float,
    vwap_engine: VWAPEngine,
    bars: List[Bar],
    rvol_value: float,
    has_position: bool,
    ts: Optional[datetime] = None,
) -> Optional[VWAPSignal]:
    ts = ts or clock.now_et()

    if has_position:
        return None
    if not clock.within_trading_window(ts):
        return None
    if vwap_engine.vwap <= 0:
        return None
    if len(bars) < max(config.RSI_PERIOD + 2, 4):
        return None

    # 1. Price dislocated below VWAP?
    dev = vwap_engine.deviation_pct(price)
    z = vwap_engine.z_score(price)
    touched_band = price <= vwap_engine.lower_1 or price <= vwap_engine.lower_2
    below_by_offset = dev <= config.VWAP_ENTRY_OFFSET
    z_triggered = z <= config.VWAP_ENTRY_Z
    if not (touched_band or below_by_offset or z_triggered):
        return None

    # 2. RSI confirms seller exhaustion
    rsi_value = rsi(closes(bars), period=config.RSI_PERIOD)
    if rsi_value >= config.RSI_OVERSOLD:
        return None

    # 3. Liquidity
    if rvol_value < config.MIN_RVOL:
        return None

    # 6. Don't catch falling knives — current bar low must be above the min
    # low of the last 3 closed bars.
    recent = bars[-3:]
    min_recent_low = min(b.low for b in recent)
    current_low = bars[-1].low
    if current_low <= min_recent_low:
        # Also require that volume is increasing on the decline (true breakdown).
        if len(bars) >= 2 and bars[-1].volume > bars[-2].volume:
            return None

    # Build the order plan.
    stop_price = price * (1 - config.VWAP_STOP_PCT)
    take_profit = max(
        vwap_engine.vwap,
        vwap_engine.vwap * (1 + config.VWAP_TP_OFFSET),
    )

    reason_parts = []
    if touched_band:
        reason_parts.append(f"below lower band (z={z:.2f})")
    if below_by_offset:
        reason_parts.append(f"dev {dev*100:.2f}%")
    reason_parts.append(f"rsi={rsi_value:.1f}")
    reason_parts.append(f"rvol={rvol_value:.2f}")

    return VWAPSignal(
        strategy="vwap_reversion",
        ticker=ticker,
        side="buy",
        price=price,
        vwap=vwap_engine.vwap,
        z_score=z,
        rsi=rsi_value,
        rvol=rvol_value,
        reason=", ".join(reason_parts),
        stop_pct=config.VWAP_STOP_PCT,
        take_profit=take_profit,
        stop_price=stop_price,
        max_hold_minutes=config.VWAP_MAX_HOLD,
    )


def check_vwap_reversion_exit(
    entry_price: float,
    entry_time: datetime,
    price: float,
    vwap_engine: VWAPEngine,
    ts: Optional[datetime] = None,
) -> Optional[str]:
    """Return an exit reason string, or None."""
    ts = ts or clock.now_et()

    # Take profit: price crosses back above VWAP (or TP offset)
    tp_target = vwap_engine.vwap * (1 + config.VWAP_TP_OFFSET)
    if price >= tp_target and price >= vwap_engine.vwap:
        return "tp_vwap_cross"

    # Stop loss: entry-based
    stop_price = entry_price * (1 - config.VWAP_STOP_PCT)
    if price <= stop_price:
        return "stop_loss"

    # Stop loss: extreme dislocation (thesis broken)
    if vwap_engine.std_dev > 0 and price <= vwap_engine.lower_2:
        return "stop_lower_2sigma"

    # Time stop
    held_minutes = (ts - entry_time).total_seconds() / 60.0
    if held_minutes >= config.VWAP_MAX_HOLD:
        return "time_stop"

    return None
