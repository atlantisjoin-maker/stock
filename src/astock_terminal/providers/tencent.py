from __future__ import annotations

import time
import urllib.request
from datetime import datetime

from ..models import Quote
from ..utils import CN_TZ, exchange_of, fnum, normalize_symbol, now_iso, tencent_prefix


TENCENT_ENDPOINTS = (
    "https://qt.gtimg.cn/q={query}",
    "https://web.sqt.gtimg.cn/q={query}",
)


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), max(size, 1))]


def _request_tencent(symbols: list[str], endpoint: str, timeout: float) -> str:
    query = ",".join(tencent_prefix(s) + normalize_symbol(s) for s in symbols)
    url = endpoint.format(query=query)
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "*/*",
            "Referer": "https://gu.qq.com/",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("gbk", errors="replace")


def _parse_tencent(text: str, fetched: str) -> list[Quote]:
    output: list[Quote] = []
    for line in text.splitlines():
        if '="' not in line:
            continue
        _, payload = line.split('="', 1)
        fields = payload.rstrip('";\r\n').split("~")
        if len(fields) < 38:
            continue
        symbol = normalize_symbol(fields[2])
        price = fnum(fields[3])
        if not price or price <= 0:
            continue
        try:
            quote_time = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=CN_TZ).isoformat()
        except Exception:
            quote_time = fetched
        amount_raw = fnum(fields[37])
        output.append(Quote(
            symbol=symbol, exchange=exchange_of(symbol), name=fields[1] or None,
            last_price=price, prev_close=fnum(fields[4]), open_price=fnum(fields[5]),
            high=fnum(fields[33]), low=fnum(fields[34]), volume=fnum(fields[6]),
            amount=amount_raw * 10000 if amount_raw is not None else None,
            bid1=fnum(fields[9]), ask1=fnum(fields[19]), quote_time=quote_time,
            fetch_time=fetched, provider="tencent"
        ))
    return output


def fetch_tencent(
    symbols: list[str],
    timeout: float = 6.0,
    retries: int = 2,
    batch_size: int = 60,
) -> tuple[list[Quote], str | None, float]:
    start = time.perf_counter()
    symbols = sorted({normalize_symbol(s) for s in symbols if normalize_symbol(s) != "000000"})
    if not symbols:
        return [], None, 0.0
    output: list[Quote] = []
    errors: list[str] = []
    for batch in _chunks(symbols, batch_size):
        batch_ok = False
        for attempt in range(max(retries, 0) + 1):
            for endpoint in TENCENT_ENDPOINTS:
                try:
                    text = _request_tencent(batch, endpoint, timeout)
                    parsed = _parse_tencent(text, now_iso())
                    if parsed:
                        output.extend(parsed)
                        batch_ok = True
                        break
                    errors.append(f"{endpoint.split('/')[2]} batch={len(batch)} 未解析出行情")
                except Exception as exc:
                    errors.append(f"{endpoint.split('/')[2]} attempt={attempt + 1} {type(exc).__name__}: {exc}")
            if batch_ok:
                break
            if attempt < retries:
                time.sleep(0.2 * (attempt + 1))
        if not batch_ok:
            errors.append("腾讯批次失败: " + ",".join(batch[:5]))
    latency = (time.perf_counter() - start) * 1000
    if output:
        unique: dict[str, Quote] = {q.symbol: q for q in output}
        err = "; ".join(errors[-4:]) if errors else None
        return list(unique.values()), err, latency
    return [], "; ".join(errors[-6:]) or "腾讯响应未解析出有效行情", latency
