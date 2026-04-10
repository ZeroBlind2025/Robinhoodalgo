"""
VWAP Engine (Section 10).

Maintains a running VWAP and volume-weighted standard deviation using the
incremental formula Var = E[X^2] - E[X]^2. Reset at the start of every trading
day.
"""

from dataclasses import dataclass


@dataclass
class VWAPEngine:
    cumulative_tp_volume: float = 0.0   # Σ(TP * V)
    cumulative_volume: float = 0.0      # Σ(V)
    cumulative_tp2_volume: float = 0.0  # Σ(TP² * V)
    vwap: float = 0.0
    std_dev: float = 0.0

    def reset(self) -> None:
        self.cumulative_tp_volume = 0.0
        self.cumulative_volume = 0.0
        self.cumulative_tp2_volume = 0.0
        self.vwap = 0.0
        self.std_dev = 0.0

    def update(self, high: float, low: float, close: float, volume: float) -> None:
        if volume <= 0:
            return
        tp = (high + low + close) / 3.0
        self.cumulative_tp_volume += tp * volume
        self.cumulative_volume += volume
        self.cumulative_tp2_volume += (tp ** 2) * volume

        self.vwap = self.cumulative_tp_volume / self.cumulative_volume
        variance = (
            self.cumulative_tp2_volume / self.cumulative_volume
        ) - self.vwap ** 2
        self.std_dev = max(variance, 0.0) ** 0.5

    @property
    def upper_1(self) -> float:
        return self.vwap + self.std_dev

    @property
    def lower_1(self) -> float:
        return self.vwap - self.std_dev

    @property
    def upper_2(self) -> float:
        return self.vwap + 2 * self.std_dev

    @property
    def lower_2(self) -> float:
        return self.vwap - 2 * self.std_dev

    def z_score(self, price: float) -> float:
        if self.std_dev == 0.0:
            return 0.0
        return (price - self.vwap) / self.std_dev

    def deviation_pct(self, price: float) -> float:
        """(price - vwap) / vwap as a decimal (e.g. -0.003 = -0.3%)."""
        if self.vwap == 0.0:
            return 0.0
        return (price - self.vwap) / self.vwap
