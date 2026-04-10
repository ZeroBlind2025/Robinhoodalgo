#!/usr/bin/env python3
"""
One-time interactive login bootstrap for Robinhood.

WHY THIS EXISTS
---------------
Robinhood migrated most accounts off authenticator-app TOTP and now
challenges logins with a push notification sent to the Robinhood mobile
app. That flow requires a human to tap "Approve" on a phone, which is
impossible from a headless Railway container.

The workaround: run this script LOCALLY on any machine where your phone
is reachable. It performs the interactive login once, solves the push
challenge, and saves a session pickle containing the OAuth tokens. You
then copy the base64 blob it prints into Railway as the
RH_SESSION_PICKLE_B64 environment variable. The engine decodes the blob
on startup and reuses the cached tokens without ever needing MFA.

The refresh token inside the pickle stays valid until Robinhood expires
it (typically days to weeks). When the engine eventually fails to
authenticate, re-run this script and update the env var.

USAGE
-----
    pip install -r requirements.txt
    python bootstrap_login.py

Follow the prompts. When the script says "approve on your phone", open
the Robinhood app and tap Approve. When it finishes, copy the emitted
blob into Railway.

ENV VARS YOU'LL END UP SETTING ON RAILWAY
------------------------------------------
    PAPER_MODE=true                    # keep until Phase 4
    RH_USERNAME=<your email>
    RH_PASSWORD=<your password>
    RH_SESSION_PICKLE_B64=<the blob below>
"""

import base64
import getpass
import sys
from pathlib import Path

import config


def main() -> int:
    print("=" * 60)
    print("  Robinhood session bootstrap")
    print("=" * 60)
    print()
    print("Run this on a machine where you can tap 'Approve' on the")
    print("Robinhood push notification from your phone.")
    print()

    try:
        import robin_stocks.robinhood as rh  # type: ignore
    except ImportError:
        print(
            "robin_stocks is not installed. Run:\n"
            "    pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    username = input("Robinhood username (email): ").strip()
    if not username:
        print("No username provided.", file=sys.stderr)
        return 1

    password = getpass.getpass("Robinhood password: ")
    if not password:
        print("No password provided.", file=sys.stderr)
        return 1

    print()
    print("Attempting login. If Robinhood sends a push notification,")
    print("approve it on your phone. robin_stocks will poll until you do.")
    print()

    try:
        rh.login(
            username=username,
            password=password,
            store_session=True,
            pickle_name=config.PICKLE_NAME,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\nLogin failed: {exc}", file=sys.stderr)
        print(
            "\nIf the error mentions a challenge or MFA, make sure you "
            "approved the push on your phone before it timed out. "
            "If it mentions SMS, enter the code when prompted.",
            file=sys.stderr,
        )
        return 1

    # Verify the session by making an authenticated call.
    try:
        profile = rh.profiles.load_portfolio_profile()
    except Exception as exc:  # noqa: BLE001
        print(f"\nSession probe failed: {exc}", file=sys.stderr)
        return 1

    if not profile:
        print("\nLogin returned but profile probe came back empty.", file=sys.stderr)
        return 1

    equity = profile.get("equity", "?")
    print(f"\n[ok] Session established. Portfolio equity: ${equity}")

    pickle_path = Path.home() / ".tokens" / f"{config.PICKLE_NAME}.pickle"
    if not pickle_path.exists():
        print(
            f"\nExpected pickle at {pickle_path} but it's missing. "
            f"robin_stocks may have changed its storage path.",
            file=sys.stderr,
        )
        return 1

    pickle_bytes = pickle_path.read_bytes()
    print(f"[ok] Pickle saved to: {pickle_path} ({len(pickle_bytes)} bytes)")

    blob = base64.b64encode(pickle_bytes).decode("ascii")

    print()
    print("=" * 60)
    print("  Copy the value below into Railway as")
    print("  RH_SESSION_PICKLE_B64")
    print("=" * 60)
    print()
    print(blob)
    print()
    print("=" * 60)
    print("  Also set these Railway env vars:")
    print("=" * 60)
    print()
    print(f"  RH_USERNAME={username}")
    print(f"  RH_PASSWORD=<your password>")
    print(f"  RH_SESSION_PICKLE_B64=<the blob above>")
    print(f"  PAPER_MODE=true        # flip to false when you're ready")
    print()
    print("The engine will load the pickle at startup and skip MFA entirely.")
    print("If the session later expires, re-run this script and update the")
    print("env var in Railway.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
