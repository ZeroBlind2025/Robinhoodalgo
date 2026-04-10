"""
Technical indicators: RSI (Wilder), ROC, and RVOL.

Pure functions so they're easy to unit-test and replay.
"""

from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Sequence

import config
from data import Bar


def rsi(values: Sequence[float], period: int = None) -> float:
    """
    Wilder's RSI. Returns 50 if not enough data.
    """
    period = period or config.RSI_PERIOD
    if len(values) <= period:
        return 50.0

    gains = 0.0
    losses = 0.0
    # Seed averages from the first `period` changes.
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period

    # Wilder smoothing for the remainder.
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def roc(values: Sequence[float], period: int = None) -> float:
    """Rate of change, as a decimal. 0.005 == +0.5%."""
    period = period or config.ROC_PERIOD
    if len(values) <= period:
        return 0.0
    past = values[-period - 1]
    now = values[-1]
    if past == 0:
        return 0.0
    return (now - past) / past


def closes(bars: Sequence[Bar]) -> List[float]:
    return [b.close for b in bars]


# ---------------------------------------------------------------------------
# RVOL baseline
# ---------------------------------------------------------------------------


class RVolBaseline:
    """
    Tracks the average per-minute volume at each minute-of-day, keyed by
    ticker. Updated incrementally from historical + live data.
    """

    def __init__(self):
        # ticker -> minute_of_day (0..1439) -> (sum_volume, count)
        self._agg: Dict[str, Dict[int, List[float]]] = defaultdict(
            lambda: defaultdict(lambda: [0.0, 0])
        )

    def add_sample(self, ticker: str, ts: datetime, volume: float) -> None:
        if volume <= 0:
            return
        mod = ts.hour * 60 + ts.minute
        bucket = self._agg[ticker][mod]
        bucket[0] += volume
        bucket[1] += 1

    def average(self, ticker: str, ts: datetime) -> float:
        mod = ts.hour * 60 + ts.minute
        bucket = self._agg.get(ticker, {}).get(mod)
        if not bucket or bucket[1] == 0:
            return 0.0
        return bucket[0] / bucket[1]

    def rvol(self, ticker: str, ts: datetime, current_volume: float) -> float:
        avg = self.average(ticker, ts)
        if avg <= 0:
            return 0.0
        return current_volume / avg

    def to_dict(self) -> Dict:
        out: Dict[str, Dict[str, List[float]]] = {}
        for ticker, buckets in self._agg.items():
            out[ticker] = {str(k): v for k, v in buckets.items()}
        return out

    @classmethod
    def from_dict(cls, data: Dict) -> "RVolBaseline":
        obj = cls()
        for ticker, buckets in (data or {}).items():
            for k, v in buckets.items():
                obj._agg[ticker][int(k)] = [float(v[0]), int(v[1])]
        return obj
