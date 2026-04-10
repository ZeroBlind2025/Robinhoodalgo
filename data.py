"""
Market data fetching + 1-minute bar builder (Sections 8.2 and 9).

`robin_stocks` only exposes 5-minute historicals, so we build our own
1-minute OHLCV bars by aggregating the polled quotes.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Dict, List, Optional

import config
from logger import get_logger

log = get_logger("data")


def _robin_stocks():
    import robin_stocks.robinhood as rh  # type: ignore
    return rh


# ---------------------------------------------------------------------------
# Quote fetching
# ---------------------------------------------------------------------------


def get_current_price(ticker: str) -> Optional[float]:
    try:
        rh = _robin_stocks()
        quote = rh.stocks.get_latest_price(ticker, includeExtendedHours=False)
        if not quote:
            return None
        return float(quote[0])
    except Exception as exc:  # noqa: BLE001
        log.warning("get_current_price(%s) failed: %s", ticker, exc)
        return None


def get_quote_data(ticker: str) -> Optional[Dict]:
    """Return a normalized quote dict or None on failure."""
    try:
        rh = _robin_stocks()
        raw = rh.stocks.get_quotes(ticker)
        if not raw or not raw[0]:
            return None
        q = raw[0]
        return {
            "ticker": ticker,
            "price": float(q["last_trade_price"]),
            "bid": float(q["bid_price"]),
            "ask": float(q["ask_price"]),
            "bid_size": int(float(q.get("bid_size") or 0)),
            "ask_size": int(float(q.get("ask_size") or 0)),
            "volume": int(float(q.get("volume", 0) or 0)),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("get_quote_data(%s) failed: %s", ticker, exc)
        return None


def get_historicals(
    ticker: str,
    interval: str = "5minute",
    span: str = "day",
) -> List[Dict]:
    """Fetch intraday OHLCV bars. Returns [] on failure."""
    try:
        rh = _robin_stocks()
        data = rh.stocks.get_stock_historicals(
            ticker, interval=interval, span=span
        )
        bars = []
        for d in data or []:
            if not d:
                continue
            bars.append({
                "open": float(d["open_price"]),
                "high": float(d["high_price"]),
                "low": float(d["low_price"]),
                "close": float(d["close_price"]),
                "volume": int(d["volume"]),
                "timestamp": d["begins_at"],
            })
        return bars
    except Exception as exc:  # noqa: BLE001
        log.warning("get_historicals(%s) failed: %s", ticker, exc)
        return []


def get_account_value() -> Optional[float]:
    try:
        rh = _robin_stocks()
        profile = rh.profiles.load_portfolio_profile()
        if profile and "equity" in profile:
            return float(profile["equity"])
    except Exception as exc:  # noqa: BLE001
        log.warning("get_account_value failed: %s", exc)
    return None


def get_live_positions() -> Dict[str, Dict]:
    try:
        rh = _robin_stocks()
        positions = rh.account.get_open_stock_positions()
        out: Dict[str, Dict] = {}
        for pos in positions or []:
            try:
                ticker = rh.stocks.get_symbol_by_url(pos["instrument"])
                qty = float(pos["quantity"])
            except Exception:  # noqa: BLE001
                continue
            if qty > 0:
                out[ticker] = {
                    "quantity": qty,
                    "avg_cost": float(pos["average_buy_price"]),
                }
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("get_live_positions failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Bar builder (Section 9)
# ---------------------------------------------------------------------------


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: datetime

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    def as_dict(self) -> Dict:
        return {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class BarBuilder:
    """
    Aggregates polled quotes into 1-minute OHLCV bars.

    Note: `robin_stocks` returns a *cumulative* session volume on each quote,
    not per-tick volume. We therefore track the cumulative volume and emit the
    delta over the current minute as the bar's volume.
    """

    max_bars: int = 500
    current_bar: Optional[Bar] = None
    completed_bars: Deque[Bar] = field(default_factory=lambda: deque(maxlen=500))
    _last_cum_volume: Optional[int] = None
    _bar_start_volume: Optional[int] = None

    def __post_init__(self):
        self.completed_bars = deque(maxlen=self.max_bars)

    def update(self, price: float, cumulative_volume: int, ts: datetime) -> Optional[Bar]:
        """
        Feed a new quote. Returns the just-completed bar, if any.
        """
        # Detect session reset (volume goes down across a day boundary)
        if self._last_cum_volume is not None and cumulative_volume < self._last_cum_volume:
            self._last_cum_volume = None
            self._bar_start_volume = None

        minute = ts.replace(second=0, microsecond=0)
        finished: Optional[Bar] = None

        if self.current_bar is None or self.current_bar.timestamp != minute:
            # Close prior bar
            if self.current_bar is not None:
                self.completed_bars.append(self.current_bar)
                finished = self.current_bar
            self._bar_start_volume = self._last_cum_volume if self._last_cum_volume is not None else cumulative_volume
            self.current_bar = Bar(
                open=price,
                high=price,
                low=price,
                close=price,
                volume=0,
                timestamp=minute,
            )

        # Update rolling OHLC
        bar = self.current_bar
        bar.high = max(bar.high, price)
        bar.low = min(bar.low, price)
        bar.close = price

        if self._bar_start_volume is None:
            self._bar_start_volume = cumulative_volume
        bar.volume = max(cumulative_volume - self._bar_start_volume, 0)

        self._last_cum_volume = cumulative_volume
        return finished

    def last_n(self, n: int) -> List[Bar]:
        if n <= 0:
            return []
        bars = list(self.completed_bars)[-n:]
        return bars

    def closed_bars(self) -> List[Bar]:
        return list(self.completed_bars)

    def snapshot(self) -> List[Bar]:
        """All closed bars plus the in-progress bar (if any)."""
        snap = list(self.completed_bars)
        if self.current_bar is not None:
            snap.append(self.current_bar)
        return snap

    def reset(self) -> None:
        self.current_bar = None
        self.completed_bars.clear()
        self._last_cum_volume = None
        self._bar_start_volume = None
