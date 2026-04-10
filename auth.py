"""
Robinhood authentication (Section 8.1).

Wraps `robin_stocks.robinhood.login` with TOTP MFA and pickled session
persistence so the Railway container can survive restarts without re-solving
MFA on every boot.
"""

import time
from typing import Optional

import config
from logger import get_logger

log = get_logger("auth")


def _robin_stocks():
    # Imported lazily so that unit tests and paper mode can run without the
    # library installed.
    import robin_stocks.robinhood as rh  # type: ignore
    return rh


def login(max_retries: int = 4) -> bool:
    """Attempt login with exponential backoff. Returns True on success."""
    if config.PAPER_MODE:
        log.info("PAPER_MODE=true, skipping Robinhood login")
        return True

    if not (config.RH_USERNAME and config.RH_PASSWORD and config.RH_MFA_SECRET):
        log.error("Robinhood credentials not set; cannot login in live mode")
        return False

    import pyotp  # type: ignore
    rh = _robin_stocks()

    backoff = 2
    for attempt in range(1, max_retries + 1):
        try:
            totp = pyotp.TOTP(config.RH_MFA_SECRET).now()
            rh.login(
                username=config.RH_USERNAME,
                password=config.RH_PASSWORD,
                mfa_code=totp,
                store_session=True,
                pickle_name=config.PICKLE_NAME,
            )
            log.info("Robinhood login successful (attempt %d)", attempt)
            return True
        except Exception as exc:  # noqa: BLE001 - RH wraps many error types
            log.warning("Login attempt %d failed: %s", attempt, exc)
            if attempt == max_retries:
                log.error("Login failed after %d attempts", max_retries)
                return False
            time.sleep(backoff)
            backoff *= 2
    return False


def logout() -> None:
    if config.PAPER_MODE:
        return
    try:
        rh = _robin_stocks()
        rh.logout()
    except Exception as exc:  # noqa: BLE001
        log.warning("Logout failed: %s", exc)


def ensure_session() -> bool:
    """Cheap probe that the session is still alive; re-login if not."""
    if config.PAPER_MODE:
        return True
    try:
        rh = _robin_stocks()
        # load_portfolio_profile is a lightweight authenticated call.
        profile = rh.profiles.load_portfolio_profile()
        if profile:
            return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Session probe failed, re-logging in: %s", exc)
    return login()
