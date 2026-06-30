from __future__ import annotations

import hashlib
import io
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from datetime import date
from typing import Any

from ..utils import normalize_symbol


BASE_URL = "http://eid.csrc.gov.cn"
DISCLOSE_URL = f"{BASE_URL}/fund/disclose"
UA = "Mozilla/5.0 AStockWebTerminal/4.1"


def _request_bytes(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 12,
    retries: int = 2,
    min_interval: float = 0.6,
) -> bytes:
    url = path if path.startswith("http") else f"{DISCLOSE_URL}/{path.lstrip('/')}"
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}{'&' if '?' in url else '?'}{query}"
    errors: list[str] = []
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/html, application/pdf, */*",
        "Referer": f"{DISCLOSE_URL}/index.html",
    }
    for attempt in range(max(retries, 0) + 1):
        if min_interval:
            time.sleep(max(0, min_interval) + random.uniform(0.05, 0.18))
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            errors.append(f"HTTP_{exc.code}")
            if exc.code in {401, 403, 404}:
                break
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        if attempt < retries:
            time.sleep(0.35 * (attempt + 1))
    raise RuntimeError("; ".join(errors[-4:]) or "request failed")


def _request_text(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 12,
    retries: int = 2,
    min_interval: float = 0.6,
) -> str:
    return _request_bytes(path, params=params, timeout=timeout, retries=retries, min_interval=min_interval).decode("utf-8", errors="replace")


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    num = _safe_float(value)
    return int(num) if num is not None else None


def _parse_cn_date(value: Any) -> date | None:
    text = _clean_cell(value)
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if not match:
        match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _period_end(report_period: str) -> date | None:
    match = re.fullmatch(r"(\d{4})Q([1-4])", report_period or "")
    if not match:
        return None
    year = int(match.group(1))
    quarter = int(match.group(2))
    return {
        1: date(year, 3, 31),
        2: date(year, 6, 30),
        3: date(year, 9, 30),
        4: date(year, 12, 31),
    }[quarter]


def _years_between(start: date | None, end: date | None) -> float | None:
    if not start or not end or start > end:
        return None
    return round((end - start).days / 365.25, 2)


def _clean_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", "", unescape(text)).strip()
    if not text or text in {"-", "--"}:
        return ""
    if text.count("\ufffd") >= 1 or text.count("?") >= max(3, len(text) // 2):
        return ""
    return text


def _stable_manager_id(name: str, company: str) -> str:
    raw = f"{name}|{company}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _html_text(value: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def parse_fund_detail_html(html: str) -> dict[str, str]:
    def after_label(label: str) -> str:
        pattern = rf"<td[^>]*>\s*{re.escape(label)}\s*</td>\s*<td[^>]*>(.*?)</td>"
        match = re.search(pattern, html, flags=re.I | re.S)
        return _html_text(match.group(1)) if match else ""

    name = ""
    match = re.search(r'id=["\']sp_fundName["\'][^>]*>(.*?)</td>', html, flags=re.I | re.S)
    if match:
        name = _html_text(match.group(1))
    return {
        "name": name or after_label("基金名称"),
        "company": after_label("基金管理人"),
        "category": after_label("基金类别"),
        "trustee": after_label("基金托管人"),
        "contract_start": after_label("基金合同生效日期"),
    }


def report_period_from_row(row: dict[str, Any]) -> str:
    year = str(row.get("reportYear") or "")[:4]
    text = f"{row.get('reportDesp') or ''} {row.get('reportName') or ''} {row.get('reportCode') or ''}"
    quarter = ""
    if "第一季度" in text or str(row.get("reportCode", "")).endswith("010"):
        quarter = "Q1"
    elif "第二季度" in text or "中期" in text or str(row.get("reportCode", "")).endswith("020"):
        quarter = "Q2"
    elif "第三季度" in text or str(row.get("reportCode", "")).endswith("030"):
        quarter = "Q3"
    elif "第四季度" in text or "年度" in text or str(row.get("reportCode", "")).endswith("040"):
        quarter = "Q4"
    return f"{year}{quarter}" if year and quarter else year


def _ao_data(fund_code: str, report_year: str | None = None, start: int = 0, length: int = 20) -> list[dict[str, Any]]:
    return [
        {"name": "sEcho", "value": 1},
        {"name": "iColumns", "value": 6},
        {"name": "sColumns", "value": ""},
        {"name": "iDisplayStart", "value": start},
        {"name": "iDisplayLength", "value": length},
        {"name": "mDataProp_0", "value": "fundCode"},
        {"name": "mDataProp_1", "value": "fundId"},
        {"name": "mDataProp_2", "value": "reportName"},
        {"name": "mDataProp_3", "value": "organName"},
        {"name": "mDataProp_4", "value": "reportDesp"},
        {"name": "mDataProp_5", "value": "reportSendDate"},
        {"name": "fundType", "value": ""},
        {"name": "reportType", "value": "FB030"},
        {"name": "reportYear", "value": report_year or ""},
        {"name": "fundCompanyShortName", "value": ""},
        {"name": "fundCode", "value": fund_code},
        {"name": "fundShortName", "value": ""},
        {"name": "startUploadDate", "value": ""},
        {"name": "endUploadDate", "value": ""},
    ]


def query_quarterly_reports(
    fund_code: str,
    *,
    report_years: list[str] | None = None,
    timeout: float = 12,
    retries: int = 2,
    min_interval: float = 0.6,
) -> list[dict[str, Any]]:
    years = report_years or [""]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for year in years:
        payload = {"aoData": json.dumps(_ao_data(fund_code, str(year) if year else ""), ensure_ascii=False, separators=(",", ":"))}
        text = _request_text(
            "advanced_search_report.do",
            params=payload,
            timeout=timeout,
            retries=retries,
            min_interval=min_interval,
        )
        data = json.loads(text)
        for row in data.get("aaData") or []:
            key = str(row.get("uploadInfoId") or row.get("idStr") or "")
            if key and key not in seen:
                seen.add(key)
                rows.append(row)
    rows.sort(key=lambda x: (report_period_from_row(x), str(x.get("reportSendDate") or x.get("uploadDate") or "")), reverse=True)
    return rows


def fetch_fund_detail(fund_id: Any, *, timeout: float = 12, retries: int = 2, min_interval: float = 0.6) -> dict[str, str]:
    if not fund_id:
        return {}
    html = _request_text(f"fund_detail.do?fundId={urllib.parse.quote(str(fund_id))}", timeout=timeout, retries=retries, min_interval=min_interval)
    return parse_fund_detail_html(html)


def discover_latest_quarterly_fund_codes(
    *,
    limit: int = 20,
    timeout: float = 12,
    retries: int = 2,
    min_interval: float = 0.6,
) -> tuple[list[str], str | None, float]:
    start = time.perf_counter()
    try:
        text = _request_text("indexPublicData.json", timeout=timeout, retries=retries, min_interval=min_interval)
        data = json.loads(text)
        codes: list[str] = []
        for row in data.get("seasonReportList") or []:
            code = normalize_symbol(row.get("fundcode") or row.get("fundCode") or "")
            if code != "000000" and code not in codes:
                codes.append(code)
            if len(codes) >= max(1, int(limit or 1)):
                break
        latency = (time.perf_counter() - start) * 1000
        return codes, None if codes else "官方首页未返回季报基金代码", latency
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}", (time.perf_counter() - start) * 1000


def _pdf_parser_name() -> str | None:
    try:
        import pdfplumber  # noqa: F401

        return "pdfplumber"
    except Exception:
        pass
    try:
        import pypdf  # noqa: F401

        return "pypdf"
    except Exception:
        return None


def parse_stock_holdings_from_text(text: str, source_page: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    line_pattern = re.compile(
        r"^\s*(?P<rank>\d{1,2})\s+(?P<symbol>\d{6})\s+(?P<name>\S{0,16})\s+"
        r"(?P<shares>[-\d,.]+)\s+(?P<market_value>[-\d,.]+)\s+(?P<nav_ratio>[-\d,.]+)\s*$"
    )
    for line in text.splitlines():
        match = line_pattern.match(line)
        if not match:
            continue
        symbol = normalize_symbol(match.group("symbol"))
        if symbol == "000000" or symbol in seen:
            continue
        seen.add(symbol)
        rows.append(
            {
                "symbol": symbol,
                "name": _clean_cell(match.group("name")),
                "holding_rank": _safe_int(match.group("rank")),
                "shares": _safe_float(match.group("shares")),
                "market_value": _safe_float(match.group("market_value")),
                "nav_ratio": _safe_float(match.group("nav_ratio")),
                "source_page": source_page,
                "holding_status": "VISIBLE",
                "evidence_status": "VERIFIED",
            }
        )
    return rows


def _parse_table_row(row: list[Any], source_page: str) -> dict[str, Any] | None:
    cells = [_clean_cell(x) for x in row]
    if not any(cells):
        return None
    symbol_index = next((idx for idx, cell in enumerate(cells[:4]) if re.fullmatch(r"\d{6}", cell or "")), None)
    if symbol_index is None:
        return None
    symbol = normalize_symbol(cells[symbol_index])
    if symbol == "000000":
        return None
    rank = _safe_int(cells[symbol_index - 1]) if symbol_index > 0 else None
    name = cells[symbol_index + 1] if symbol_index + 1 < len(cells) else ""
    numbers = [_safe_float(cell) for cell in cells[symbol_index + 2 :]]
    numbers = [n for n in numbers if n is not None]
    if len(numbers) < 2:
        return None
    shares = numbers[-3] if len(numbers) >= 3 else None
    market_value = numbers[-2]
    nav_ratio = numbers[-1]
    return {
        "symbol": symbol,
        "name": name,
        "holding_rank": rank,
        "shares": shares,
        "market_value": market_value,
        "nav_ratio": nav_ratio,
        "source_page": source_page,
        "holding_status": "VISIBLE",
        "evidence_status": "VERIFIED",
    }


def parse_stock_holdings_from_pdf(pdf_bytes: bytes) -> tuple[list[dict[str, Any]], str]:
    parser = _pdf_parser_name()
    if not parser:
        return [], "PDF_PARSER_MISSING"
    holdings: list[dict[str, Any]] = []
    seen: set[str] = set()
    if parser == "pdfplumber":
        import pdfplumber

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                source_page = f"PDF_PAGE_{page_index}"
                for table in page.extract_tables() or []:
                    for row in table or []:
                        item = _parse_table_row(row or [], source_page)
                        if item and item["symbol"] not in seen:
                            seen.add(item["symbol"])
                            holdings.append(item)
                text = page.extract_text() or ""
                for item in parse_stock_holdings_from_text(text, source_page):
                    if item["symbol"] not in seen:
                        seen.add(item["symbol"])
                        holdings.append(item)
    else:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        for page_index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            source_page = f"PDF_PAGE_{page_index}"
            for item in parse_stock_holdings_from_text(text, source_page):
                if item["symbol"] not in seen:
                    seen.add(item["symbol"])
                    holdings.append(item)
    holdings.sort(key=lambda x: x.get("holding_rank") or 999)
    return holdings[:10], "PDF_PARSED" if holdings else "PDF_NO_HOLDINGS_PARSED"


def parse_fund_manager_rows(
    rows: list[list[Any]],
    *,
    company: str,
    report_period: str,
    source_url: str,
) -> list[dict[str, Any]]:
    period_end = _period_end(report_period)
    managers: list[dict[str, Any]] = []
    for row in rows:
        cells = [_clean_cell(x) for x in row]
        if len(cells) < 3:
            continue
        name = cells[0]
        role = cells[1]
        if not name or "基金经理" not in role:
            continue
        if not re.fullmatch(r"[\u4e00-\u9fff·]{2,8}", name):
            continue
        start_date = _parse_cn_date(cells[2])
        end_date = _parse_cn_date(cells[3]) if len(cells) > 3 else None
        if period_end and start_date and start_date > period_end:
            continue
        if period_end and end_date and end_date < period_end:
            continue
        tenure = _years_between(start_date, period_end)
        score = 60 + min((tenure or 0) * 3.0, 24) + (5 if company else 0)
        managers.append(
            {
                "manager_id": _stable_manager_id(name, company),
                "name": name,
                "company": company,
                "score": round(min(score, 92), 2),
                "tenure_years": tenure,
                "report_period": report_period,
                "source_url": source_url,
                "evidence_status": "VERIFIED",
            }
        )
    return managers


def parse_fund_managers_from_pdf(
    pdf_bytes: bytes,
    *,
    company: str,
    report_period: str,
    source_url: str,
) -> list[dict[str, Any]]:
    try:
        import pdfplumber
    except Exception:
        return []
    managers: list[dict[str, Any]] = []
    seen: set[str] = set()
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                parsed = parse_fund_manager_rows(table or [], company=company, report_period=report_period, source_url=source_url)
                for manager in parsed:
                    if manager["manager_id"] not in seen:
                        seen.add(manager["manager_id"])
                        managers.append(manager)
    return managers


def _report_sort_key(period: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})Q([1-4])", period or "")
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def _annotate_changes(payload: dict[str, Any]) -> None:
    reports_by_id = {item["report_id"]: item for item in payload.get("reports", [])}
    holdings_by_report: dict[str, list[dict[str, Any]]] = {}
    for item in payload.get("holdings", []):
        holdings_by_report.setdefault(item.get("report_id", ""), []).append(item)
    reports_by_fund: dict[str, list[dict[str, Any]]] = {}
    for report in payload.get("reports", []):
        reports_by_fund.setdefault(report.get("fund_code", ""), []).append(report)
    for reports in reports_by_fund.values():
        reports.sort(key=lambda x: _report_sort_key(x.get("report_period", "")))
        previous: dict[str, dict[str, Any]] = {}
        for report in reports:
            current = holdings_by_report.get(report["report_id"], [])
            for item in current:
                prev = previous.get(item["symbol"])
                if prev and item.get("shares") is not None and prev.get("shares") is not None:
                    item["previous_shares"] = prev.get("shares")
                    item["change_shares"] = round(float(item["shares"]) - float(prev["shares"]), 4)
                    item["confirmed_increase"] = 1 if item["change_shares"] > 0 else 0
                    item["holding_status"] = "INCREASE" if item["confirmed_increase"] else "VISIBLE"
                elif previous and (item.get("holding_rank") or 999) <= 10:
                    item["visible_new"] = 1
                    item["holding_status"] = "VISIBLE_NEW"
            previous = {item["symbol"]: item for item in current}
    for item in payload.get("holdings", []):
        report = reports_by_id.get(item.get("report_id", ""), {})
        item.setdefault("fund_code", report.get("fund_code", ""))
        item.setdefault("report_period", report.get("report_period", ""))


def fetch_official_fund_reports(
    fund_codes: list[str],
    *,
    max_reports_per_fund: int = 4,
    report_years: list[str] | None = None,
    parse_pdf: bool = True,
    timeout: float = 12,
    retries: int = 2,
    min_interval: float = 0.6,
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None, float]:
    start = time.perf_counter()
    payload: dict[str, Any] = {"managers": [], "products": [], "reports": [], "holdings": []}
    outcomes: list[dict[str, Any]] = []
    errors: list[str] = []
    manager_seen: set[str] = set()
    for raw_code in fund_codes:
        fund_code = normalize_symbol(raw_code)
        if fund_code == "000000":
            outcomes.append({"fund_code": raw_code, "status": "INVALID_CODE"})
            continue
        try:
            rows = query_quarterly_reports(
                fund_code,
                report_years=report_years,
                timeout=timeout,
                retries=retries,
                min_interval=min_interval,
            )
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            errors.append(f"{fund_code} {err}")
            outcomes.append({"fund_code": fund_code, "status": "LIST_FAILED", "error": err})
            continue
        if not rows:
            outcomes.append({"fund_code": fund_code, "status": "NO_REPORTS"})
            continue
        detail: dict[str, str] = {}
        fund_id = rows[0].get("fundId")
        try:
            detail = fetch_fund_detail(fund_id, timeout=timeout, retries=retries, min_interval=min_interval)
        except Exception as exc:
            errors.append(f"{fund_code} detail {type(exc).__name__}: {exc}")
        product_name = detail.get("name") or rows[0].get("fundShortName") or ""
        company = detail.get("company") or rows[0].get("organName") or ""
        product_record = {
            "fund_code": fund_code,
            "name": product_name,
            "company": company,
            "category": detail.get("category", ""),
            "source_url": f"{DISCLOSE_URL}/fund_detail.do?fundId={fund_id}" if fund_id else f"{DISCLOSE_URL}/index.html",
            "evidence_status": "VERIFIED",
        }
        payload["products"].append(product_record)
        imported_reports = 0
        for row in rows[: max(1, int(max_reports_per_fund or 1))]:
            upload_id = row.get("uploadInfoId") or row.get("idStr")
            if not upload_id:
                continue
            report_id = f"eid-{fund_code}-{upload_id}"
            report_period = report_period_from_row(row)
            pdf_url = f"{DISCLOSE_URL}/instance_show_pdf_id.do?instanceid={upload_id}"
            pdf_sha = ""
            parser_status = "OFFICIAL_METADATA_ONLY"
            holdings: list[dict[str, Any]] = []
            managers: list[dict[str, Any]] = []
            if parse_pdf:
                try:
                    pdf_bytes = _request_bytes(
                        f"instance_show_pdf_id.do?instanceid={upload_id}",
                        timeout=timeout,
                        retries=retries,
                        min_interval=min_interval,
                    )
                    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
                    holdings, parser_status = parse_stock_holdings_from_pdf(pdf_bytes)
                    managers = parse_fund_managers_from_pdf(pdf_bytes, company=company or row.get("organName", ""), report_period=report_period, source_url=pdf_url)
                except Exception as exc:
                    parser_status = "PDF_DOWNLOAD_OR_PARSE_FAILED"
                    errors.append(f"{fund_code} {upload_id} {type(exc).__name__}: {exc}")
            else:
                parser_status = "PDF_PARSE_DISABLED"
            if managers and not product_record.get("manager_name"):
                product_record["manager_name"] = "、".join(x["name"] for x in managers)
                product_record["manager_id"] = "team-" + hashlib.sha256(product_record["manager_name"].encode("utf-8")).hexdigest()[:12]
            for manager in managers:
                if manager["manager_id"] not in manager_seen:
                    manager_seen.add(manager["manager_id"])
                    payload["managers"].append(manager)
            team_name = "、".join(x["name"] for x in managers)
            team_id = "team-" + hashlib.sha256(team_name.encode("utf-8")).hexdigest()[:12] if team_name else ""
            report = {
                "report_id": report_id,
                "fund_code": fund_code,
                "fund_name": product_name or row.get("fundShortName", ""),
                "manager_id": team_id,
                "manager_name": team_name,
                "company": company or row.get("organName", ""),
                "report_period": report_period,
                "report_type": "quarterly",
                "announcement_date": row.get("reportSendDate") or row.get("uploadDate") or "",
                "source_url": pdf_url,
                "pdf_sha256": pdf_sha,
                "parser_status": parser_status,
                "coverage": min(len(holdings) / 10, 1.0) if holdings else 0,
                "evidence_status": "VERIFIED",
            }
            payload["reports"].append(report)
            for holding in holdings:
                payload["holdings"].append({**holding, "report_id": report_id, "fund_code": fund_code, "report_period": report_period})
            imported_reports += 1
            outcomes.append(
                {
                    "fund_code": fund_code,
                    "report_id": report_id,
                    "report_period": report_period,
                    "status": parser_status,
                    "holdings": len(holdings),
                    "source_url": pdf_url,
                }
            )
        if not imported_reports:
            outcomes.append({"fund_code": fund_code, "status": "NO_IMPORTABLE_REPORTS"})
    _annotate_changes(payload)
    latency = (time.perf_counter() - start) * 1000
    error = "; ".join(errors[-6:]) if errors else None
    return payload, outcomes, error, latency
