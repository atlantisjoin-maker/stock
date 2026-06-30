from __future__ import annotations

import json
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, timedelta
from typing import Any

from ..utils import normalize_symbol


UA = "Mozilla/5.0 AStockWebTerminal/4.1"
EASTMONEY_HOT_BODY = {"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38"}

_eastmoney_lock = threading.RLock()
_eastmoney_last_call = 0.0


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _throttle(min_interval: float) -> None:
    global _eastmoney_last_call
    min_interval = max(float(min_interval or 0), 0)
    if min_interval <= 0:
        return
    with _eastmoney_lock:
        elapsed = time.perf_counter() - _eastmoney_last_call
        wait = min_interval - elapsed
        if wait > 0:
            time.sleep(wait + random.uniform(0.08, 0.35))
        _eastmoney_last_call = time.perf_counter()


def _request_text(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10,
    retries: int = 2,
    eastmoney: bool = True,
    min_interval: float = 1.2,
) -> str:
    errors: list[str] = []
    if params:
        query = urllib.parse.urlencode(params)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{query}"
    body = None
    merged_headers = {"User-Agent": UA, "Accept": "application/json, text/javascript, */*"}
    merged_headers.update(headers or {})
    if json_body is not None:
        body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        merged_headers.setdefault("Content-Type", "application/json")
    method = "POST" if body is not None else "GET"
    for attempt in range(max(retries, 0) + 1):
        if eastmoney:
            _throttle(min_interval)
        try:
            req = urllib.request.Request(url, data=body, headers=merged_headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            errors.append(f"HTTP_{exc.code}")
            if exc.code in {401, 403, 404}:
                break
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        if attempt < retries:
            time.sleep(0.35 * (attempt + 1))
    raise RuntimeError("; ".join(errors[-4:]) or "request failed")


def _json_loads(text: str) -> Any:
    stripped = text.strip()
    if re.match(r"^[\w$]+\(", stripped) and stripped.endswith(")"):
        stripped = stripped[stripped.find("(") + 1 : stripped.rfind(")")]
    return json.loads(stripped)


def fetch_eastmoney_global_news(
    page_size: int = 30,
    timeout: float = 10,
    retries: int = 2,
    min_interval: float = 1.2,
) -> tuple[list[dict[str, Any]], str | None, float]:
    start = time.perf_counter()
    try:
        text = _request_text(
            "https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
            params={
                "client": "web",
                "biz": "web_724",
                "fastColumn": "102",
                "sortEnd": "",
                "pageSize": str(page_size),
                "req_trace": str(uuid.uuid4()),
            },
            headers={"Referer": "https://kuaixun.eastmoney.com/"},
            timeout=timeout,
            retries=retries,
            min_interval=min_interval,
        )
        data = _json_loads(text)
        rows = []
        for item in (data.get("data") or {}).get("fastNewsList") or []:
            title = str(item.get("title") or item.get("summary") or "").strip()
            if not title:
                continue
            rows.append(
                {
                    "title": title,
                    "summary": str(item.get("summary") or "")[:300],
                    "published_at": item.get("showTime") or item.get("updateTime") or "",
                    "source": "东方财富7x24",
                    "source_level": "C",
                    "original_url": item.get("url") or "",
                    "source_root": "eastmoney_global_news",
                    "verification": "PENDING",
                    "is_original": False,
                }
            )
        latency = (time.perf_counter() - start) * 1000
        return rows, None if rows else "东财全球资讯未返回有效新闻", latency
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}", (time.perf_counter() - start) * 1000


def fetch_eastmoney_stock_news(
    symbols: list[str],
    page_size: int = 5,
    timeout: float = 10,
    retries: int = 2,
    min_interval: float = 1.2,
) -> tuple[list[dict[str, Any]], str | None, float]:
    start = time.perf_counter()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for symbol in [normalize_symbol(s) for s in symbols]:
        try:
            callback = "jQuery_news"
            inner = json.dumps(
                {
                    "uid": "",
                    "keyword": symbol,
                    "type": ["cmsArticleWebOld"],
                    "client": "web",
                    "clientType": "web",
                    "clientVersion": "curr",
                    "param": {
                        "cmsArticleWebOld": {
                            "searchScope": "default",
                            "sort": "default",
                            "pageIndex": 1,
                            "pageSize": int(page_size),
                            "preTag": "",
                            "postTag": "",
                        }
                    },
                },
                separators=(",", ":"),
                ensure_ascii=False,
            )
            text = _request_text(
                "https://search-api-web.eastmoney.com/search/jsonp",
                params={"cb": callback, "param": inner},
                headers={"Referer": "https://so.eastmoney.com/"},
                timeout=timeout,
                retries=retries,
                min_interval=min_interval,
            )
            data = _json_loads(text)
            for article in (data.get("result") or {}).get("cmsArticleWebOld") or []:
                title = re.sub(r"<[^>]+>", "", str(article.get("title") or "")).strip()
                if not title:
                    continue
                rows.append(
                    {
                        "title": title,
                        "summary": re.sub(r"<[^>]+>", "", str(article.get("content") or ""))[:300],
                        "published_at": article.get("date") or "",
                        "source": article.get("mediaName") or "东方财富个股新闻",
                        "source_level": "C",
                        "original_url": article.get("url") or "",
                        "source_root": f"eastmoney_stock_news:{symbol}",
                        "symbols": [symbol],
                        "verification": "PENDING",
                        "is_original": False,
                    }
                )
        except Exception as exc:
            errors.append(f"{symbol} {type(exc).__name__}: {exc}")
    latency = (time.perf_counter() - start) * 1000
    if rows:
        return rows, "; ".join(errors[-3:]) if errors else None, latency
    return [], "; ".join(errors[-5:]) or "东财个股新闻未返回有效数据", latency


