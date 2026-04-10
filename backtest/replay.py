"""
Offline replay harness for the VWAP / momentum strategies.

Takes a JSON dump (produced by data_collector.py) of OHLCV bars per ticker,
feeds them through the strategy pipeline, and prints a summary of the
hypothetical trades that would have fired.

This intentionally ignores the trading-window restriction so you can see how
the strategies behave over the full day.
"""

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pytz

import config
from data import Bar
from indicators import RVolBaseline, rsi
from strategy_momentum import check_momentum_breakout, check_momentum_exit
from strategy_vwap import check_vwap_reversion, check_vwap_reversion_exit
from vwap import VWAPEngine

ET = pytz.timezone("US/Eastern")


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(ET)


def _to_bars(raw: List[Dict]) -> List[Bar]:
    out: List[Bar] = []
    for r in raw:
        ts = r.get("timestamp")
        if isinstance(ts, str):
            ts = _parse_ts(ts)
        out.append(Bar(
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=int(r["volume"]),
            timestamp=ts,
        ))
    return out


@dataclass
class SimTrade:
    ticker: str
    strategy: str
    entry_ts: datetime
    entry_price: float
    exit_ts: Optional[datetime] = None
    exit_price: Optional[float] = None
    reason: str = ""

    @property
    def pnl_pct(self) -> float:
        if self.exit_price is None:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price


def _always_in_window(ts: datetime) -> bool:
    return True


def replay_ticker(ticker: str, bars: List[Bar], rvol: RVolBaseline) -> List[SimTrade]:
    vwap_eng = VWAPEngine()
    trades: List[SimTrade] = []
    open_trade: Optional[SimTrade] = None
    highest: float = 0.0
    entry_idx = 0

    # Build RVOL baseline from earlier bars if not already populated.
    for bar in bars:
        rvol.add_sample(ticker, bar.timestamp, bar.volume)

    # Run the replay.
    for i, bar in enumerate(bars):
        vwap_eng.update(bar.high, bar.low, bar.close, bar.volume)
        window = bars[: i + 1]
        price = bar.close
        rvol_val = rvol.rvol(ticker, bar.timestamp, bar.volume)

        if open_trade:
            bars_since = window[entry_idx:]
            if open_trade.exit_price is None:
                highest = max(highest, bar.high)
                if open_trade.strategy == "vwap_reversion":
                    reason = check_vwap_reversion_exit(
                        entry_price=open_trade.entry_price,
                        entry_time=open_trade.entry_ts,
                        price=price,
                        vwap_engine=vwap_eng,
                        ts=bar.timestamp,
                    )
                else:
                    reason = check_momentum_exit(
                        entry_price=open_trade.entry_price,
                        entry_time=open_trade.entry_ts,
                        highest_since_entry=highest,
                        price=price,
                        bars=bars_since,
                        rvol_value=rvol_val,
                        ts=bar.timestamp,
                    )
                if reason:
                    open_trade.exit_ts = bar.timestamp
                    open_trade.exit_price = price
                    open_trade.reason = reason
                    trades.append(open_trade)
                    open_trade = None
                    highest = 0.0
            continue

        # Entries — bypass the clock window for backtest purposes.
        import clock
        original = clock.within_trading_window
        clock.within_trading_window = _always_in_window
        try:
            signal = check_vwap_reversion(
                ticker=ticker,
                price=price,
                vwap_engine=vwap_eng,
                bars=window,
                rvol_value=rvol_val,
                has_position=False,
                ts=bar.timestamp,
            )
            if signal is None:
                signal = check_momentum_breakout(
                    ticker=ticker,
                    price=price,
                    vwap_engine=vwap_eng,
                    bars=window,
                    rvol_value=rvol_val,
                    has_position=False,
                    ts=bar.timestamp,
                )
        finally:
            clock.within_trading_window = original

        if signal:
            open_trade = SimTrade(
                ticker=ticker,
                strategy=signal.strategy,
                entry_ts=bar.timestamp,
                entry_price=price,
            )
            entry_idx = i
            highest = price

    return trades


def summarize(trades: List[SimTrade]) -> Dict:
    if not trades:
        return {"count": 0}
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    total_pnl = sum(t.pnl_pct for t in trades)
    avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0
    pf = (
        sum(t.pnl_pct for t in wins)
        / abs(sum(t.pnl_pct for t in losses))
        if losses and sum(t.pnl_pct for t in losses) != 0
        else float("inf")
    )
    return {
        "count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades),
        "avg_win_pct": avg_win * 100,
        "avg_loss_pct": avg_loss * 100,
        "total_pnl_pct": total_pnl * 100,
        "profit_factor": pf,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay historical bars through strategies")
    ap.add_argument("path", help="JSON file produced by data_collector.py")
    args = ap.parse_args()

    with open(args.path) as f:
        data = json.load(f)

    rvol = RVolBaseline()
    grand: List[SimTrade] = []
    for ticker, raw in data.items():
        bars = _to_bars(raw)
        trades = replay_ticker(ticker, bars, rvol)
        summary = summarize(trades)
        print(f"\n{ticker}: {summary}")
        for t in trades:
            print(
                f"  {t.strategy:<18} {t.entry_ts} -> {t.exit_ts} "
                f"{t.pnl_pct*100:+.2f}% ({t.reason})"
            )
        grand.extend(trades)

    print("\n=== OVERALL ===")
    print(summarize(grand))


if __name__ == "__main__":
    main()
