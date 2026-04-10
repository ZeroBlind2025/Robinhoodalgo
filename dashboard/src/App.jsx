/*
 * VWAP Scalper dashboard — wired to the Python engine's HTTP API.
 *
 * Polls the engine every 2 seconds for:
 *   GET /state      account value, day P/L, trade count, budget, flags
 *   GET /tickers    per-ticker quote + VWAP + RSI + RVOL snapshots
 *   GET /positions  open positions keyed by symbol
 *   GET /trades     in-memory trade memo for today
 *
 * Configuration (via Vite env vars):
 *   VITE_API_URL    default "" = same-origin. Set to the engine's
 *                   public URL if the dashboard is deployed separately.
 *   VITE_API_TOKEN  matches DASHBOARD_TOKEN on the engine, empty = no auth
 *
 * When deployed inside the Python engine's FastAPI server (single
 * Railway service), leave VITE_API_URL unset. The bundle will hit
 * /state, /tickers, etc. on the current origin.
 */

import { useState, useEffect, useCallback, useRef } from "react";

// ─── Config ───────────────────────────────────────────────────────────
// Default to "" (same-origin) so the dashboard works when served from
// the Python FastAPI engine on the same host. Override VITE_API_URL at
// build time for split deployments (engine on Railway, dashboard on
// Vercel, etc).
const API_URL = (import.meta.env && import.meta.env.VITE_API_URL) || "";
const API_TOKEN = (import.meta.env && import.meta.env.VITE_API_TOKEN) || "";
const POLL_MS = 2000;

const TICKERS = ["CRWV", "NBIS", "SMCI"];

// ─── Fonts / formatting ──────────────────────────────────────────────
const mono = "'JetBrains Mono', 'SF Mono', monospace";
const display = "'Space Mono', monospace";
const fmt = (n, d = 2) => Number(n ?? 0).toFixed(d);
const fmtSh = (n) => {
  const v = Number(n ?? 0);
  return v >= 1 ? v.toFixed(2) : v.toFixed(4);
};
const pc = (v) => (v >= 0 ? "#10b981" : "#ef4444");
const ps = (v) => (v >= 0 ? "+" : "");

// ─── API helpers ─────────────────────────────────────────────────────
async function apiGet(path) {
  const headers = { "Content-Type": "application/json" };
  if (API_TOKEN) headers.Authorization = `Bearer ${API_TOKEN}`;
  const res = await fetch(`${API_URL}${path}`, { headers });
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json();
}

// ─── Market Clock ────────────────────────────────────────────────────
function getMarketState() {
  const now = new Date();
  const etOffset = -4;
  const etSec =
    (((now.getUTCHours() + 24 + etOffset) % 24) * 3600) +
    now.getUTCMinutes() * 60 +
    now.getUTCSeconds();
  const openSec = 9 * 3600 + 30 * 60;
  const closeSec = 16 * 3600;
  const day = now.getUTCDay();
  const etDay = now.getUTCHours() + etOffset < 0 ? (day + 6) % 7 : day;
  const isWd = etDay >= 1 && etDay <= 5;
  const h = Math.floor(etSec / 3600);
  const m = Math.floor((etSec % 3600) / 60);
  const s = etSec % 60;
  const etTime = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;

  if (!isWd) {
    const dToMon = etDay === 0 ? 1 : etDay === 6 ? 2 : 0;
    return { status: "closed", cd: Math.max(0, dToMon * 86400 + openSec - etSec), label: "MARKET OPENS", etTime };
  }
  if (etSec < openSec) return { status: "pre", cd: openSec - etSec, label: "MARKET OPENS", etTime };
  if (etSec < closeSec) return { status: "open", cd: closeSec - etSec, label: "MARKET CLOSES", etTime };
  return { status: "after", cd: 86400 - etSec + openSec, label: "MARKET OPENS", etTime };
}

