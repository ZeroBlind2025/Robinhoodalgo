"""
HTTP status server for the dashboard.

A minimal FastAPI app that runs in a background thread alongside the
main trading loop. It exposes read-only snapshots of the engine's
state and, if a built dashboard is present at `dashboard/dist/`,
serves it as static files from the same origin — so one Railway
service does everything with no CORS / Vercel needed.

API endpoints (all JSON):
  GET /health       — liveness check, no auth
  GET /state        — account value, P/L, trade count, flags
  GET /tickers      — per-ticker snapshots matching the dashboard shape
  GET /positions    — open positions keyed by symbol
  GET /trades       — in-memory trade memo for today

Static hosting:
  GET /             — dashboard/dist/index.html (if present)
  GET /assets/...   — compiled JS/CSS chunks
  The mount is conditional: if dashboard/dist doesn't exist the server
  runs API-only. This lets you run the engine locally without ever
  building the dashboard.

Auth: set DASHBOARD_TOKEN in the environment and send it as
`Authorization: Bearer <token>`. If DASHBOARD_TOKEN is empty, auth is
disabled (useful for local testing). Static files are always public —
the dashboard ships with no secrets baked in, all state comes from the
authenticated JSON endpoints.

CORS: `*` by default. Set DASHBOARD_ORIGIN to lock it down. Not
strictly needed when the dashboard is served from the same origin as
the API, but kept in place for external API clients.
"""

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from indicators import rsi
from logger import get_logger

log = get_logger("server")


def _check_auth(auth_header: Optional[str]) -> None:
    from fastapi import HTTPException

    if not config.DASHBOARD_TOKEN:
        return
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = auth_header.split(" ", 1)[1].strip()
    if token != config.DASHBOARD_TOKEN:
        raise HTTPException(401, "Invalid bearer token")


def _ticker_snapshot(scalper, ticker: str) -> Dict[str, Any]:
    ts_state = scalper.state.get(ticker)
    bars = ts_state.bars.snapshot()
    closes = [b.close for b in bars]

    # priceHistory: last 50 (price, vwap) pairs from the rolling trail.
    # Pad with the current price/vwap if we don't have enough history yet
    # so the dashboard's mini-chart has something to render.
    prices = list(ts_state.price_trail)
    vwaps = list(ts_state.vwap_trail)
    if len(prices) < 50 and ts_state.last_price > 0:
        filler_count = 50 - len(prices)
        prices = [ts_state.last_price] * filler_count + prices
        vwaps = [ts_state.vwap.vwap or ts_state.last_price] * filler_count + vwaps
    price_history = [
        {"price": p, "vwap": v}
        for p, v in zip(prices[-50:], vwaps[-50:])
    ]

    rsi_val = (
        rsi(closes, period=config.RSI_PERIOD)
        if len(closes) > config.RSI_PERIOD
        else 50.0
    )

    cb = ts_state.bars.current_bar
    rvol_val = (
        scalper.state.rvol.rvol(ticker, cb.timestamp, cb.volume) if cb else 0.0
    )

    q = ts_state.last_quote or {}
    last_price = ts_state.last_price
    bid = q.get("bid", last_price)
    ask = q.get("ask", last_price)

    return {
        "symbol": ticker,
        "price": last_price,
        "vwap": ts_state.vwap.vwap,
        "stdDev": ts_state.vwap.std_dev,
        "volume": q.get("volume", 0),
        "rvol": rvol_val,
        "rsi": rsi_val,
        "zScore": ts_state.vwap.z_score(last_price) if last_price else 0.0,
        "priceHistory": price_history,
        "bid": bid,
        "ask": ask,
        "dayChange": ts_state.day_change_pct,
    }


def _position_snapshot(scalper) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for sym, ts_state in scalper.state.tickers.items():
        p = ts_state.position
        if not p:
            continue
        out[sym] = {
            "entry": p.entry_price,
            "shares": p.quantity,
            "entryTime": int(p.entry_time.timestamp() * 1000),
            "strategy": "VWAP" if p.strategy == "vwap_reversion" else "MOM",
            "timeStr": p.entry_time.strftime("%H:%M"),
            "sl": p.stop_price,
            "tp": p.take_profit,
            "current": ts_state.last_price,
            "highest": p.highest_since_entry,
            "lowest": p.lowest_since_entry,
        }
    return out


def create_app(scalper):
    from fastapi import FastAPI, Header
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title="VWAP Scalper Status", version="1.0")

    origins = ["*"] if config.DASHBOARD_ORIGIN == "*" else [config.DASHBOARD_ORIGIN]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "running": scalper.running,
            "paperMode": config.PAPER_MODE,
        }

    @app.get("/state")
    def state(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
        _check_auth(authorization)
        total = scalper.risk.daily.trades_opened
        wins = scalper.win_count
        losses = max(0, total - wins)
        return {
            "accountValue": scalper._last_account_value,
            "dayPnl": scalper.risk.daily.realized_pnl,
            "startingEquity": scalper.risk.daily.starting_equity,
            "tradeCount": total,
            "winCount": wins,
            "lossCount": losses,
            "stoppedOut": scalper.risk.daily.stopped_out,
            "running": scalper.running,
            "paperMode": config.PAPER_MODE,
            "budget": config.BUDGET,
            "maxExposurePct": config.MAX_EXPOSURE_PCT,
            "maxExposure": config.BUDGET * config.MAX_EXPOSURE_PCT,
            "riskPerTrade": config.RISK_PER_TRADE,
            "tickers": list(config.TICKERS),
        }

    @app.get("/tickers")
    def tickers(authorization: Optional[str] = Header(None)) -> List[Dict[str, Any]]:
        _check_auth(authorization)
        return [_ticker_snapshot(scalper, t) for t in config.TICKERS]

    @app.get("/positions")
    def positions(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
        _check_auth(authorization)
        return _position_snapshot(scalper)

    @app.get("/trades")
    def trades(authorization: Optional[str] = Header(None)) -> List[Dict[str, Any]]:
        _check_auth(authorization)
        return scalper.recent_trades()

    # ------------------------------------------------------------------
    # Static dashboard mount (optional — only if the Vite build exists)
    # ------------------------------------------------------------------
    # Mounted AFTER all API routes so /state, /tickers etc take
    # precedence. html=True makes StaticFiles serve index.html on "/".
    dist_dir = Path(__file__).parent / "dashboard" / "dist"
    if dist_dir.exists() and (dist_dir / "index.html").exists():
        from fastapi.staticfiles import StaticFiles
        app.mount(
            "/",
            StaticFiles(directory=str(dist_dir), html=True),
            name="dashboard",
        )
        log.info("Serving dashboard from %s", dist_dir)
    else:
        log.info(
            "dashboard/dist not found — running API-only "
            "(dashboard will be built inside the Docker image)"
        )

    return app


def run_server_in_thread(scalper) -> threading.Thread:
    """
    Launch the FastAPI app in a daemon background thread. Returns the
    thread so the caller can inspect it if needed.
    """
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "uvicorn not installed — add `uvicorn` to requirements.txt"
        ) from exc

    app = create_app(scalper)

    def _serve() -> None:
        uvicorn.run(
            app,
            host=config.SERVER_HOST,
            port=config.SERVER_PORT,
            log_level="warning",
            access_log=False,
        )

    t = threading.Thread(target=_serve, daemon=True, name="status-server")
    t.start()
    log.info(
        "Status server listening on %s:%d (auth=%s, origin=%s)",
        config.SERVER_HOST,
        config.SERVER_PORT,
        "on" if config.DASHBOARD_TOKEN else "off",
        config.DASHBOARD_ORIGIN,
    )
    return t
