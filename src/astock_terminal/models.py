from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Quote:
    symbol: str
    exchange: str
    name: str | None
    last_price: float
    prev_close: float | None
    open_price: float | None
    high: float | None
    low: float | None
    volume: float | None
    amount: float | None
    bid1: float | None
    ask1: float | None
    quote_time: str
    fetch_time: str
    provider: str
    status: str = "OK"
    error: str | None = None
