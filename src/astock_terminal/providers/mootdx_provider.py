from __future__ import annotations

import time

from ..models import Quote
from ..utils import exchange_of, fnum, normalize_symbol, now_iso


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), max(size, 1))]


def fetch_mootdx(
    symbols: list[str],
    server: str = "",
    retries: int = 2,
    batch_size: int = 80,
) -> tuple[list[Quote], str | None, float]:
    start = time.perf_counter()
    symbols = sorted({normalize_symbol(s) for s in symbols if normalize_symbol(s) != "000000"})
    if not symbols:
        return [], None, 0.0
    try:
        from mootdx.quotes import Quotes
        from mootdx.consts import HQ_HOSTS
    except Exception as exc:
        return [], f"mootdx未安装或导入失败: {exc}", (time.perf_counter() - start) * 1000
    try:
        candidates: list[tuple[str, int]] = []
        if server and ":" in server:
            host, port = server.rsplit(":", 1)
            candidates.append((host.strip(), int(port)))
        candidates.extend((x[1], int(x[2])) for x in HQ_HOSTS[:6] if (x[1], int(x[2])) not in candidates)
        errors: list[str] = []
        output: list[Quote] = []
        for attempt in range(max(retries, 0) + 1):
            client = None
            try:
                for candidate in candidates:
                    try:
                        client = Quotes.factory(market="std", server=candidate, timeout=2, heartbeat=True)
                        if not getattr(client, "closed", False):
                            break
                    except Exception as exc:
                        errors.append(f"{candidate[0]}:{candidate[1]} {type(exc).__name__}")
                        client = None
                if client is None:
                    raise ConnectionError("无可用通达信服务器: " + "; ".join(errors[-6:]))
                for batch in _chunks(symbols, batch_size):
                    raw = client.quotes(symbol=batch)
                    records = raw.to_dict("records") if hasattr(raw, "to_dict") else list(raw or [])
                    fetched = now_iso()
                    for row in records:
                        symbol = normalize_symbol(row.get("code") or row.get("symbol") or "")
                        price = fnum(row.get("price") or row.get("last_price") or row.get("close"))
                        if not price or price <= 0:
                            continue
                        output.append(Quote(
                            symbol=symbol, exchange=exchange_of(symbol), name=row.get("name"),
                            last_price=price, prev_close=fnum(row.get("last_close") or row.get("prev_close")),
                            open_price=fnum(row.get("open")), high=fnum(row.get("high")), low=fnum(row.get("low")),
                            volume=fnum(row.get("vol") or row.get("volume")), amount=fnum(row.get("amount")),
                            bid1=fnum(row.get("bid1")), ask1=fnum(row.get("ask1")),
                            quote_time=fetched, fetch_time=fetched, provider="mootdx"
                        ))
                break
            except Exception as exc:
                errors.append(f"attempt={attempt + 1} {type(exc).__name__}: {exc}")
                if attempt < retries:
                    time.sleep(0.2 * (attempt + 1))
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
        latency = (time.perf_counter() - start) * 1000
        if output:
            unique: dict[str, Quote] = {q.symbol: q for q in output}
            err = "; ".join(errors[-4:]) if errors else None
            return list(unique.values()), err, latency
        return [], "; ".join(errors[-6:]) or "mootdx未返回有效行情", latency
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}", (time.perf_counter() - start) * 1000
