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
from typing import Any, Dict, Optional

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


def _find_pickle() -> Optional[Path]:
    """
    Locate the session pickle wherever robin_stocks actually wrote it.

    robin_stocks 3.4.0 has been observed to write the pickle to a
    different location (or not at all) depending on the challenge
    flow used. This walks the likely directories and returns the
    most-recently-modified .pickle file.
    """
    expected = _pickle_path()
    if expected.exists():
        return expected

    search_dirs = [
        Path.home() / ".tokens",
        Path("/root") / ".tokens",
        Path.home(),
        Path.cwd(),
        Path("/tmp"),
    ]

    best: Optional[Path] = None
    best_mtime: float = 0.0
    seen = set()
    for d in search_dirs:
        try:
            rd = d.resolve()
        except Exception:  # noqa: BLE001
            continue
        if rd in seen or not rd.exists():
            continue
        seen.add(rd)
        try:
            for f in rd.rglob("*.pickle"):
                if not f.is_file():
                    continue
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                if mtime > best_mtime:
                    best = f
                    best_mtime = mtime
        except (PermissionError, OSError):
            continue
    return best


def _try_manual_pickle_save() -> Optional[Path]:
    """
    Fallback: if robin_stocks didn't write the pickle to disk after a
    successful login, pull the session state out of its module globals
    and write our own pickle. The resulting file has the same shape as
    the one robin_stocks normally writes, so _restore_pickle_from_env()
    can feed it back on next boot.
    """
    try:
        rh = _robin_stocks()
        from robin_stocks.robinhood import helper as rh_helper  # type: ignore

        session = getattr(rh_helper, "SESSION", None)
        if session is None:
            log.warning("robin_stocks helper.SESSION not available; cannot manual-pickle")
            return None

        # Pull OAuth state from the module. Different robin_stocks
        # versions keep these in slightly different places; try a few.
        candidates = [
            rh_helper,
            getattr(rh, "authentication", None),
        ]
        token_data: Dict[str, Any] = {}
        for mod in candidates:
            if mod is None:
                continue
            for key in ("access_token", "refresh_token", "device_token",
                        "token_type", "expires_in"):
                val = getattr(mod, key, None)
                if val and key not in token_data:
                    token_data[key] = val

        if not token_data.get("access_token"):
            log.warning("No access_token in robin_stocks module state")
            return None

        # Reconstruct the pickle payload robin_stocks normally writes.
        payload = {
            "token_type": token_data.get("token_type", "Bearer"),
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token", ""),
            "device_token": token_data.get("device_token", ""),
            "expires_in": token_data.get("expires_in", 86400),
            "headers": dict(session.headers) if hasattr(session, "headers") else {},
        }

        path = _pickle_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        import pickle
        with open(path, "wb") as f:
            pickle.dump(payload, f)
        log.info("Manually wrote session pickle to %s", path)
        return path
    except Exception as exc:  # noqa: BLE001
        log.warning("Manual pickle save failed: %s", exc)
        return None


def debug_token_state() -> Dict[str, Any]:
    """Return filesystem + session debug info for /bootstrap/debug."""
    info: Dict[str, Any] = {
        "home": str(Path.home()),
        "cwd": str(Path.cwd()),
        "pickle_name": config.PICKLE_NAME,
        "expected_pickle_path": str(_pickle_path()),
        "expected_pickle_exists": _pickle_path().exists(),
        "tokens_dir": str(Path.home() / ".tokens"),
        "tokens_dir_exists": (Path.home() / ".tokens").exists(),
        "tokens_dir_contents": [],
        "found_pickles": [],
        "rh_module_state": {},
    }
    tokens_dir = Path.home() / ".tokens"
    if tokens_dir.exists():
        try:
            for f in tokens_dir.iterdir():
                info["tokens_dir_contents"].append({
                    "name": f.name,
                    "size": f.stat().st_size if f.exists() else 0,
                    "path": str(f),
                })
        except Exception as exc:  # noqa: BLE001
            info["tokens_dir_contents"] = f"error: {exc}"

    for d in (Path.home() / ".tokens", Path.home(), Path.cwd()):
        if not d.exists():
            continue
        try:
            for f in d.rglob("*.pickle"):
                if f.is_file():
                    info["found_pickles"].append({
                        "path": str(f),
                        "size": f.stat().st_size,
                        "mtime": f.stat().st_mtime,
                    })
        except Exception:  # noqa: BLE001
            continue

    # Probe robin_stocks module state (non-sensitive keys only).
    try:
        from robin_stocks.robinhood import helper as rh_helper  # type: ignore
        info["rh_module_state"] = {
            "has_session": hasattr(rh_helper, "SESSION"),
            "has_access_token": bool(getattr(rh_helper, "access_token", None)),
            "has_refresh_token": bool(getattr(rh_helper, "refresh_token", None)),
        }
    except Exception as exc:  # noqa: BLE001
        info["rh_module_state"] = {"error": str(exc)}

    return info


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


