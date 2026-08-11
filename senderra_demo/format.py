"""Display helpers. Pure, and deliberately boring.

The only opinion here: a missing number renders as an em dash, never as 0 and
never as "nan". A zero is a measurement; a blank is the absence of one, and a
dashboard that confuses the two is worse than no dashboard.
"""
from __future__ import annotations

import math
from typing import Any

DASH = "—"


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def num(value: Any, decimals: int = 0) -> str:
    if _missing(value):
        return DASH
    return f"{float(value):,.{decimals}f}"


def usd(value: Any, decimals: int = 2) -> str:
    if _missing(value):
        return DASH
    return f"${float(value):,.{decimals}f}"


def pct(value: Any, decimals: int = 1) -> str:
    """Takes a 0–1 fraction, renders a percentage."""
    if _missing(value):
        return DASH
    return f"{float(value) * 100:.{decimals}f}%"


def ms(value: Any) -> str:
    """Milliseconds at human scale. A 31-second stage should not read '31022'."""
    if _missing(value):
        return DASH
    value = float(value)
    if value < 1_000:
        return f"{value:.0f} ms"
    if value < 60_000:
        return f"{value / 1_000:.1f} s"
    minutes, seconds = divmod(value / 1_000, 60)
    return f"{int(minutes)}m {seconds:.0f}s"


def size(value: Any) -> str:
    if _missing(value):
        return DASH
    value = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return DASH


def confidence(value: Any) -> str:
    if _missing(value):
        return DASH
    return f"{float(value):.3f}"


def safe_int(value: Any, default: int = 0) -> int:
    """`int(x or 0)` is not safe here.

    A missing metrics column arrives as float('nan'), which is TRUTHY, so
    `int(value or 0)` reaches `int(nan)` and raises ValueError. Every count read
    off an optional column goes through this.
    """
    return default if _missing(value) else int(value)


def text(value: Any, default: str = DASH) -> str:
    """A string cell, or a dash. Guards the same NaN-is-truthy trap: an absent
    column would otherwise render as the literal 'nan'."""
    return default if _missing(value) or not isinstance(value, str) or not value else value


def truncate(text: Any, limit: int = 80) -> str:
    if _missing(text) or text == "":
        return DASH
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"