function fmtCD(t) {
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function MarketClock() {
  const [st, setSt] = useState(getMarketState);
  useEffect(() => {
    const iv = setInterval(() => setSt(getMarketState()), 1000);
    return () => clearInterval(iv);
  }, []);
  const cfg = {
    open: { c: "#10b981", t: "MARKET OPEN", bg: "#041f14", glow: "0 0 12px rgba(16,185,129,0.4)" },
    pre: { c: "#f59e0b", t: "PRE-MARKET", bg: "#1a1400", glow: "0 0 8px rgba(245,158,11,0.3)" },
    after: { c: "#8b5cf6", t: "AFTER HOURS", bg: "#0f0a1e", glow: "none" },
    closed: { c: "#ef4444", t: "WEEKEND", bg: "#1a0a0a", glow: "none" },
  }[st.status];

  return (
    <div style={{ background: cfg.bg, border: `1px solid ${cfg.c}33`, borderRadius: 6, padding: "10px 14px", marginBottom: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{ width: 7, height: 7, borderRadius: "50%", background: cfg.c, boxShadow: cfg.glow, animation: st.status === "open" ? "pulse 2s infinite" : "none" }} />
        <span style={{ fontSize: 10, color: cfg.c, fontFamily: mono, fontWeight: 700, letterSpacing: "1px" }}>{cfg.t}</span>
        <span style={{ fontSize: 10, color: "#444", fontFamily: mono }}>{st.etTime} ET</span>
      </div>
      <div style={{ textAlign: "right" }}>
        <div style={{ fontSize: 8, color: "#555", fontFamily: mono, letterSpacing: "0.5px" }}>{st.label}</div>
        <div style={{ fontSize: 18, fontWeight: 700, color: cfg.c, fontFamily: display, letterSpacing: "2px", textShadow: st.status === "open" ? `0 0 6px ${cfg.c}44` : "none" }}>{fmtCD(st.cd)}</div>
      </div>
      <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}`}</style>
    </div>
  );
}

// ─── Small primitives ────────────────────────────────────────────────
function Badge({ text, color, bg }) {
  return (
    <span style={{ background: bg, border: `1px solid ${color}`, color, padding: "1px 6px", borderRadius: 3, fontSize: 9, fontWeight: 700, letterSpacing: ".5px", fontFamily: mono }}>
      {text}
    </span>
  );
}

function Met({ label, value, sub, sc }) {
  return (
    <div>
      <div style={{ fontSize: 8, color: "#555", fontFamily: mono, letterSpacing: ".5px", marginBottom: 1 }}>{label}</div>
      <div style={{ fontSize: 12, color: "#e2e8f0", fontWeight: 600, fontFamily: mono }}>{value}</div>
      {sub && <div style={{ fontSize: 8, color: sc || "#555", fontFamily: mono, fontWeight: 700 }}>{sub}</div>}
    </div>
  );
}

function MiniChart({ data, w = 200, h = 40, color = "#10b981" }) {
  if (!data || data.length < 2) return null;
  const ps2 = data.map((d) => d.price);
  const vs = data.map((d) => d.vwap);
  const all = [...ps2, ...vs];
  const mn = Math.min(...all);
  const mx = Math.max(...all);
  const rng = mx - mn || 1;
  const path = (a) =>
    a
      .map((v, i) => `${i === 0 ? "M" : "L"}${((i / (a.length - 1)) * w).toFixed(1)},${(2 + (1 - (v - mn) / rng) * (h - 4)).toFixed(1)}`)
      .join(" ");
  return (
    <svg width={w} height={h} style={{ display: "block" }}>
      <path d={path(vs)} fill="none" stroke="#333" strokeWidth="1" strokeDasharray="3,3" />
      <path d={path(ps2)} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}

function ZBar({ z }) {
  const cl = Math.max(-3.5, Math.min(3.5, z || 0));
  const pct = ((cl + 3.5) / 7) * 100;
  const c = Math.abs(cl) > 2 ? "#ef4444" : Math.abs(cl) > 1.5 ? "#f59e0b" : "#10b981";
  return (
    <div style={{ position: "relative", height: 5, background: "#111", borderRadius: 3, overflow: "hidden", width: "100%" }}>
      <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: "#222" }} />
      <div style={{ position: "absolute", left: cl >= 0 ? "50%" : `${pct}%`, width: `${Math.abs(pct - 50)}%`, top: 0, bottom: 0, background: c, borderRadius: 3, transition: "all .3s" }} />
    </div>
  );
}

// ─── Open Positions ──────────────────────────────────────────────────
function OpenPos({ positions, tData }) {
  const ents = Object.entries(positions || {}).filter(([, p]) => p && p.shares > 0);
  const totPnl = ents.reduce((s, [sym, p]) => s + ((tData[sym]?.price || p.entry) - p.entry) * p.shares, 0);
  const totExp = ents.reduce((s, [sym, p]) => s + p.shares * (tData[sym]?.price || p.entry), 0);
  return (
    <div style={{ background: "#0a1628", border: "1px solid #1e3a5f", borderRadius: 6, padding: 12, marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: ents.length ? 8 : 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 10, color: "#3b82f6", fontFamily: mono, fontWeight: 700, letterSpacing: "1px" }}>OPEN POSITIONS</span>
          <span style={{ background: ents.length ? "#1e3a5f" : "#111", color: ents.length ? "#93c5fd" : "#333", padding: "0 5px", borderRadius: 8, fontSize: 9, fontWeight: 700, fontFamily: mono }}>{ents.length}</span>
        </div>
        {ents.length > 0 && (
          <div style={{ fontSize: 9, fontFamily: mono, color: "#64748b" }}>
            EXP ${fmt(totExp)} · P/L <span style={{ color: pc(totPnl), fontWeight: 700 }}>{ps(totPnl)}${fmt(totPnl, 3)}</span>
          </div>
        )}
      </div>
      {ents.length === 0 ? (
        <div style={{ fontSize: 9, color: "#1e3a5f", fontFamily: mono, textAlign: "center", padding: "6px 0" }}>NO OPEN POSITIONS</div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "48px 60px 52px 60px 44px 70px 60px 1fr", gap: 2, fontSize: 7, color: "#334155", fontFamily: mono, fontWeight: 700, paddingBottom: 3, borderBottom: "1px solid #152238" }}>
            <div>TICKER</div><div>ENTRY</div><div>SHARES</div><div>CURRENT</div><div>HOLD</div><div>SL / TP</div><div>P/L $</div><div>P/L %</div>
          </div>
          {ents.map(([sym, pos]) => {
            const cur = tData[sym]?.price || pos.entry;
            const pnl = (cur - pos.entry) * pos.shares;
            const pnlP = ((cur - pos.entry) / pos.entry) * 100;
            const hold = Math.max(0, Math.floor((Date.now() - pos.entryTime) / 60000));
            return (
              <div key={sym} style={{ display: "grid", gridTemplateColumns: "48px 60px 52px 60px 44px 70px 60px 1fr", gap: 2, fontSize: 10, fontFamily: mono, padding: "5px 0", borderBottom: "1px solid #0d1a2e" }}>
                <div style={{ color: "#e2e8f0", fontWeight: 700 }}>{sym}</div>
                <div style={{ color: "#94a3b8" }}>${fmt(pos.entry)}</div>
                <div style={{ color: "#64748b" }}>{fmtSh(pos.shares)}</div>
                <div style={{ color: "#e2e8f0" }}>${fmt(cur)}</div>
                <div style={{ color: "#475569" }}>{hold}m</div>
                <div style={{ color: "#334155", fontSize: 8 }}>{fmt(pos.sl)}/{fmt(pos.tp)}</div>
                <div style={{ color: pc(pnl), fontWeight: 700 }}>{ps(pnl)}${fmt(pnl, 3)}</div>
                <div style={{ color: pc(pnlP) }}>{ps(pnlP)}{fmt(pnlP, 2)}%</div>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}

// ─── Closed Trades Today ─────────────────────────────────────────────
function ClosedToday({ trades }) {
  const closed = (trades || []).filter((t) => t.closed && t.pnl != null);
  const totPnl = closed.reduce((s, t) => s + t.pnl, 0);
  const wins = closed.filter((t) => t.pnl > 0).length;
  return (
    <div style={{ background: "#0d0d14", border: "1px solid #1a1a2e", borderRadius: 6, padding: 12, marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: closed.length ? 8 : 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 10, color: "#666", fontFamily: mono, fontWeight: 700, letterSpacing: "1px" }}>CLOSED TODAY</span>
          <span style={{ background: "#1a1a2e", color: "#555", padding: "0 5px", borderRadius: 8, fontSize: 9, fontWeight: 700, fontFamily: mono }}>{closed.length}</span>
        </div>
        {closed.length > 0 && (
          <div style={{ fontSize: 9, fontFamily: mono, color: "#64748b" }}>
            NET <span style={{ color: pc(totPnl), fontWeight: 700 }}>{ps(totPnl)}${fmt(totPnl, 3)}</span> · W/L {wins}/{closed.length - wins}
          </div>
        )}
      </div>
      {closed.length === 0 ? (
        <div style={{ fontSize: 9, color: "#222", fontFamily: mono, textAlign: "center", padding: "6px 0" }}>NO CLOSED TRADES TODAY</div>
      ) : (
        <div style={{ maxHeight: 180, overflowY: "auto" }}>
          <div style={{ display: "grid", gridTemplateColumns: "40px 44px 44px 52px 52px 52px 52px 52px 36px", gap: 2, fontSize: 7, color: "#333", fontFamily: mono, fontWeight: 700, paddingBottom: 3, borderBottom: "1px solid #111", position: "sticky", top: 0, background: "#0d0d14" }}>
            <div>TIME</div><div>TICKER</div><div>STRAT</div><div>ENTRY</div><div>EXIT</div><div>SH</div><div>P/L $</div><div>P/L %</div><div>HOLD</div>
          </div>
          {closed.slice().reverse().map((t, i) => {
            const ep = t.entry ?? t.price;
            const pp = ep > 0 ? ((t.price - ep) / ep) * 100 : 0;
            return (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "40px 44px 44px 52px 52px 52px 52px 52px 36px", gap: 2, fontSize: 9, fontFamily: mono, padding: "4px 0", borderBottom: "1px solid #0a0a14" }}>
                <div style={{ color: "#444" }}>{t.time}</div>
                <div style={{ color: "#94a3b8", fontWeight: 700 }}>{t.symbol}</div>
                <div>
                  <Badge
                    text={t.strategy || "—"}
                    color={t.strategy === "VWAP" ? "#10b981" : "#3b82f6"}
                    bg={t.strategy === "VWAP" ? "#041f14" : "#0a1628"}
                  />
                </div>
                <div style={{ color: "#555" }}>${fmt(ep)}</div>
                <div style={{ color: "#94a3b8" }}>${fmt(t.price)}</div>
                <div style={{ color: "#444" }}>{fmtSh(t.shares)}</div>
                <div style={{ color: pc(t.pnl), fontWeight: 700 }}>{ps(t.pnl)}${fmt(t.pnl, 3)}</div>
                <div style={{ color: pc(pp) }}>{ps(pp)}{fmt(pp, 2)}%</div>
                <div style={{ color: "#444" }}>{t.holdMins ?? 0}m</div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Ticker Card ─────────────────────────────────────────────────────
function TCard({ data, maxExposure }) {
  const sh = data.price > 0 ? maxExposure / data.price : 0;
  const sig = data.zScore < -1.5 && data.rsi < 30 ? "VWAP" : data.rvol > 2.5 && data.price > data.vwap ? "MOM" : null;
  return (
    <div style={{ background: "#0d0d14", border: "1px solid #1a1a2e", borderRadius: 6, padding: 12, marginBottom: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontFamily: display, fontSize: 14, fontWeight: 700, color: "#e2e8f0" }}>{data.symbol}</span>
            {sig && <Badge text={sig === "VWAP" ? "VWAP LONG" : "MOMENTUM"} color={sig === "VWAP" ? "#10b981" : "#3b82f6"} bg={sig === "VWAP" ? "#064e3b" : "#1e3a5f"} />}
          </div>
          <div style={{ fontFamily: mono, fontSize: 18, fontWeight: 700, color: "#f8fafc" }}>${fmt(data.price)}</div>
          <span style={{ fontSize: 9, color: pc(data.dayChange), fontFamily: mono }}>{ps(data.dayChange)}{fmt(data.dayChange)}%</span>
        </div>
        <MiniChart data={data.priceHistory} color={pc(data.dayChange)} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr 1fr", gap: 4, marginBottom: 6 }}>
        <Met label="VWAP" value={`$${fmt(data.vwap)}`} sub={data.price > data.vwap ? "ABOVE" : "BELOW"} sc={data.price > data.vwap ? "#10b981" : "#ef4444"} />
        <Met label="RSI(7)" value={fmt(data.rsi, 1)} sub={data.rsi < 30 ? "OVERSOLD" : data.rsi > 70 ? "OVERBOUGHT" : "—"} sc={data.rsi < 30 ? "#10b981" : data.rsi > 70 ? "#ef4444" : "#333"} />
        <Met label="RVOL" value={`${fmt(data.rvol, 1)}x`} sub={data.rvol > 2.5 ? "SPIKE" : data.rvol < 0.5 ? "DEAD" : "—"} sc={data.rvol > 2.5 ? "#f59e0b" : "#333"} />
        <Met label="SPREAD" value={`$${fmt((data.ask || 0) - (data.bid || 0))}`} sub={`${fmt(((data.ask || 0) - (data.bid || 0)) / (data.price || 1) * 100, 2)}%`} />
        <Met label="LOT SIZE" value={`${fmtSh(sh)} sh`} sub={`$${fmt(sh * data.price)} exp`} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
        <span style={{ fontSize: 7, color: "#444", fontFamily: mono }}>VWAP Z-SCORE</span>
        <span style={{ fontSize: 8, color: Math.abs(data.zScore || 0) > 2 ? "#ef4444" : "#555", fontFamily: mono, fontWeight: 700 }}>
          {data.zScore >= 0 ? "+" : ""}{fmt(data.zScore)}σ
        </span>
      </div>
      <ZBar z={data.zScore} />
    </div>
  );
}

// ─── CSV Export ──────────────────────────────────────────────────────
function dlCSV(name, hd, rows) {
  const csv = [hd.join(","), ...rows.map((r) => r.map((v) => `"${v}"`).join(","))].join("\n");
  const b = new Blob([csv], { type: "text/csv" });
  const u = URL.createObjectURL(b);
  const a = document.createElement("a");
  a.href = u;
  a.download = name;
  a.click();
  URL.revokeObjectURL(u);
}

// ─── Main App ────────────────────────────────────────────────────────
export default function App() {
  const [tD, setTD] = useState({});
  const [openPos, setOpenPos] = useState({});
  const [trades, setTrades] = useState([]);
  const [engineState, setEngineState] = useState({
    accountValue: 0,
    dayPnl: 0,
    tradeCount: 0,
    winCount: 0,
    lossCount: 0,
    paperMode: true,
    budget: 500,
    maxExposure: 12.5,
    running: true,
  });
  const [running, setRunning] = useState(true);
  const [stale, setStale] = useState(false);
  const [lastFetch, setLastFetch] = useState(null);
  const tick = useRef(0);

  const poll = useCallback(async () => {
    if (!running) return;
    try {
      const [state, tickers, positions, tradesList] = await Promise.all([
        apiGet("/state"),
        apiGet("/tickers"),
        apiGet("/positions"),
        apiGet("/trades"),
      ]);
      setEngineState(state);
      setTD(Object.fromEntries(tickers.map((t) => [t.symbol, t])));
      setOpenPos(positions || {});
      setTrades(tradesList || []);
      setStale(false);
      setLastFetch(new Date());
      tick.current += 1;
    } catch (err) {
      console.error("engine poll failed:", err);
      setStale(true);
    }
  }, [running]);

  useEffect(() => {
    poll();
    const iv = setInterval(poll, POLL_MS);
    return () => clearInterval(iv);
  }, [poll]);

  const wr = engineState.tradeCount > 0
    ? ((engineState.winCount / engineState.tradeCount) * 100).toFixed(1)
    : "—";

  const maxExposure = engineState.maxExposure || (engineState.budget * 0.025);

  return (
    <div style={{ background: "#06060c", color: "#e2e8f0", minHeight: "100vh", fontFamily: mono, padding: 14 }}>
      <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet" />

      {/* Title */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 7, letterSpacing: "3px", color: "#333" }}>HARWOODLABS</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: "#f8fafc", fontFamily: display }}>VWAP SCALPER</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {stale && <Badge text="STALE" color="#ef4444" bg="#1a0a0a" />}
          <Badge
            text={engineState.paperMode ? "PAPER" : "LIVE"}
            color={engineState.paperMode ? "#f59e0b" : "#10b981"}
            bg={engineState.paperMode ? "#1a1400" : "#041f14"}
          />
          <button
            onClick={() => setRunning((p) => !p)}
            style={{ background: "none", border: `1px solid ${running ? "#10b981" : "#ef4444"}`, color: running ? "#10b981" : "#ef4444", padding: "2px 8px", borderRadius: 3, fontSize: 8, cursor: "pointer", fontFamily: mono, fontWeight: 700 }}
          >
            {running ? "● POLLING" : "■ PAUSED"}
          </button>
        </div>
      </div>

      {/* Market countdown */}
      <MarketClock />

      {/* Stats bar */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 8, marginBottom: 10, background: "#0a0a14", border: "1px solid #1a1a2e", borderRadius: 6, padding: 10 }}>
        <Met label="ACCOUNT" value={`$${fmt(engineState.accountValue)}`} />
        <Met
          label="DAY P/L"
          value={`${ps(engineState.dayPnl)}$${fmt(engineState.dayPnl, 3)}`}
          sub={engineState.budget > 0 ? `${ps(engineState.dayPnl)}${fmt((engineState.dayPnl / engineState.budget) * 100)}%` : ""}
          sc={pc(engineState.dayPnl)}
        />
        <Met label="TRADES" value={String(engineState.tradeCount)} sub={`${engineState.winCount}W / ${engineState.lossCount}L`} />
        <Met label="WIN RATE" value={wr === "—" ? "—" : `${wr}%`} sc={parseFloat(wr) >= 50 ? "#10b981" : "#ef4444"} />
        <Met label="EXPOSURE" value={`$${fmt(maxExposure)}`} sub="per trade" />
        <Met label="BUDGET" value={`$${fmt(engineState.budget)}`} sub={`${fmt((engineState.maxExposurePct || 0.025) * 100, 1)}% cap`} />
      </div>

      {/* Open Positions */}
      <OpenPos positions={openPos} tData={tD} />

      {/* Closed Trades */}
      <ClosedToday trades={trades} />

      {/* Watchlist */}
      <div style={{ fontSize: 9, color: "#333", fontFamily: mono, letterSpacing: "1px", marginBottom: 4, marginTop: 14 }}>WATCHLIST</div>
      {TICKERS.map((t) => {
        const d = tD[t];
        if (!d) {
          return (
            <div key={t} style={{ background: "#0d0d14", border: "1px solid #1a1a2e", borderRadius: 6, padding: 12, marginBottom: 8, fontSize: 10, color: "#333", fontFamily: mono }}>
              {t} — awaiting first snapshot from engine…
            </div>
          );
        }
        return <TCard key={t} data={d} maxExposure={maxExposure} />;
      })}

      {/* Exports */}
      <div style={{ marginTop: 14, display: "flex", gap: 6, flexWrap: "wrap" }}>
        {[
          {
            label: "EXPORT TRADES",
            color: "#3b82f6",
            fn: () => {
              const hd = ["Date", "Time", "Symbol", "Side", "Shares", "Entry", "Exit/Price", "P/L", "Strategy", "Hold", "Reason"];
              const rows = trades.map((t) => [
                new Date().toISOString().slice(0, 10),
                t.time,
                t.symbol,
                t.side,
                fmtSh(t.shares),
                t.entry != null ? fmt(t.entry) : "",
                fmt(t.price),
                t.pnl != null ? fmt(t.pnl, 4) : "",
                t.strategy || "",
                t.holdMins ?? "",
                t.reason || "",
              ]);
              dlCSV(`trades-${new Date().toISOString().slice(0, 10)}.csv`, hd, rows);
            },
          },
          {
            label: "EXPORT SUMMARY",
            color: "#10b981",
            fn: () => {
              const cl = trades.filter((t) => t.closed && t.pnl != null);
              const w = cl.filter((t) => t.pnl > 0);
              const gw = w.reduce((s, t) => s + t.pnl, 0);
              const gl = Math.abs(cl.filter((t) => t.pnl <= 0).reduce((s, t) => s + t.pnl, 0));
              dlCSV(
                `summary-${new Date().toISOString().slice(0, 10)}.csv`,
                ["Date", "Balance", "P/L", "Trades", "W", "L", "WR%", "PF", "AvgW", "AvgL"],
                [[
                  new Date().toISOString().slice(0, 10),
                  fmt(engineState.accountValue),
                  fmt(engineState.dayPnl, 4),
                  cl.length,
                  w.length,
                  cl.length - w.length,
                  cl.length ? fmt((w.length / cl.length) * 100, 1) : "0",
                  gl > 0 ? fmt(gw / gl) : "∞",
                  w.length ? fmt(gw / w.length, 4) : "0",
                  cl.length - w.length > 0 ? fmt(gl / (cl.length - w.length), 4) : "0",
                ]]
              );
            },
          },
        ].map(({ label, color, fn }) => (
          <button
            key={label}
            onClick={fn}
            disabled={!trades.length}
            style={{
              background: trades.length ? "#0d0d14" : "#111",
              border: `1px solid ${trades.length ? color : "#1a1a2e"}`,
              color: trades.length ? color : "#333",
              padding: "5px 10px",
              borderRadius: 3,
              fontSize: 8,
              cursor: trades.length ? "pointer" : "not-allowed",
              fontFamily: mono,
              fontWeight: 700,
              letterSpacing: ".5px",
              opacity: trades.length ? 1 : 0.5,
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Footer */}
      <div style={{ marginTop: 16, padding: "8px 0", borderTop: "1px solid #0d0d14", display: "flex", justifyContent: "space-between", fontSize: 7, color: "#1a1a2e" }}>
        <span>
          {engineState.paperMode ? "PAPER MODE · NOT FINANCIAL ADVICE" : "LIVE MODE · REAL CAPITAL"}
          {lastFetch && ` · last update ${lastFetch.toLocaleTimeString()}`}
        </span>
        <span>
          API: {API_URL} · BUDGET: ${fmt(engineState.budget)} · MAX/TRADE: ${fmt(maxExposure)}
        </span>
      </div>
    </div>
  );
}
