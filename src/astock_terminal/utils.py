from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CN_TZ).replace(microsecond=0).isoformat()


def normalize_symbol(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) > 6:
        digits = digits[-6:]
    return digits.zfill(6)


def exchange_of(symbol: str) -> str:
    s = normalize_symbol(symbol)
    if s.startswith(("920", "83", "87", "88")):
        return "BSE"
    if s.startswith(("5", "6", "9")):
        return "SSE"
    return "SZSE"


def tencent_prefix(symbol: str) -> str:
    return {"SSE": "sh", "SZSE": "sz", "BSE": "bj"}[exchange_of(symbol)]


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def fnum(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
