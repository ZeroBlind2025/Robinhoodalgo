"""
Order execution — live Robinhood path and paper simulator (Section 8.3 / 14.3).

Exposes a common `Executor` interface so the main loop doesn't care which
backend is in use.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol

import config
from logger import get_logger

log = get_logger("executor")


def _robin_stocks():
    import robin_stocks.robinhood as rh  # type: ignore
    return rh


@dataclass
class Fill:
    ticker: str
    side: str  # 'buy' or 'sell'
    quantity: float
    price: float
    timestamp: datetime
    order_id: Optional[str] = None
    note: str = ""


class Executor(Protocol):
    def buy_limit(self, ticker: str, qty: float, price: float) -> Optional[Fill]: ...
    def sell_limit(self, ticker: str, qty: float, price: float) -> Optional[Fill]: ...
    def buy_market(self, ticker: str, qty: float) -> Optional[Fill]: ...
    def sell_market(self, ticker: str, qty: float) -> Optional[Fill]: ...
    def cancel_all(self) -> None: ...
    def get_positions(self) -> Dict[str, Dict]: ...
    def get_account_value(self) -> float: ...


# ---------------------------------------------------------------------------
# Live Robinhood executor
# ---------------------------------------------------------------------------


class LiveExecutor:
    """
    Robinhood order execution using FRACTIONAL shares.

    Robinhood restricts fractional orders to market orders — there is no
    fractional limit-order API. At $12.50 per trade on CRWV/NBIS/SMCI
    (20M+ daily volume), slippage from a market order is a handful of
    cents, well below the strategy's 0.3-0.8% targets, so we accept that
    tradeoff in exchange for fractional share sizing.

    buy_limit / sell_limit are therefore aliased to the market path so
    the strategy layer doesn't have to care.
    """

    def __init__(self):
        self._open_order_ids: List[str] = []

    def _wait_for_fill(self, order_id: str, timeout: int) -> Optional[Fill]:
        rh = _robin_stocks()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                info = rh.orders.get_stock_order_info(order_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("get_stock_order_info %s failed: %s", order_id, exc)
                info = None
            if info:
                state = info.get("state")
                if state == "filled":
                    qty = float(info.get("cumulative_quantity") or info.get("quantity") or 0)
                    avg = float(info.get("average_price") or 0)
                    side = info.get("side", "buy")
                    symbol = info.get("symbol", "")
                    if order_id in self._open_order_ids:
                        self._open_order_ids.remove(order_id)
                    return Fill(
                        ticker=symbol,
                        side=side,
                        quantity=qty,
                        price=avg,
                        timestamp=datetime.now(timezone.utc),
                        order_id=order_id,
                    )
                if state in ("cancelled", "canceled", "rejected", "failed"):
                    if order_id in self._open_order_ids:
                        self._open_order_ids.remove(order_id)
                    return None
            time.sleep(1.0)
        return None

    def buy_limit(self, ticker: str, qty: float, price: float) -> Optional[Fill]:
        # Fractional shares don't support limit orders on Robinhood.
        return self._place_fractional("buy", ticker, qty)

    def sell_limit(self, ticker: str, qty: float, price: float) -> Optional[Fill]:
        return self._place_fractional("sell", ticker, qty)

    def _place_fractional(self, side: str, ticker: str, qty: float) -> Optional[Fill]:
        """
        Place a fractional-share market order via
        `order_buy_fractional_by_quantity` / `order_sell_fractional_by_quantity`.
        """
        if qty <= 0:
            return None

        rh = _robin_stocks()
        # Round to 6dp — Robinhood's fractional precision
        q = round(float(qty), 6)

        try:
            if side == "buy":
                result = rh.orders.order_buy_fractional_by_quantity(
                    symbol=ticker,
                    quantity=q,
                    timeInForce="gfd",
                    extendedHours=False,
                )
            else:
                result = rh.orders.order_sell_fractional_by_quantity(
                    symbol=ticker,
                    quantity=q,
                    timeInForce="gfd",
                    extendedHours=False,
                )
        except Exception as exc:  # noqa: BLE001
            log.error("fractional %s %s qty=%s failed: %s", side, ticker, q, exc)
            return None

        if not result or "id" not in result:
            log.warning("fractional %s %s got no order id: %s", side, ticker, result)
            return None

        order_id = result["id"]
        self._open_order_ids.append(order_id)
        filled = self._wait_for_fill(order_id, timeout=max(config.ORDER_TIMEOUT, 60))
        if not filled:
            # Market orders should always fill; if not, try to cancel and move on
            try:
                rh.orders.cancel_stock_order(order_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("cancel_stock_order %s failed: %s", order_id, exc)
        return filled

    def buy_market(self, ticker: str, qty: float) -> Optional[Fill]:
        return self._place_fractional("buy", ticker, qty)

    def sell_market(self, ticker: str, qty: float) -> Optional[Fill]:
        return self._place_fractional("sell", ticker, qty)

    # -- maintenance ------------------------------------------------------

    def cancel_all(self) -> None:
        rh = _robin_stocks()
        try:
            for o in rh.orders.get_all_open_stock_orders() or []:
                oid = o.get("id")
                if oid:
                    try:
                        rh.orders.cancel_stock_order(oid)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("cancel %s failed: %s", oid, exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("cancel_all failed: %s", exc)
        self._open_order_ids.clear()

    def get_positions(self) -> Dict[str, Dict]:
        from data import get_live_positions
        return get_live_positions()

    def get_account_value(self) -> float:
        from data import get_account_value
        val = get_account_value()
        return val or 0.0


# ---------------------------------------------------------------------------
# Paper executor (Section 14.3)
# ---------------------------------------------------------------------------


@dataclass
class PaperPosition:
    quantity: float
    avg_cost: float


@dataclass
class PaperExecutor:
    cash: float = field(default_factory=lambda: config.PAPER_STARTING_CASH)
    positions: Dict[str, PaperPosition] = field(default_factory=dict)
    fills: List[Fill] = field(default_factory=list)

    def _buy(self, ticker: str, qty: float, price: float, note: str) -> Optional[Fill]:
        cost = qty * price
        if qty <= 0 or cost > self.cash:
            log.warning("Paper buy rejected %s qty=%s cost=%.2f cash=%.2f",
                        ticker, qty, cost, self.cash)
            return None
        self.cash -= cost
        pos = self.positions.get(ticker)
        if pos:
            total_cost = pos.quantity * pos.avg_cost + cost
            pos.quantity += qty
            pos.avg_cost = total_cost / pos.quantity
        else:
            self.positions[ticker] = PaperPosition(quantity=qty, avg_cost=price)
        fill = Fill(
            ticker=ticker,
            side="buy",
            quantity=qty,
            price=price,
            timestamp=datetime.now(timezone.utc),
            note=note,
        )
        self.fills.append(fill)
        return fill

    def _sell(self, ticker: str, qty: float, price: float, note: str) -> Optional[Fill]:
        pos = self.positions.get(ticker)
        if not pos or pos.quantity < qty:
            log.warning("Paper sell rejected %s qty=%s held=%s",
                        ticker, qty, pos.quantity if pos else 0)
            return None
        self.cash += qty * price
        pos.quantity -= qty
        if pos.quantity <= 1e-9:
            del self.positions[ticker]
        fill = Fill(
            ticker=ticker,
            side="sell",
            quantity=qty,
            price=price,
            timestamp=datetime.now(timezone.utc),
            note=note,
        )
        self.fills.append(fill)
        return fill

    def buy_limit(self, ticker: str, qty: float, price: float) -> Optional[Fill]:
        return self._buy(ticker, qty, price, note="limit")

    def sell_limit(self, ticker: str, qty: float, price: float) -> Optional[Fill]:
        return self._sell(ticker, qty, price, note="limit")

    def buy_market(self, ticker: str, qty: float) -> Optional[Fill]:
        # The caller passes the last known price via a side channel; for paper
        # mode we expect it stashed in `self._last_mark`.
        price = self._last_mark.get(ticker, 0.0) if hasattr(self, "_last_mark") else 0.0
        if price <= 0:
            log.warning("Paper market buy rejected %s — no mark price", ticker)
            return None
        return self._buy(ticker, qty, price, note="market")

    def sell_market(self, ticker: str, qty: float) -> Optional[Fill]:
        price = self._last_mark.get(ticker, 0.0) if hasattr(self, "_last_mark") else 0.0
        if price <= 0:
            log.warning("Paper market sell rejected %s — no mark price", ticker)
            return None
        return self._sell(ticker, qty, price, note="market")

    def cancel_all(self) -> None:
        # Paper executor is synchronous; no resting orders.
        return None

    def get_positions(self) -> Dict[str, Dict]:
        return {
            t: {"quantity": p.quantity, "avg_cost": p.avg_cost}
            for t, p in self.positions.items()
        }

    def get_account_value(self) -> float:
        mark: Dict[str, float] = getattr(self, "_last_mark", {})
        equity = self.cash
        for t, p in self.positions.items():
            equity += p.quantity * mark.get(t, p.avg_cost)
        return equity

    # Helper for the main loop to stash the last known prices so market
    # orders in paper mode can be priced.
    def set_mark(self, ticker: str, price: float) -> None:
        if not hasattr(self, "_last_mark"):
            self._last_mark = {}  # type: ignore[attr-defined]
        self._last_mark[ticker] = price


def build_executor() -> Executor:
    if config.PAPER_MODE:
        log.info("Using PaperExecutor (PAPER_MODE=true)")
        return PaperExecutor()
    log.info("Using LiveExecutor")
    return LiveExecutor()