def fetch_eastmoney_hot_rank(
    top: int = 30,
    timeout: float = 10,
    retries: int = 2,
    min_interval: float = 1.2,
) -> tuple[list[dict[str, Any]], str | None, float]:
    start = time.perf_counter()
    try:
        text = _request_text(
            "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
            json_body={**EASTMONEY_HOT_BODY, "marketType": "", "pageNo": 1, "pageSize": int(top)},
            timeout=timeout,
            retries=retries,
            min_interval=min_interval,
        )
        payload = _json_loads(text)
        data = payload.get("data") or []
        if not data:
            latency = (time.perf_counter() - start) * 1000
            return [], "东财人气榜未返回数据", latency

        secids: list[str] = []
        code_order: list[str] = []
        for item in data:
            sc = str(item.get("sc") or "")
            if len(sc) < 8:
                continue
            market, code = sc[:2].upper(), normalize_symbol(sc[2:])
            secid_prefix = "1." if market == "SH" else "0."
            secids.append(secid_prefix + code)
            code_order.append(code)

        quote_map: dict[str, tuple[str, float | None, float | None]] = {}
        if secids:
            qtext = _request_text(
                "https://push2.eastmoney.com/api/qt/ulist.np/get",
                params={
                    "ut": "f057cbcbce2a86e2866ab8877db1d059",
                    "fltt": 2,
                    "invt": 2,
                    "fields": "f12,f14,f2,f3",
                    "secids": ",".join(secids),
                },
                headers={"Referer": "https://quote.eastmoney.com/"},
                timeout=timeout,
                retries=retries,
                min_interval=min_interval,
            )
            qdata = _json_loads(qtext)
            diff = (qdata.get("data") or {}).get("diff") or []
            if isinstance(diff, dict):
                diff = list(diff.values())
            for row in diff:
                code = normalize_symbol(row.get("f12") or "")
                quote_map[code] = (str(row.get("f14") or ""), _safe_float(row.get("f2")), _safe_float(row.get("f3")))

        rows = []
        for item in data:
            sc = str(item.get("sc") or "")
            code = normalize_symbol(sc[2:] if len(sc) >= 8 else sc)
            if code == "000000":
                continue
            name, price, pct = quote_map.get(code, ("", None, None))
            rows.append(
                {
                    "rank": _safe_int(item.get("rk")),
                    "symbol": code,
                    "name": name,
                    "price": price,
                    "pct": pct,
                    "rank_chg": _safe_int(item.get("hisRc")),
                    "source": "东方财富人气榜",
                }
            )
        latency = (time.perf_counter() - start) * 1000
        return rows, None if rows else "东财人气榜未解析出有效股票", latency
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}", (time.perf_counter() - start) * 1000


def _secid_for_symbol(symbol: str) -> str:
    code = normalize_symbol(symbol)
    if code.startswith(("6", "9")):
        return "1." + code
    return "0." + code


