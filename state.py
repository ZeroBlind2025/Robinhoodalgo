"""
Per-ticker state and open-position bookkeeping.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

from data import BarBuilder
from indicators import RVolBaseline
from vwap import VWAPEngine


@dataclass
class OpenPosition:
    ticker: str
    strategy: str
    side: str
    quantity: float
    entry_price: float
    entry_time: datetime
    stop_price: float
    take_profit: float
    max_hold_minutes: int
    highest_since_entry: float = 0.0
    lowest_since_entry: float = 0.0

    def __post_init__(self):
        if self.highest_since_entry == 0.0:
            self.highest_since_entry = self.entry_price
        if self.lowest_since_entry == 0.0:
            self.lowest_since_entry = self.entry_price

    def mark(self, price: float) -> None:
        if price > self.highest_since_entry:
            self.highest_since_entry = price
        if price < self.lowest_since_entry or self.lowest_since_entry == 0.0:
            self.lowest_since_entry = price

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.entry_price) * self.quantity


@dataclass
class TickerState:
    ticker: str
    bars: BarBuilder = field(default_factory=BarBuilder)
    vwap: VWAPEngine = field(default_factory=VWAPEngine)
    last_price: float = 0.0
    last_price_time: Optional[datetime] = None
    last_bar_used_for_vwap: Optional[datetime] = None
    position: Optional[OpenPosition] = None

    def update_vwap_from_new_bars(self) -> None:
        """Feed any closed bars the VWAP hasn't seen yet."""
        for bar in self.bars.closed_bars():
            if (
                self.last_bar_used_for_vwap is None
                or bar.timestamp > self.last_bar_used_for_vwap
            ):
                self.vwap.update(bar.high, bar.low, bar.close, bar.volume)
                self.last_bar_used_for_vwap = bar.timestamp

    def reset_day(self) -> None:
        self.bars.reset()
        self.vwap.reset()
        self.last_bar_used_for_vwap = None


@dataclass
class StateManager:
    tickers: Dict[str, TickerState] = field(default_factory=dict)
    rvol: RVolBaseline = field(default_factory=RVolBaseline)

    def init_tickers(self, tickers) -> None:
        for t in tickers:
            if t not in self.tickers:
                self.tickers[t] = TickerState(ticker=t)

    def get(self, ticker: str) -> TickerState:
        if ticker not in self.tickers:
            self.tickers[ticker] = TickerState(ticker=ticker)
        return self.tickers[ticker]

    def open_positions(self) -> Dict[str, OpenPosition]:
        return {t: s.position for t, s in self.tickers.items() if s.position}

    def count_open(self) -> int:
        return sum(1 for s in self.tickers.values() if s.position)

    def reset_day(self) -> None:
        for s in self.tickers.values():
            s.reset_day()
