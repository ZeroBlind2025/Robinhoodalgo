"""
Risk management: position sizing, PDT tracker, daily limits (Section 6).
"""

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import config
from logger import get_logger

log = get_logger("risk")


# ---------------------------------------------------------------------------
# Pattern Day Trader tracker (Section 6.3)
# ---------------------------------------------------------------------------


@dataclass
class PDTTracker:
    day_trades: List[datetime] = field(default_factory=list)

    def can_trade(self, account_value: float) -> bool:
        # PDT (FINRA Rule 4210) only applies to margin accounts. Cash
        # accounts can day trade without limit — they're bounded by T+1
        # settlement instead, not by trade count.
        if config.ACCOUNT_TYPE == "cash":
            return True
        if account_value >= 25000:
            return True
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        recent = [t for t in self.day_trades if t > cutoff]
        self.day_trades = recent
        return len(recent) < 3

    def record_day_trade(self, ts: Optional[datetime] = None) -> None:
        self.day_trades.append(ts or datetime.now(timezone.utc))

    def to_dict(self) -> Dict:
        return {"day_trades": [t.isoformat() for t in self.day_trades]}

    @classmethod
    def from_dict(cls, data: Dict) -> "PDTTracker":
        obj = cls()
        for s in (data or {}).get("day_trades", []):
            try:
                obj.day_trades.append(datetime.fromisoformat(s))
            except ValueError:
                pass
        return obj


# ---------------------------------------------------------------------------
# Position sizing (Section 6.1)
# ---------------------------------------------------------------------------


def compute_position_size(
    account_value: float,
    entry_price: float,
    stop_pct: float,
) -> float:
    """
    Fractional share sizing.

    With a $500 budget and 3 tickers at $30–$135, whole-share sizing
    would blow through the exposure cap on a single share. We use
    Robinhood's fractional share support instead.

    Rule (matches the dashboard's lotSize):
        qty = min(
            BUDGET * MAX_EXPOSURE_PCT / price,   # dollar-exposure cap
            BUDGET * RISK_PER_TRADE / (price * stop_pct),  # risk cap
        )

    The effective sizing basis is min(account_value, BUDGET) so we never
    size above what's actually in the Robinhood account (e.g. after losses
    or if the broker balance is less than configured budget).

    Returns a float quantity rounded to 6 decimal places (Robinhood's
    fractional precision). Returns 0.0 if the resulting notional would be
    below MIN_NOTIONAL.
    """
    if account_value <= 0 or entry_price <= 0 or stop_pct <= 0:
        return 0.0

    basis = min(account_value, config.BUDGET)
    max_exposure = basis * config.MAX_EXPOSURE_PCT
    max_loss = basis * config.RISK_PER_TRADE
    per_share_risk = entry_price * stop_pct

    qty_by_exposure = max_exposure / entry_price
    qty_by_risk = (max_loss / per_share_risk) if per_share_risk > 0 else qty_by_exposure

    qty = min(qty_by_exposure, qty_by_risk)
    qty = math.floor(qty * 1_000_000) / 1_000_000  # 6dp truncation

    if qty * entry_price < config.MIN_NOTIONAL:
        return 0.0
    return max(qty, 0.0)


# ---------------------------------------------------------------------------
# Daily limits (Section 6.2)
# ---------------------------------------------------------------------------


@dataclass
class DailyRiskState:
    date: str = ""
    starting_equity: float = 0.0
    realized_pnl: float = 0.0
    trades_opened: int = 0
    stopped_out: bool = False

    def to_dict(self) -> Dict:
        return {
            "date": self.date,
            "starting_equity": self.starting_equity,
            "realized_pnl": self.realized_pnl,
            "trades_opened": self.trades_opened,
            "stopped_out": self.stopped_out,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "DailyRiskState":
        if not data:
            return cls()
        return cls(
            date=data.get("date", ""),
            starting_equity=float(data.get("starting_equity", 0.0)),
            realized_pnl=float(data.get("realized_pnl", 0.0)),
            trades_opened=int(data.get("trades_opened", 0)),
            stopped_out=bool(data.get("stopped_out", False)),
        )


class RiskManager:
    def __init__(self, state_path: Optional[str] = None):
        self.state_path = state_path or config.STATE_PATH
        self.daily = DailyRiskState()
        self.pdt = PDTTracker()
        self.load()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        if not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path) as f:
                data = json.load(f)
            self.daily = DailyRiskState.from_dict(data.get("daily", {}))
            self.pdt = PDTTracker.from_dict(data.get("pdt", {}))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Failed to load risk state: %s", exc)

    def save(self) -> None:
        try:
            with open(self.state_path, "w") as f:
                json.dump(
                    {"daily": self.daily.to_dict(), "pdt": self.pdt.to_dict()},
                    f,
                    default=str,
                )
        except OSError as exc:
            log.warning("Failed to save risk state: %s", exc)

    # ------------------------------------------------------------------
    # daily lifecycle
    # ------------------------------------------------------------------

    def begin_day(self, date_str: str, account_value: float) -> None:
        if self.daily.date != date_str:
            self.daily = DailyRiskState(
                date=date_str,
                starting_equity=account_value,
                realized_pnl=0.0,
                trades_opened=0,
                stopped_out=False,
            )
            self.save()

    def record_open(self) -> None:
        self.daily.trades_opened += 1
        self.save()

    def record_close(self, pnl: float) -> None:
        self.daily.realized_pnl += pnl
        self.save()

    # ------------------------------------------------------------------
    # checks
    # ------------------------------------------------------------------

    def daily_loss_exceeded(self) -> bool:
        if self.daily.starting_equity <= 0:
            return False
        threshold = -config.MAX_DAILY_LOSS * self.daily.starting_equity
        return self.daily.realized_pnl <= threshold

    def can_open_new_position(
        self, account_value: float, open_position_count: int
    ) -> bool:
        if self.daily.stopped_out:
            return False
        if self.daily.trades_opened >= config.MAX_DAILY_TRADES:
            return False
        if open_position_count >= config.MAX_CONCURRENT:
            return False
        if self.daily_loss_exceeded():
            self.daily.stopped_out = True
            self.save()
            return False
        if not self.pdt.can_trade(account_value):
            return False
        return True

    def size(self, account_value: float, entry_price: float, stop_pct: float) -> float:
        return compute_position_size(account_value, entry_price, stop_pct)
