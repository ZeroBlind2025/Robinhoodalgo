"""
Robinhood authentication — pickle-based session, headless-safe.

Robinhood moved most accounts off TOTP to push-notification challenges
that can't be solved from a headless Railway container. The only reliable
pattern is:

  1. Run `python bootstrap_login.py` LOCALLY on a machine where you can
     approve the push on your phone. That produces a session pickle.
  2. Copy the base64-encoded pickle into Railway as the
     RH_SESSION_PICKLE_B64 environment variable.
  3. On startup this module decodes the blob, writes it to the path
     robin_stocks expects (~/.tokens/<pickle_name>.pickle), and lets
     robin_stocks reuse the cached OAuth tokens.

The refresh token inside the pickle is what keeps things working —
robin_stocks silently refreshes the access token on each call as long
as the refresh token is valid (typically days to weeks). When it
eventually expires, the engine logs a loud error; re-run the bootstrap
and update the env var.

For legacy compatibility: if RH_MFA_SECRET is set, the old TOTP flow is
still attempted first. It will likely fail for most accounts now, but
leaving it in place doesn't hurt users who still have authenticator-app
MFA enabled.
"""

import base64
import os
import time
from pathlib import Path
from typing import Optional

import config
from logger import get_logger

log = get_logger("auth")

SESSION_ENV = "RH_SESSION_PICKLE_B64"


def _robin_stocks():
    # Imported lazily so paper mode and unit tests can run without the lib.
    import robin_stocks.robinhood as rh  # type: ignore
    return rh


def _pickle_path() -> Path:
    """Where robin_stocks expects the session pickle to live."""
    return Path.home() / ".tokens" / f"{config.PICKLE_NAME}.pickle"


def _restore_pickle_from_env() -> bool:
    """
    If RH_SESSION_PICKLE_B64 is set, decode it to ~/.tokens/<name>.pickle
    so robin_stocks picks it up on its next call. Returns True if a
    pickle was actually written.
    """
    blob = os.environ.get(SESSION_ENV, "").strip()
    if not blob:
        return False
    try:
        data = base64.b64decode(blob)
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to base64-decode %s: %s", SESSION_ENV, exc)
        return False

    path = _pickle_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError as exc:
        log.error("Failed to write session pickle to %s: %s", path, exc)
        return False

    log.info(
        "Restored session pickle from %s (%d bytes -> %s)",
        SESSION_ENV, len(data), path,
    )
    return True


def _session_valid() -> bool:
    """Cheap authenticated probe — did the cached tokens still work?"""
    try:
        rh = _robin_stocks()
        profile = rh.profiles.load_portfolio_profile()
        return bool(profile)
    except Exception:  # noqa: BLE001
        return False


def _try_totp_login() -> bool:
    """Legacy TOTP path — kept for accounts that still have it enabled."""
    if not config.RH_MFA_SECRET:
        return False
    try:
        import pyotp  # type: ignore
    except ImportError:
        log.warning("RH_MFA_SECRET set but pyotp not installed; skipping TOTP")
        return False

    try:
        totp = pyotp.TOTP(config.RH_MFA_SECRET).now()
        _robin_stocks().login(
            username=config.RH_USERNAME,
            password=config.RH_PASSWORD,
            mfa_code=totp,
            store_session=True,
            pickle_name=config.PICKLE_NAME,
        )
        return _session_valid()
    except Exception as exc:  # noqa: BLE001
        log.info("TOTP login path failed (expected for most accounts): %s", exc)
        return False


def _try_pickle_login() -> bool:
    """
    Let robin_stocks load the pickled session. If the refresh token
    inside is still valid this succeeds without any MFA prompt.
    """
    try:
        _robin_stocks().login(
            username=config.RH_USERNAME,
            password=config.RH_PASSWORD,
            store_session=True,
            pickle_name=config.PICKLE_NAME,
        )
        return _session_valid()
    except Exception as exc:  # noqa: BLE001
        log.warning("Pickle-based login failed: %s", exc)
        return False


def login(max_retries: int = 4) -> bool:
    """
    Establish a Robinhood session. Returns True on success.

    Strategy:
      1. In paper mode with no creds → skip entirely.
      2. Restore any base64 pickle from the env var to disk.
      3. Try TOTP if a secret is set (legacy).
      4. Fall back to pickle-based login.
      5. On failure, emit a loud instruction to re-run bootstrap_login.py.
    """
    if config.PAPER_MODE and not (config.RH_USERNAME and config.RH_PASSWORD):
        log.info("PAPER_MODE with no credentials; skipping Robinhood login")
        return True

    if not (config.RH_USERNAME and config.RH_PASSWORD):
        log.error("RH_USERNAME/RH_PASSWORD must be set")
        return False

    # Write the base64-encoded pickle to disk if one was provided.
    restored = _restore_pickle_from_env()
    pickle_present = restored or _pickle_path().exists()

    backoff = 2
    for attempt in range(1, max_retries + 1):
        log.info("Login attempt %d/%d", attempt, max_retries)

        if _try_totp_login():
            log.info("Robinhood session active via TOTP")
            return True

        if _try_pickle_login():
            log.info("Robinhood session active via pickled tokens")
            return True

        if attempt < max_retries:
            time.sleep(backoff)
            backoff *= 2

    if not pickle_present:
        log.error(
            "No session pickle found and no working credentials. "
            "Run `python bootstrap_login.py` on a machine where you can "
            "approve the Robinhood push notification, then copy the "
            "resulting %s value into Railway.", SESSION_ENV,
        )
    else:
        log.error(
            "Login failed — the session pickle has likely expired. "
            "Re-run `python bootstrap_login.py` locally and update %s "
            "in Railway.", SESSION_ENV,
        )
    return False


def logout() -> None:
    if config.PAPER_MODE and not (config.RH_USERNAME and config.RH_PASSWORD):
        return
    try:
        _robin_stocks().logout()
    except Exception as exc:  # noqa: BLE001
        log.warning("Logout failed: %s", exc)


def ensure_session() -> bool:
    """
    Called periodically by the main loop. If the session is still good,
    return True. Otherwise try a single re-login (the cached refresh
    token should do the work) and return the result.
    """
    if config.PAPER_MODE and not (config.RH_USERNAME and config.RH_PASSWORD):
        return True
    if _session_valid():
        return True
    log.warning("Session probe failed, attempting re-login")
    return login(max_retries=1)
