"""
Collect 5-minute historical bars for offline backtesting.

Usage:
    python -m backtest.data_collector CRWV NBIS SMCI --span week --out bars.json
"""

import argparse
import json
import sys

from data import get_historicals


def collect(tickers, interval, span):
    out = {}
    for t in tickers:
        bars = get_historicals(t, interval=interval, span=span)
        out[t] = bars
        print(f"{t}: {len(bars)} bars", file=sys.stderr)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Collect historicals for backtesting")
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--interval", default="5minute",
                    choices=["5minute", "10minute", "hour", "day"])
    ap.add_argument("--span", default="day",
                    choices=["day", "week", "month", "year"])
    ap.add_argument("--out", default="bars.json")
    args = ap.parse_args()

    data = collect(args.tickers, args.interval, args.span)
    with open(args.out, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
