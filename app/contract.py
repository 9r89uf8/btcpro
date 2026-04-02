"""Centralized Redis channel and key contract.

Rules:
  - Payload symbol field: uppercase (e.g. BTCUSDT)
  - Channel/key suffix: lowercase (e.g. btcusdt)
  - Raw event channels: raw:{event_type}:{venue}:{symbol}
  - Latest-state keys:  state:latest:{event_type}:{venue}:{symbol}
  - Derived channels:   derived:{event_type}:{symbol}
  - Derived state keys: state:latest:{event_type}:{symbol}
  - Book state keys:    state:book:{venue}:{symbol}
"""

from __future__ import annotations

from app.config import get_settings

# ── Venues ───────────────────────────────────────────────────────────

BINANCE_FUTURES = "binance_futures"
BINANCE_SPOT = "binance_spot"
BYBIT = "bybit"


def _symbol() -> str:
    """Lowercase symbol for channel/key suffixes."""
    return get_settings().symbol.lower()


# ── Raw event channels (pub/sub) ─────────────────────────────────────

def raw_channel(event_type: str, venue: str, symbol: str | None = None) -> str:
    return f"raw:{event_type}:{venue}:{symbol or _symbol()}"


# ── Latest-state keys ────────────────────────────────────────────────

def latest_key(event_type: str, venue: str, symbol: str | None = None) -> str:
    return f"state:latest:{event_type}:{venue}:{symbol or _symbol()}"


# ── Book state keys ──────────────────────────────────────────────────

def book_key(venue: str, symbol: str | None = None) -> str:
    return f"state:book:{venue}:{symbol or _symbol()}"


# ── Collector state keys ────────────────────────────────────────────

def collector_key(venue: str, symbol: str | None = None) -> str:
    return f"state:collector:{venue}:{symbol or _symbol()}"


# ── Derived channels and keys ────────────────────────────────────────

def derived_channel(event_type: str, symbol: str | None = None) -> str:
    return f"derived:{event_type}:{symbol or _symbol()}"


def derived_key(event_type: str, symbol: str | None = None) -> str:
    return f"state:latest:{event_type}:{symbol or _symbol()}"


# ── Convenience constants for commonly used paths ────────────────────

class Channels:
    """Pre-built channel names for the default symbol."""

    @staticmethod
    def trade(venue: str) -> str:
        return raw_channel("trade", venue)

    @staticmethod
    def bbo(venue: str) -> str:
        return raw_channel("bbo", venue)

    @staticmethod
    def book_delta(venue: str) -> str:
        return raw_channel("book_delta", venue)

    @staticmethod
    def mark_index(venue: str) -> str:
        return raw_channel("mark_index", venue)

    @staticmethod
    def liquidation(venue: str) -> str:
        return raw_channel("liquidation", venue)

    @staticmethod
    def open_interest(venue: str) -> str:
        return raw_channel("open_interest", venue)

    @staticmethod
    def feature_bar() -> str:
        return derived_channel("feature_bar")

    @staticmethod
    def score() -> str:
        return derived_channel("score")


class Keys:
    """Pre-built key names for the default symbol."""

    @staticmethod
    def latest(event_type: str, venue: str) -> str:
        return latest_key(event_type, venue)

    @staticmethod
    def book(venue: str) -> str:
        return book_key(venue)

    @staticmethod
    def collector(venue: str) -> str:
        return collector_key(venue)

    @staticmethod
    def feature_bar() -> str:
        return derived_key("feature_bar")

    @staticmethod
    def score() -> str:
        return derived_key("score")
