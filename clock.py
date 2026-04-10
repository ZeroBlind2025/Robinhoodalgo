"""
Market clock helpers. All times are evaluated in US/Eastern.
"""

from datetime import datetime, time, timedelta
from typing import Optional

import pytz

import config

ET = pytz.timezone("US/Eastern")


def now_et() -> datetime:
    return datetime.now(ET)


def market_open_time(day: Optional[datetime] = None) -> datetime:
    day = day or now_et()
    return ET.localize(
        datetime(day.year, day.month, day.day,
                 config.MARKET_OPEN_HOUR, config.MARKET_OPEN_MINUTE)
    )


def market_close_time(day: Optional[datetime] = None) -> datetime:
    day = day or now_et()
    return ET.localize(
        datetime(day.year, day.month, day.day,
                 config.MARKET_CLOSE_HOUR, config.MARKET_CLOSE_MINUTE)
    )


def flatten_time(day: Optional[datetime] = None) -> datetime:
    day = day or now_et()
    return ET.localize(
        datetime(day.year, day.month, day.day,
                 config.FLATTEN_HOUR, config.FLATTEN_MINUTE)
    )


def trade_cutoff_time(day: Optional[datetime] = None) -> datetime:
    day = day or now_et()
    return ET.localize(
        datetime(day.year, day.month, day.day,
                 config.TRADE_CUTOFF_HOUR, config.TRADE_CUTOFF_MINUTE)
    )


def is_weekday(dt: Optional[datetime] = None) -> bool:
    dt = dt or now_et()
    return dt.weekday() < 5


def is_market_open(dt: Optional[datetime] = None) -> bool:
    dt = dt or now_et()
    if not is_weekday(dt):
        return False
    return market_open_time(dt) <= dt < market_close_time(dt)


def within_trading_window(dt: Optional[datetime] = None) -> bool:
    """Entries only permitted between (open + buffer) and trade_cutoff."""
    dt = dt or now_et()
    if not is_market_open(dt):
        return False
    start = market_open_time(dt) + timedelta(minutes=config.MARKET_OPEN_BUFFER)
    return start <= dt < trade_cutoff_time(dt)


def should_flatten(dt: Optional[datetime] = None) -> bool:
    dt = dt or now_et()
    return is_weekday(dt) and dt >= flatten_time(dt) and dt < market_close_time(dt)


def seconds_until(target: datetime) -> float:
    delta = (target - now_et()).total_seconds()
    return max(delta, 0.0)