def _parse_kline_rows(data: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in ((data.get("data") or {}).get("klines") or []):
        parts = str(raw).split(",")
        if len(parts) < 3:
            continue
        close = _safe_float(parts[2])
        if close is None or close <= 0:
            continue
        rows.append({"date": parts[0], "close": close})
    rows.sort(key=lambda x: str(x["date"]))
    return rows


def _percentile_rank(values: list[float], current: float) -> float | None:
    clean = [float(x) for x in values if x and x > 0]
    if not clean or current <= 0:
        return None
    return round(sum(1 for x in clean if x <= current) / len(clean) * 100, 2)


def _price_on_or_after(rows: list[dict[str, Any]], target: str | None) -> float | None:
    if not target:
        return None
    normalized = str(target)[:10]
    for row in rows:
        if str(row.get("date")) >= normalized:
            return _safe_float(row.get("close"))
    return None


def _valuation_percentile_proxy(
    *,
    price_percentile: float | None,
    pe_ttm: float | None,
    pb: float | None,
    price_drawdown_pct: float | None,
    post_disclosure_runup_pct: float | None,
) -> float | None:
    if price_percentile is None and pe_ttm is None and pb is None:
        return None
    score = float(price_percentile if price_percentile is not None else 50)
    if pe_ttm is None:
        score += 4
    elif pe_ttm <= 0:
        score = max(score + 25, 65)
    elif pe_ttm < 15:
        score -= 8
    elif pe_ttm > 45:
        score += 12
    if pb is None:
        score += 3
    elif pb <= 0:
        score = max(score + 20, 62)
    elif pb < 1.5:
        score -= 8
    elif pb > 6:
        score += 12
    if price_drawdown_pct is not None:
        if price_drawdown_pct <= -20:
            score -= 5
        elif price_drawdown_pct > -3:
            score += 8
    if post_disclosure_runup_pct is not None and post_disclosure_runup_pct >= 30:
        score += 30
    return round(max(0, min(100, score)), 2)


def fetch_eastmoney_stock_valuation(
    symbols: list[str],
    *,
    disclosure_dates: dict[str, str] | None = None,
    lookback_days: int = 1095,
    timeout: float = 10,
    retries: int = 2,
    min_interval: float = 1.2,
) -> tuple[list[dict[str, Any]], str | None, float]:
    """Fetch current multiples plus a price-history valuation proxy from Eastmoney.

    Eastmoney does not expose a stable official historical PE/PB percentile API here.
    This function therefore labels the result as a valuation proxy: current PE/PB
    adjusted by the stock's own price percentile and drawdown over the lookback window.
    """
    start = time.perf_counter()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    disclosure_dates = disclosure_dates or {}
    begin = (date.today() - timedelta(days=max(int(lookback_days or 1095), 180))).strftime("%Y%m%d")
    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol)
        if symbol == "000000":
            continue
        secid = _secid_for_symbol(symbol)
        try:
            quote_text = _request_text(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={
                    "ut": "f057cbcbce2a86e2866ab8877db1d059",
                    "fltt": 2,
                    "invt": 2,
                    "fields": "f43,f57,f58,f84,f116,f117,f162,f167,f168,f169,f170,f171,f173,f115,f9,f23",
                    "secid": secid,
                },
                headers={"Referer": "https://quote.eastmoney.com/"},
                timeout=timeout,
                retries=retries,
                min_interval=min_interval,
            )
            quote = (_json_loads(quote_text).get("data") or {})
            name = str(quote.get("f58") or "")
            last_price = _safe_float(quote.get("f43"))
            pe_ttm = _safe_float(quote.get("f115")) or _safe_float(quote.get("f162")) or _safe_float(quote.get("f9"))
            pb = _safe_float(quote.get("f167")) or _safe_float(quote.get("f23"))

            kline_text = _request_text(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                params={
                    "secid": secid,
                    "klt": 101,
                    "fqt": 1,
                    "beg": begin,
                    "end": "20500101",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                },
                headers={"Referer": "https://quote.eastmoney.com/"},
                timeout=timeout,
                retries=retries,
                min_interval=min_interval,
            )
            krows = _parse_kline_rows(_json_loads(kline_text))
            closes = [float(x["close"]) for x in krows]
            if closes and not last_price:
                last_price = closes[-1]
            price_percentile = _percentile_rank(closes, float(last_price or 0))
            high = max(closes) if closes else None
            price_drawdown_pct = round((float(last_price) / high - 1) * 100, 2) if high and last_price else None
            base_price = _price_on_or_after(krows, disclosure_dates.get(symbol))
            post_runup = round((float(last_price) / base_price - 1) * 100, 2) if base_price and last_price else None
            valuation_percentile = _valuation_percentile_proxy(
                price_percentile=price_percentile,
                pe_ttm=pe_ttm,
                pb=pb,
                price_drawdown_pct=price_drawdown_pct,
                post_disclosure_runup_pct=post_runup,
            )
            if valuation_percentile is None:
                errors.append(f"{symbol} 无可用估值字段")
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "last_price": last_price,
                    "pe_ttm": pe_ttm,
                    "pb": pb,
                    "price_percentile": price_percentile,
                    "valuation_percentile": valuation_percentile,
                    "price_drawdown_pct": price_drawdown_pct,
                    "post_disclosure_runup_pct": post_runup,
                    "lookback_days": int(lookback_days),
                    "source": "东方财富估值代理",
                    "source_url": f"https://quote.eastmoney.com/{'sh' if secid.startswith('1.') else 'sz'}{symbol}.html",
                    "evidence_status": "PARTIAL_VALUATION_PROXY",
                }
            )
        except Exception as exc:
            errors.append(f"{symbol} {type(exc).__name__}: {exc}")
    latency = (time.perf_counter() - start) * 1000
    if rows:
        return rows, "; ".join(errors[-3:]) if errors else None, latency
    return [], "; ".join(errors[-5:]) or "东财估值源未返回有效数据", latency
