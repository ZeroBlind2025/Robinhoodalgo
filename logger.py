"""
Logging utilities. A small wrapper around the stdlib logging module plus a
JSON-lines trade log writer.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

import config


def get_logger(name: str = "scalper") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(level)

    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


class TradeLogger:
    """Append-only JSONL trade log."""

    def __init__(self, path: str = None):
        self.path = path or config.TRADE_LOG_PATH
        self._log = get_logger("trades")

    def record(self, event: str, payload: Dict[str, Any]) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **payload,
        }
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:
            self._log.warning("Failed to write trade log: %s", exc)
        self._log.info("%s %s", event, payload)
