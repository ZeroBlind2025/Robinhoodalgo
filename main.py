"""
Main orchestration loop (Section 13).

Drives:
  - Robinhood session maintenance
  - Price polling
  - Bar / VWAP / RVOL updates
  - Strategy evaluation (VWAP reversion first, momentum second)
  - Order execution (paper or live)
  - Risk limits and end-of-day flatten
"""

import signal
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import auth
import clock
import config
from data import BarBuilder, get_quote_data, get_historicals
from executor import Executor, Fill, PaperExecutor, build_executor
from logger import TradeLogger, get_logger
from risk import RiskManager
from state import OpenPosition, StateManager, TickerState
from strategy_momentum import (
    MomentumSignal,
    check_momentum_breakout,
    check_momentum_exit,
)
from strategy_vwap import (
    VWAPSignal,
    check_vwap_reversion,
    check_vwap_reversion_exit,
)

log = get_logger("main")


class Scalper:
    def __init__(self):
        self.state = StateManager()
        self.state.init_tickers(config.TICKERS)
        self.executor: Executor = build_executor()
        self.risk = RiskManager()
        self.trade_log = TradeLogger()
        self.running = True
        self._current_day: Optional[str] = None

    # ------------------------------------------------------------------
    # startup
    # ------------------------------------------------------------------

    def start(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_term)
        signal.signal(signal.SIGINT, self._handle_term)

        if not auth.login():
            log.error("Unable to login to Robinhood. Exiting.")
            sys.exit(1)

        self._seed_rvol_baseline()
        log.info(
            "Scalper started (paper=%s, tickers=%s)",
            config.PAPER_MODE, config.TICKERS,
        )
        self.loop()

    def _handle_term(self, signum, frame) -> None:
        log.info("Received signal %s, shutting down", signum)
        self.running = False

    def _seed_rvol_baseline(self) -> None:
        """Use the 5-minute historical bars to seed a coarse baseline."""
        if config.PAPER_MODE and not (
            config.RH_USERNAME and config.RH_PASSWORD
        ):
            log.info("Skipping RVOL baseline seed (no credentials in paper mode)")
            return
        from datetime import timedelta
        for ticker in config.TICKERS:
            bars = get_historicals(ticker, interval="5minute", span="week")
            for bar in bars:
                try:
                    ts = datetime.fromisoformat(bar["timestamp"].replace("Z", "+00:00"))
                except Exception:  # noqa: BLE001
                    continue
                minute_et = ts.astimezone(clock.ET).replace(second=0, microsecond=0)
                # 5-minute bar volume → distribute across the 5 minutes it covers.
                per_minute = bar["volume"] / 5.0
                for offset in range(5):
                    self.state.rvol.add_sample(
                        ticker, minute_et + timedelta(minutes=offset), per_minute
                    )
            log.info("Seeded RVOL baseline for %s with %d bars", ticker, len(bars))

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------

    def loop(self) -> None:
        while self.running:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                log.exception("Unhandled error in tick: %s", exc)
            time.sleep(config.POLL_INTERVAL)

    def tick(self) -> None:
        now = clock.now_et()
        today = now.strftime("%Y-%m-%d")

        # Daily lifecycle
        if self._current_day != today:
            self._start_new_day(now, today)

        # Outside market hours → idle
        if not clock.is_market_open(now):
            time.sleep(30)
            return

        # Ensure authenticated session is still alive.
        if not config.PAPER_MODE:
            auth.ensure_session()

        # Poll quotes first so we can mark open positions even in the buffer.
        quotes: Dict[str, Dict] = {}
        for ticker in config.TICKERS:
            q = get_quote_data(ticker) if not config.PAPER_MODE or (
                config.RH_USERNAME and config.RH_PASSWORD
            ) else None
            if q is None:
                continue
            quotes[ticker] = q
            self._ingest_quote(ticker, q, now)

        # EOD flatten
        if clock.should_flatten(now):
            self._flatten_all(quotes)
            return

        # Daily loss check
        if self.risk.daily_loss_exceeded():
            if not self.risk.daily.stopped_out:
                log.warning("Daily loss limit hit — flattening and halting")
                self.risk.daily.stopped_out = True
                self.risk.save()
                self._flatten_all(quotes)
            return

        # First 15 minutes: only accumulate data, no trades.
        if not clock.within_trading_window(now):
            return

        # 1) Exits on existing positions
        for ticker in config.TICKERS:
            ts_state = self.state.get(ticker)
            if ts_state.position and ticker in quotes:
                self._evaluate_exit(ts_state, quotes[ticker])

        # 2) Entries
        account_value = self._account_value(quotes)
        if not self.risk.can_open_new_position(account_value, self.state.count_open()):
            return

        for ticker in config.TICKERS:
            if ticker not in quotes:
                continue
            ts_state = self.state.get(ticker)
            if ts_state.position:
                continue
            self._evaluate_entry(ts_state, quotes[ticker], account_value, now)
            if not self.risk.can_open_new_position(account_value, self.state.count_open()):
                break

    # ------------------------------------------------------------------
    # daily lifecycle
    # ------------------------------------------------------------------

    def _start_new_day(self, now: datetime, today: str) -> None:
        log.info("New trading day %s", today)
        self.state.reset_day()
        account_value = self._account_value({})
        if account_value <= 0:
            account_value = config.PAPER_STARTING_CASH
        self.risk.begin_day(today, account_value)
        self._current_day = today

    # ------------------------------------------------------------------
    # quote ingestion
    # ------------------------------------------------------------------

    def _ingest_quote(self, ticker: str, quote: Dict, now: datetime) -> None:
        ts_state = self.state.get(ticker)
        price = quote["price"]
        vol = quote.get("volume", 0)
        ts_state.last_price = price
        ts_state.last_price_time = now

        if isinstance(self.executor, PaperExecutor):
            self.executor.set_mark(ticker, price)

        finished = ts_state.bars.update(price, vol, now)
        if finished is not None:
            # New closed bar → update RVOL baseline and VWAP.
            self.state.rvol.add_sample(ticker, finished.timestamp, finished.volume)
            ts_state.update_vwap_from_new_bars()

        # Mark open position highs/lows.
        if ts_state.position:
            ts_state.position.mark(price)

    # ------------------------------------------------------------------
    # strategy evaluation
    # ------------------------------------------------------------------

    def _rvol(self, ticker: str, ts_state: TickerState, now: datetime) -> float:
        if ts_state.bars.current_bar is None:
            return 0.0
        minute = ts_state.bars.current_bar.timestamp
        return self.state.rvol.rvol(ticker, minute, ts_state.bars.current_bar.volume)

    def _evaluate_entry(
        self,
        ts_state: TickerState,
        quote: Dict,
        account_value: float,
        now: datetime,
    ) -> None:
        bars = ts_state.bars.snapshot()
        if len(bars) < 4:
            return
        price = quote["price"]
        rvol_value = self._rvol(ts_state.ticker, ts_state, now)

        # VWAP reversion first (higher conviction)
        signal = check_vwap_reversion(
            ticker=ts_state.ticker,
            price=price,
            vwap_engine=ts_state.vwap,
            bars=bars,
            rvol_value=rvol_value,
            has_position=False,
            ts=now,
        )
        if signal is None:
            signal = check_momentum_breakout(
                ticker=ts_state.ticker,
                price=price,
                vwap_engine=ts_state.vwap,
                bars=bars,
                rvol_value=rvol_value,
                has_position=False,
                ts=now,
            )
        if signal is None:
            return

        qty = self.risk.size(account_value, signal.price, signal.stop_pct)
        if qty <= 0:
            log.info("Signal on %s skipped: qty=0", ts_state.ticker)
            return

        limit_price = round(quote.get("bid", price) + 0.01, 2)
        fill = self.executor.buy_limit(ts_state.ticker, qty, limit_price)
        if not fill or fill.quantity <= 0:
            log.info("Entry not filled for %s", ts_state.ticker)
            return

        ts_state.position = OpenPosition(
            ticker=ts_state.ticker,
            strategy=signal.strategy,
            side="long",
            quantity=fill.quantity,
            entry_price=fill.price,
            entry_time=now,
            stop_price=fill.price * (1 - signal.stop_pct),
            take_profit=signal.take_profit,
            max_hold_minutes=signal.max_hold_minutes,
            highest_since_entry=fill.price,
            lowest_since_entry=fill.price,
        )
        self.risk.record_open()
        self.trade_log.record("entry", {
            "ticker": ts_state.ticker,
            "strategy": signal.strategy,
            "qty": fill.quantity,
            "price": fill.price,
            "reason": signal.reason,
            "vwap": signal.vwap,
            "rsi": signal.rsi,
            "rvol": signal.rvol,
        })

    def _evaluate_exit(self, ts_state: TickerState, quote: Dict) -> None:
        pos = ts_state.position
        if not pos:
            return
        price = quote["price"]
        now = clock.now_et()
        bars = ts_state.bars.snapshot()
        rvol_value = self._rvol(ts_state.ticker, ts_state, now)

        if pos.strategy == "vwap_reversion":
            reason = check_vwap_reversion_exit(
                entry_price=pos.entry_price,
                entry_time=pos.entry_time,
                price=price,
                vwap_engine=ts_state.vwap,
                ts=now,
            )
        else:
            reason = check_momentum_exit(
                entry_price=pos.entry_price,
                entry_time=pos.entry_time,
                highest_since_entry=pos.highest_since_entry,
                price=price,
                bars=bars,
                rvol_value=rvol_value,
                ts=now,
            )

        if reason is None:
            return

        self._close_position(ts_state, quote, reason)

    def _close_position(
        self,
        ts_state: TickerState,
        quote: Optional[Dict],
        reason: str,
    ) -> None:
        pos = ts_state.position
        if not pos:
            return

        use_market = reason in (
            "stop_loss",
            "stop_lower_2sigma",
            "time_stop",
            "eod_flatten",
            "volume_fade",
            "daily_loss_halt",
        )
        price_hint = quote["price"] if quote else pos.entry_price
        limit_price = round((quote.get("ask", price_hint) - 0.01) if quote else price_hint, 2)

        if use_market:
            fill = self.executor.sell_market(ts_state.ticker, pos.quantity)
        else:
            fill = self.executor.sell_limit(ts_state.ticker, pos.quantity, limit_price)
            if not fill:
                log.info("Limit exit not filled on %s, falling back to market", ts_state.ticker)
                fill = self.executor.sell_market(ts_state.ticker, pos.quantity)

        if not fill:
            log.error("Failed to close %s — position still open!", ts_state.ticker)
            return

        pnl = (fill.price - pos.entry_price) * fill.quantity
        self.risk.record_close(pnl)
        self.risk.pdt.record_day_trade()
        self.risk.save()

        self.trade_log.record("exit", {
            "ticker": ts_state.ticker,
            "strategy": pos.strategy,
            "qty": fill.quantity,
            "entry": pos.entry_price,
            "exit": fill.price,
            "pnl": pnl,
            "reason": reason,
            "hold_seconds": (clock.now_et() - pos.entry_time).total_seconds(),
        })
        ts_state.position = None

    def _flatten_all(self, quotes: Dict[str, Dict]) -> None:
        for ticker in list(self.state.tickers):
            ts_state = self.state.get(ticker)
            if ts_state.position:
                self._close_position(ts_state, quotes.get(ticker), "eod_flatten")
        self.executor.cancel_all()

    def _account_value(self, quotes: Dict[str, Dict]) -> float:
        if isinstance(self.executor, PaperExecutor):
            for ticker, q in quotes.items():
                self.executor.set_mark(ticker, q["price"])
            return self.executor.get_account_value()
        val = self.executor.get_account_value()
        return val or 0.0


def main() -> None:
    Scalper().start()


if __name__ == "__main__":
    main()