def _print_pickle_blob() -> None:
    """
    Read the pickle off disk, base64-encode it, and print it to the
    Railway logs with clear copy-paste instructions. Called once after
    a successful fresh bootstrap login so the user can persist the
    session as an env var and skip MFA on future boots.
    """
    path = _find_pickle()
    if path is None:
        log.warning(
            "Login succeeded but no pickle file found on disk. "
            "robin_stocks may have skipped store_session for this "
            "flow. Attempting manual pickle save from module state."
        )
        path = _try_manual_pickle_save()

    if path is None:
        log.warning("Cannot persist session — see /bootstrap/debug for details")
        return

    try:
        data = path.read_bytes()
    except OSError as exc:
        log.warning("Failed to read pickle at %s: %s", path, exc)
        return

    blob = base64.b64encode(data).decode("ascii")
    log.info("Encoded pickle at %s (%d bytes -> %d base64 chars)",
             path, len(data), len(blob))

    banner = "=" * 72
    log.warning("")
    log.warning(banner)
    log.warning("  LOGIN SUCCEEDED — SAVE THIS PICKLE TO RAILWAY")
    log.warning(banner)
    log.warning("  Copy the ENTIRE base64 blob below, then in Railway:")
    log.warning("    Variables -> New Variable")
    log.warning("    Name:  RH_SESSION_PICKLE_B64")
    log.warning("    Value: <paste the blob>")
    log.warning("  Railway will auto-redeploy and the engine will use")
    log.warning("  the cached session from now on. You won't need to")
    log.warning("  approve another push notification until the refresh")
    log.warning("  token eventually expires (typically days to weeks).")
    log.warning(banner)
    log.warning("")
    log.warning("RH_SESSION_PICKLE_B64=%s", blob)
    log.warning("")
    log.warning(banner)
    log.warning("")


def login(max_retries: int = 4) -> bool:
    """
    Establish a Robinhood session. Returns True on success.

    Strategy:
      1. In paper mode with no creds → skip entirely.
      2. Restore any base64 pickle from the env var to disk.
      3. Try TOTP if a secret is set (legacy).
      4. Fall back to a fresh rh.login() — which will trigger a
         Robinhood push notification to the user's phone and poll
         until they approve. This is how the first-time bootstrap
         works directly on Railway without needing a local machine.
      5. On the first successful fresh login (no prior pickle), emit
         the base64 pickle blob to the logs so the user can persist
         it as an env var.
      6. On failure, emit a loud instruction.
    """
    if config.PAPER_MODE and not (config.RH_USERNAME and config.RH_PASSWORD):
        log.info("PAPER_MODE with no credentials; skipping Robinhood login")
        return True

    if not (config.RH_USERNAME and config.RH_PASSWORD):
        log.error("RH_USERNAME/RH_PASSWORD must be set")
        return False

    # Write the base64-encoded pickle to disk if one was provided.
    restored = _restore_pickle_from_env()
    pickle_path_existed = _pickle_path().exists()
    pickle_present = restored or pickle_path_existed
    is_fresh_bootstrap = not pickle_present

    if is_fresh_bootstrap:
        banner = "=" * 72
        log.warning("")
        log.warning(banner)
        log.warning("  FIRST-TIME BOOTSTRAP — no session pickle found")
        log.warning(banner)
        log.warning("  About to call rh.login() which will trigger a")
        log.warning("  Robinhood push notification to your phone.")
        log.warning("")
        log.warning("  >>> OPEN THE ROBINHOOD APP AND TAP 'APPROVE' <<<")
        log.warning("")
        log.warning("  robin_stocks will poll for approval for about 60s.")
        log.warning("  If you miss the window, redeploy to retry.")
        log.warning(banner)
        log.warning("")

    backoff = 2
    for attempt in range(1, max_retries + 1):
        log.info("Login attempt %d/%d", attempt, max_retries)

        if _try_totp_login():
            log.info("Robinhood session active via TOTP")
            return True

        if _try_pickle_login():
            log.info("Robinhood session active via pickled tokens")
            if is_fresh_bootstrap:
                _print_pickle_blob()
            return True

        if attempt < max_retries:
            time.sleep(backoff)
            backoff *= 2

    if is_fresh_bootstrap:
        log.error(
            "Fresh bootstrap login failed. Make sure RH_USERNAME and "
            "RH_PASSWORD are correct, then redeploy to retry. If you "
            "approved the push too late, robin_stocks times out after "
            "~60s and a redeploy will trigger a new push."
        )
    else:
        log.error(
            "Login failed — the session pickle has likely expired. "
            "Delete the RH_SESSION_PICKLE_B64 env var in Railway and "
            "redeploy; the engine will trigger a fresh bootstrap push "
            "on next start, and log the new pickle blob for you to "
            "paste back into the env var."
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
