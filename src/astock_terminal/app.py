from __future__ import annotations

import argparse
import base64
import binascii
import concurrent.futures
import csv
import hmac
from contextlib import closing
from email.utils import parsedate_to_datetime
import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from .models import Quote
from .providers import discover_latest_quarterly_fund_codes as provider_discover_latest_quarterly_fund_codes
from .providers import fetch_eastmoney_global_news as provider_fetch_global_news
from .providers import fetch_eastmoney_hot_rank as provider_fetch_hot_rank
from .providers import fetch_eastmoney_stock_news as provider_fetch_stock_news
from .providers import fetch_eastmoney_stock_valuation as provider_fetch_stock_valuation
from .providers import fetch_official_fund_reports as provider_fetch_official_fund_reports
from .providers import fetch_mootdx as provider_fetch_mootdx
from .providers import fetch_tencent as provider_fetch_tencent
from .utils import CN_TZ, exchange_of, normalize_symbol, now_iso, read_json, tencent_prefix

PACKAGE_ROOT = Path(__file__).resolve().parent
STATIC = PACKAGE_ROOT / "static"
APP_HOME = Path(os.environ.get("ASTOCK_HOME", Path.home() / ".astock_terminal")).resolve()
DATA = APP_HOME / "data"
DB_PATH = DATA / "terminal.db"
CONFIG_PATH = APP_HOME / "config.json"
LEGACY_OWNER_ID = "local"
SYSTEM_OWNER_ID = "system"
SESSION_COOKIE = "astock_session"
SESSION_DAYS = 30


DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 8765,
    "bootstrap_watchlist": ["510300", "510500", "159915", "588000", "512100"],
    "refresh_seconds": 10,
    "quote_stale_seconds": 20,
    "quote_retry_count": 2,
    "quote_batch_size": 60,
    "price_warn_deviation": 0.002,
    "price_block_deviation": 0.005,
    "mootdx_server": "",
    "rss_sources": [],
    "json_news_sources": [],
    "news_timeout_seconds": 8,
    "news_retry_count": 2,
    "a_stock_data": {
        "enabled": True,
        "eastmoney_min_interval": 1.2,
        "news_enabled": True,
        "global_news_page_size": 30,
        "stock_news_symbols": 5,
        "stock_news_page_size": 5,
        "signal_enabled": True,
        "hot_rank_top": 30,
        "signal_watchlist_limit": 10,
        "valuation_enabled": True,
        "valuation_lookback_days": 1095,
        "valuation_symbol_limit": 40,
        "undervalued_percentile": 35,
        "background_refresh": True,
        "news_refresh_seconds": 120,
        "signal_refresh_seconds": 300,
        "valuation_refresh_seconds": 3600
    },
    "fund_report_sync": {
        "enabled": False,
        "fund_codes": [],
        "discover_latest": False,
        "discover_latest_limit": 20,
        "max_reports_per_fund": 4,
        "report_years": [],
        "parse_pdf": True,
        "official_min_interval": 0.6,
        "structured_json_sources": [],
        "check_interval_seconds": 86400,
        "report_months": [1, 4, 7, 10],
        "evidence_required": True
    },
    "ocr": {
        "tesseract_cmd": "",
        "languages": "chi_sim+eng"
    },
    "notification": {
        "wecom_webhook": "",
        "dingtalk_webhook": "",
        "feishu_webhook": "",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "generic_webhook": ""
    }
}


def config() -> dict[str, Any]:
    loaded = read_json(CONFIG_PATH, {})
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in loaded.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged


def a_stock_data_config() -> dict[str, Any]:
    cfg = config().get("a_stock_data") or {}
    defaults = DEFAULT_CONFIG["a_stock_data"]
    return {**defaults, **cfg}


def fund_report_sync_config() -> dict[str, Any]:
    cfg = config().get("fund_report_sync") or {}
    defaults = DEFAULT_CONFIG["fund_report_sync"]
    return {**defaults, **cfg}


def is_equity_symbol(symbol: str) -> bool:
    s = normalize_symbol(symbol)
    if s.startswith(("5", "1")):
        return False
    if s.startswith(("000", "399")):
        return False
    return s != "000000"


class Database:
    def __init__(self, path: Path = DB_PATH):
        DATA.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.init()

    def connect(self):
        con = sqlite3.connect(self.path, timeout=20, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def init(self):
        with closing(self.connect()) as con:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users(
                    user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, role TEXT DEFAULT 'user',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions(
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_settings(
                    owner_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                    PRIMARY KEY(owner_id, key)
                );
                CREATE TABLE IF NOT EXISTS watchlist(
                    owner_id TEXT NOT NULL DEFAULT 'local',
                    symbol TEXT NOT NULL, name TEXT DEFAULT '',
                    PRIMARY KEY(owner_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS positions(
                    owner_id TEXT NOT NULL DEFAULT 'local',
                    symbol TEXT NOT NULL, name TEXT DEFAULT '', theme TEXT DEFAULT '未分类',
                    asset_type TEXT DEFAULT 'stock', current_price REAL,
                    quantity REAL NOT NULL, average_cost REAL NOT NULL,
                    stop_price REAL, take_profit_price REAL, score REAL DEFAULT 50,
                    note TEXT DEFAULT '', updated_at TEXT NOT NULL,
                    PRIMARY KEY(owner_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS quotes(
                    provider TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT DEFAULT '',
                    last_price REAL, prev_close REAL, open_price REAL, high REAL, low REAL,
                    volume REAL, amount REAL, bid1 REAL, ask1 REAL,
                    quote_time TEXT, fetch_time TEXT, status TEXT, error TEXT,
                    PRIMARY KEY(provider, symbol)
                );
                CREATE TABLE IF NOT EXISTS quote_validation(
                    symbol TEXT PRIMARY KEY, name TEXT DEFAULT '', last_price REAL,
                    level TEXT NOT NULL, primary_provider TEXT, secondary_provider TEXT,
                    deviation REAL, reasons TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fund_managers(
                    manager_id TEXT PRIMARY KEY, name TEXT NOT NULL, company TEXT NOT NULL,
                    score REAL NOT NULL, tenure_years REAL, report_period TEXT,
                    source_url TEXT, evidence_status TEXT DEFAULT 'UNVERIFIED', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fund_products(
                    fund_code TEXT PRIMARY KEY, name TEXT DEFAULT '', company TEXT DEFAULT '',
                    manager_id TEXT DEFAULT '', manager_name TEXT DEFAULT '',
                    category TEXT DEFAULT '', strategy_track TEXT DEFAULT '',
                    benchmark TEXT DEFAULT '', asset_size REAL, fee_rate REAL,
                    source_url TEXT DEFAULT '', evidence_status TEXT DEFAULT 'UNVERIFIED',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fund_reports(
                    report_id TEXT PRIMARY KEY, fund_code TEXT DEFAULT '', fund_name TEXT DEFAULT '',
                    manager_id TEXT DEFAULT '', manager_name TEXT DEFAULT '', company TEXT DEFAULT '',
                    report_period TEXT NOT NULL, report_type TEXT DEFAULT 'quarterly',
                    announcement_date TEXT DEFAULT '', source_url TEXT DEFAULT '',
                    pdf_sha256 TEXT DEFAULT '', parser_status TEXT DEFAULT 'IMPORTED',
                    coverage REAL DEFAULT 0, evidence_status TEXT DEFAULT 'UNVERIFIED',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fund_report_holdings(
                    report_id TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT DEFAULT '',
                    industry TEXT DEFAULT '', holding_rank INTEGER,
                    market_value REAL, nav_ratio REAL, shares REAL,
                    previous_shares REAL, change_shares REAL, change_ratio REAL,
                    holding_status TEXT DEFAULT 'VISIBLE',
                    visible_new INTEGER DEFAULT 0, confirmed_increase INTEGER DEFAULT 0,
                    source_page TEXT DEFAULT '', evidence_status TEXT DEFAULT 'UNVERIFIED',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(report_id, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_fund_reports_period ON fund_reports(report_period);
                CREATE INDEX IF NOT EXISTS idx_fund_holdings_symbol ON fund_report_holdings(symbol);
                CREATE TABLE IF NOT EXISTS fund_consensus(
                    symbol TEXT PRIMARY KEY, name TEXT DEFAULT '', report_period TEXT,
                    manager_count INTEGER DEFAULT 0, company_count INTEGER DEFAULT 0,
                    confirmed_increase INTEGER DEFAULT 0, new_visible INTEGER DEFAULT 0,
                    consecutive_increase INTEGER DEFAULT 0, excellent_manager_count INTEGER DEFAULT 0,
                    style_count INTEGER DEFAULT 0, triple_confirm_status TEXT DEFAULT 'UNVERIFIED',
                    consensus_score REAL DEFAULT 0, source_url TEXT,
                    evidence_status TEXT DEFAULT 'UNVERIFIED', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stock_scores(
                    symbol TEXT PRIMARY KEY, name TEXT DEFAULT '', industry TEXT DEFAULT '',
                    quality REAL, growth REAL, valuation REAL, trend REAL, risk REAL,
                    fund_signal REAL, total_score REAL, grade TEXT, data_date TEXT,
                    manager_signal REAL, fundamental_signal REAL, valuation_signal REAL,
                    triple_confirm_status TEXT DEFAULT 'UNVERIFIED', exclusion_flags TEXT DEFAULT '[]',
                    scoring_notes TEXT DEFAULT '',
                    source_status TEXT DEFAULT 'UNVERIFIED', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stock_due_diligence(
                    symbol TEXT PRIMARY KEY, name TEXT DEFAULT '',
                    profit_trend REAL, cashflow_quality REAL, debt_risk REAL,
                    industry_outlook REAL, competitive_position REAL,
                    valuation_percentile REAL, price_drawdown_pct REAL, post_disclosure_runup_pct REAL,
                    earnings_decline INTEGER DEFAULT 0,
                    receivable_inventory_goodwill_risk INTEGER DEFAULT 0,
                    governance_risk INTEGER DEFAULT 0,
                    industry_decline_risk INTEGER DEFAULT 0,
                    source_url TEXT DEFAULT '', evidence_status TEXT DEFAULT 'UNVERIFIED',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stock_valuations(
                    symbol TEXT PRIMARY KEY, name TEXT DEFAULT '',
                    pe_ttm REAL, pb REAL, price_percentile REAL,
                    valuation_percentile REAL, price_drawdown_pct REAL,
                    post_disclosure_runup_pct REAL, lookback_days INTEGER DEFAULT 1095,
                    source TEXT DEFAULT '', source_url TEXT DEFAULT '',
                    evidence_status TEXT DEFAULT 'UNVERIFIED', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS news_events(
                    event_id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                    source_level TEXT DEFAULT 'C', published_at TEXT, original_url TEXT,
                    symbols TEXT DEFAULT '[]', themes TEXT DEFAULT '[]',
                    opportunity_score REAL DEFAULT 0, risk_score REAL DEFAULT 0,
                    verification TEXT DEFAULT 'UNVERIFIED', is_original INTEGER DEFAULT 1,
                    source_root TEXT DEFAULT '', fetched_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alerts(
                    owner_id TEXT NOT NULL DEFAULT 'local',
                    alert_id TEXT PRIMARY KEY, category TEXT NOT NULL, severity TEXT NOT NULL,
                    symbol TEXT, title TEXT NOT NULL, message TEXT NOT NULL, action TEXT NOT NULL,
                    created_at TEXT NOT NULL, acknowledged INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS source_health(
                    name TEXT PRIMARY KEY, status TEXT NOT NULL, latency_ms REAL,
                    last_success TEXT, error TEXT, updated_at TEXT NOT NULL
                );
                """
            )
            self.migrate_owner_tables(con)
            self.ensure_columns(con, "fund_consensus", {
                "consecutive_increase": "INTEGER DEFAULT 0",
                "excellent_manager_count": "INTEGER DEFAULT 0",
                "style_count": "INTEGER DEFAULT 0",
                "triple_confirm_status": "TEXT DEFAULT 'UNVERIFIED'",
            })
            self.ensure_columns(con, "stock_scores", {
                "manager_signal": "REAL",
                "fundamental_signal": "REAL",
                "valuation_signal": "REAL",
                "triple_confirm_status": "TEXT DEFAULT 'UNVERIFIED'",
                "exclusion_flags": "TEXT DEFAULT '[]'",
                "scoring_notes": "TEXT DEFAULT ''",
            })
            self.ensure_columns(con, "positions", {
                "owner_id": f"TEXT DEFAULT '{LEGACY_OWNER_ID}'",
                "asset_type": "TEXT DEFAULT 'stock'",
                "current_price": "REAL",
            })
            self.ensure_columns(con, "alerts", {
                "owner_id": f"TEXT DEFAULT '{LEGACY_OWNER_ID}'",
            })
            defaults = {
                "total_capital": 100000,
                "risk_profile": "balanced",
                "default_stop_loss_pct": 0.06,
                "notifications_enabled": False,
                "opportunity_min_score": 70,
                "opportunity_max_risk": 35,
                "market_regime": "neutral"
            }
            for k, v in defaults.items():
                con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)))
            if con.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 0:
                for symbol in DEFAULT_CONFIG["bootstrap_watchlist"]:
                    con.execute("INSERT OR IGNORE INTO watchlist(owner_id,symbol) VALUES(?,?)", (SYSTEM_OWNER_ID, normalize_symbol(symbol),))
            con.commit()

    @staticmethod
    def table_columns(con: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
        return {row[1]: row for row in con.execute(f"PRAGMA table_info({table})").fetchall()}

    def migrate_owner_tables(self, con: sqlite3.Connection) -> None:
        watch_cols = self.table_columns(con, "watchlist")
        watch_pk = [name for name, row in watch_cols.items() if row[5]]
        if "owner_id" not in watch_cols or watch_pk != ["owner_id", "symbol"]:
            old = f"watchlist_legacy_{int(time.time() * 1000)}"
            con.execute(f"ALTER TABLE watchlist RENAME TO {old}")
            con.execute(
                """CREATE TABLE watchlist(
                    owner_id TEXT NOT NULL DEFAULT 'local',
                    symbol TEXT NOT NULL, name TEXT DEFAULT '',
                    PRIMARY KEY(owner_id, symbol)
                )"""
            )
            for row in con.execute(f"SELECT * FROM {old}").fetchall():
                owner_id = row["owner_id"] if "owner_id" in row.keys() and row["owner_id"] else LEGACY_OWNER_ID
                con.execute(
                    "INSERT OR IGNORE INTO watchlist(owner_id,symbol,name) VALUES(?,?,?)",
                    (owner_id, normalize_symbol(row["symbol"]), row["name"] if "name" in row.keys() else ""),
                )

        pos_cols = self.table_columns(con, "positions")
        pos_pk = [name for name, row in pos_cols.items() if row[5]]
        if "owner_id" not in pos_cols or pos_pk != ["owner_id", "symbol"]:
            old = f"positions_legacy_{int(time.time() * 1000)}"
            con.execute(f"ALTER TABLE positions RENAME TO {old}")
            con.execute(
                """CREATE TABLE positions(
                    owner_id TEXT NOT NULL DEFAULT 'local',
                    symbol TEXT NOT NULL, name TEXT DEFAULT '', theme TEXT DEFAULT '未分类',
                    asset_type TEXT DEFAULT 'stock', current_price REAL,
                    quantity REAL NOT NULL, average_cost REAL NOT NULL,
                    stop_price REAL, take_profit_price REAL, score REAL DEFAULT 50,
                    note TEXT DEFAULT '', updated_at TEXT NOT NULL,
                    PRIMARY KEY(owner_id, symbol)
                )"""
            )
            for row in con.execute(f"SELECT * FROM {old}").fetchall():
                keys = set(row.keys())
                owner_id = row["owner_id"] if "owner_id" in keys and row["owner_id"] else LEGACY_OWNER_ID
                con.execute(
                    """INSERT OR REPLACE INTO positions(owner_id,symbol,name,theme,asset_type,current_price,quantity,average_cost,stop_price,take_profit_price,score,note,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        owner_id, row["symbol"], row["name"] if "name" in keys else "",
                        row["theme"] if "theme" in keys else "未分类",
                        row["asset_type"] if "asset_type" in keys else "stock",
                        row["current_price"] if "current_price" in keys else None,
                        row["quantity"], row["average_cost"],
                        row["stop_price"] if "stop_price" in keys else None,
                        row["take_profit_price"] if "take_profit_price" in keys else None,
                        row["score"] if "score" in keys else 50,
                        row["note"] if "note" in keys else "",
                        row["updated_at"] if "updated_at" in keys else now_iso(),
                    ),
                )

    @staticmethod
    def ensure_columns(con: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.lock, closing(self.connect()) as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple = ()):
        with self.lock, closing(self.connect()) as con:
            con.execute(sql, params)
            con.commit()

    def executemany(self, sql: str, rows: list[tuple]):
        with self.lock, closing(self.connect()) as con:
            con.executemany(sql, rows)
            con.commit()

    def settings(self, user_id: str | None = None) -> dict[str, Any]:
        out = {}
        for row in self.all("SELECT key,value FROM settings"):
            try:
                out[row["key"]] = json.loads(row["value"])
            except Exception:
                out[row["key"]] = row["value"]
        if user_id:
            for row in self.all("SELECT key,value FROM user_settings WHERE owner_id=?", (user_id,)):
                try:
                    out[row["key"]] = json.loads(row["value"])
                except Exception:
                    out[row["key"]] = row["value"]
        return out

    def set_settings(self, values: dict[str, Any], user_id: str | None = None):
        rows = [(k, json.dumps(v, ensure_ascii=False)) for k, v in values.items()]
        if user_id:
            scoped = [(user_id, key, value) for key, value in rows]
            self.executemany(
                "INSERT INTO user_settings(owner_id,key,value) VALUES(?,?,?) ON CONFLICT(owner_id,key) DO UPDATE SET value=excluded.value",
                scoped,
            )
        else:
            self.executemany("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", rows)


DB = Database()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_hash(password: str, salt: bytes | None = None, iterations: int = 200_000) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        expected = password_hash(password, base64.urlsafe_b64decode(salt_b64.encode("ascii")), int(iterations))
        return hmac.compare_digest(expected, stored)
    except Exception:
        return False


def normalize_username(value: Any) -> str:
    username = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise ValueError("用户名只能包含字母、数字、点、下划线或短横线，长度 3-32")
    return username


def create_user(username: str, password: str) -> dict[str, Any]:
    username = normalize_username(username)
    if len(str(password or "")) < 8:
        raise ValueError("密码至少 8 位")
    first_user = (DB.one("SELECT COUNT(*) AS n FROM users") or {}).get("n", 0) == 0
    if DB.one("SELECT user_id FROM users WHERE username=?", (username,)):
        raise ValueError("用户名已存在")
    user_id = "u_" + secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]
    role = "admin" if first_user else "user"
    DB.execute(
        "INSERT INTO users(user_id,username,password_hash,role,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        (user_id, username, password_hash(password), role, now_iso(), now_iso()),
    )
    if first_user:
        DB.execute("UPDATE positions SET owner_id=? WHERE owner_id=?", (user_id, LEGACY_OWNER_ID))
        DB.execute("UPDATE watchlist SET owner_id=? WHERE owner_id=?", (user_id, LEGACY_OWNER_ID))
        DB.execute("UPDATE alerts SET owner_id=? WHERE owner_id=?", (user_id, LEGACY_OWNER_ID))
        DB.execute("UPDATE user_settings SET owner_id=? WHERE owner_id=?", (user_id, LEGACY_OWNER_ID))
    if not DB.one("SELECT symbol FROM watchlist WHERE owner_id=? LIMIT 1", (user_id,)):
        DB.executemany(
            "INSERT OR IGNORE INTO watchlist(owner_id,symbol) VALUES(?,?)",
            [(user_id, normalize_symbol(symbol)) for symbol in DEFAULT_CONFIG["bootstrap_watchlist"]],
        )
    return {"user_id": user_id, "username": username, "role": role}


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    row = DB.one("SELECT * FROM users WHERE username=?", (str(username or "").strip(),))
    if not row or not verify_password(str(password or ""), row.get("password_hash", "")):
        return None
    return {"user_id": row["user_id"], "username": row["username"], "role": row.get("role", "user")}


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(CN_TZ) + timedelta(days=SESSION_DAYS)
    DB.execute(
        "INSERT OR REPLACE INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
        (hash_token(token), user_id, now_iso(), expires.replace(microsecond=0).isoformat()),
    )
    return token


def user_from_session_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    row = DB.one(
        """SELECT u.user_id,u.username,u.role,s.expires_at FROM sessions s
           JOIN users u ON u.user_id=s.user_id WHERE s.token_hash=?""",
        (hash_token(token),),
    )
    if not row:
        return None
    expires = iso_to_datetime(row.get("expires_at"))
    if not expires or expires < datetime.now(CN_TZ):
        DB.execute("DELETE FROM sessions WHERE token_hash=?", (hash_token(token),))
        return None
    return {"user_id": row["user_id"], "username": row["username"], "role": row.get("role", "user")}


def auth_status(user: dict[str, Any] | None = None) -> dict[str, Any]:
    count = int((DB.one("SELECT COUNT(*) AS n FROM users") or {}).get("n") or 0)
    return {
        "authenticated": bool(user),
        "user": user,
        "allow_registration": True,
        "user_count": count,
    }


def iso_to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CN_TZ)
        return dt.astimezone(CN_TZ)
    except Exception:
        return None


def row_is_fresh(row: dict[str, Any], stale_seconds: float) -> bool:
    if stale_seconds <= 0:
        return True
    dt = iso_to_datetime(row.get("fetch_time"))
    if not dt:
        return False
    return (datetime.now(CN_TZ) - dt).total_seconds() <= stale_seconds


def update_source_health(name: str, status: str, latency_ms: float | None, error: str | None = None, success: bool = False):
    DB.execute(
        """INSERT INTO source_health(name,status,latency_ms,last_success,error,updated_at) VALUES(?,?,?,?,?,?)
           ON CONFLICT(name) DO UPDATE SET status=excluded.status,latency_ms=excluded.latency_ms,
           last_success=COALESCE(excluded.last_success,source_health.last_success),error=excluded.error,updated_at=excluded.updated_at""",
        (name, status, latency_ms, now_iso() if success else None, error, now_iso())
    )


def source_config(entry: Any, default_name: str, default_level: str = "B") -> dict[str, Any]:
    if isinstance(entry, str):
        return {"url": entry, "name": default_name, "source_level": default_level, "source_root": entry}
    if isinstance(entry, dict):
        out = dict(entry)
        out.setdefault("name", default_name)
        out.setdefault("source_level", default_level)
        out.setdefault("source_root", out.get("url") or out.get("name") or default_name)
        return out
    return {"url": "", "name": default_name, "source_level": default_level, "source_root": default_name}


def fetch_text_with_retries(url: str, timeout: float, retries: int) -> tuple[str | None, str | None, float]:
    start = time.perf_counter()
    errors: list[str] = []
    headers = {
        "Accept": "application/rss+xml, application/atom+xml, application/json, text/xml, */*",
        "User-Agent": "Mozilla/5.0 AStockWebTerminal/4.1",
    }
    for attempt in range(max(retries, 0) + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace"), None, (time.perf_counter() - start) * 1000
        except urllib.error.HTTPError as exc:
            errors.append(f"HTTP_{exc.code}")
            if exc.code in {401, 403, 404}:
                break
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        if attempt < retries:
            time.sleep(0.25 * (attempt + 1))
    return None, "; ".join(errors[-4:]) or "fetch failed", (time.perf_counter() - start) * 1000


def extract_symbols_from_text(text: str) -> list[str]:
    symbols = []
    for raw in re.findall(r"(?<!\d)(?:SH|SZ|BJ)?\.?(\d{6})(?!\d)", text, flags=re.I):
        symbol = normalize_symbol(raw)
        if symbol != "000000" and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def parse_dateish(value: str | None) -> str:
    if not value:
        return now_iso()
    raw = str(value).strip()
    try:
        return parsedate_to_datetime(raw).astimezone(CN_TZ).replace(microsecond=0).isoformat()
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CN_TZ)
        return dt.astimezone(CN_TZ).replace(microsecond=0).isoformat()
    except Exception:
        return raw


def import_news_items(items: list[dict[str, Any]]) -> int:
    imported = 0
    roots: dict[str, int] = {}
    for item in items:
        root = item.get("source_root") or item.get("source") or "unknown"
        roots[root] = roots.get(root, 0) + 1
    independent_roots = max(len(set(roots.keys())), 1)
    for item in items:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        event_id = item.get("event_id") or hashlib.sha256(
            (title + str(item.get("published_at")) + str(item.get("source"))).encode("utf-8")
        ).hexdigest()[:24]
        level = str(item.get("source_level", "C")).upper()
        root = item.get("source_root") or item.get("source") or "unknown"
        symbols = item.get("symbols")
        if not symbols:
            symbols = extract_symbols_from_text(" ".join([title, str(item.get("summary", "")), str(item.get("content", ""))]))
        ver = item.get("verification") or verification_for_event(level, independent_roots, bool(item.get("contradicted", False)))
        DB.execute(
            """INSERT OR REPLACE INTO news_events(event_id,title,source,source_level,published_at,original_url,symbols,themes,opportunity_score,risk_score,verification,is_original,source_root,fetched_at)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id, title, item.get("source", "未知"), level,
                parse_dateish(item.get("published_at")), item.get("original_url", ""),
                json.dumps([normalize_symbol(x) for x in symbols], ensure_ascii=False),
                json.dumps(item.get("themes", []), ensure_ascii=False),
                float(item.get("opportunity_score", 0)), float(item.get("risk_score", 0)),
                ver, 1 if item.get("is_original", True) else 0, root, now_iso()
            )
        )
        imported += 1
    return imported


def parse_rss_items(text: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
    root = ET.fromstring(text)
    items: list[dict[str, Any]] = []
    source = meta.get("name") or meta.get("url") or "RSS"
    level = str(meta.get("source_level", "B")).upper()
    source_root = meta.get("source_root") or meta.get("url") or source
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        items.append({
            "title": title,
            "source": source,
            "source_level": level,
            "published_at": item.findtext("pubDate") or item.findtext("date") or now_iso(),
            "original_url": item.findtext("link") or "",
            "summary": item.findtext("description") or "",
            "source_root": source_root,
            "themes": meta.get("themes", []),
        })
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns):
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        if not title:
            continue
        link = ""
        link_node = entry.find("atom:link", ns)
        if link_node is not None:
            link = link_node.attrib.get("href", "")
        items.append({
            "title": title,
            "source": source,
            "source_level": level,
            "published_at": entry.findtext("atom:updated", default="", namespaces=ns) or entry.findtext("atom:published", default="", namespaces=ns) or now_iso(),
            "original_url": link,
            "summary": entry.findtext("atom:summary", default="", namespaces=ns) or "",
            "source_root": source_root,
            "themes": meta.get("themes", []),
        })
    return items


def parse_json_news_items(text: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
    raw = json.loads(text)
    items = raw.get("items", raw.get("data", raw)) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    source = meta.get("name") or meta.get("url") or "JSON"
    level = str(meta.get("source_level", "B")).upper()
    root = meta.get("source_root") or meta.get("url") or source
    for item in items:
        if not isinstance(item, dict):
            continue
        mapped = {
            "title": item.get("title") or item.get("headline") or item.get("name") or "",
            "source": item.get("source") or source,
            "source_level": item.get("source_level") or level,
            "published_at": item.get("published_at") or item.get("pubDate") or item.get("time") or now_iso(),
            "original_url": item.get("original_url") or item.get("url") or item.get("link") or "",
            "symbols": item.get("symbols") or item.get("stocks") or [],
            "themes": item.get("themes") or meta.get("themes", []),
            "opportunity_score": item.get("opportunity_score", 0),
            "risk_score": item.get("risk_score", 0),
            "verification": item.get("verification"),
            "is_original": item.get("is_original", True),
            "source_root": item.get("source_root") or root,
            "summary": item.get("summary") or item.get("content") or "",
        }
        out.append(mapped)
    return out


def refresh_a_stock_data_news() -> tuple[int, list[dict[str, Any]]]:
    astock_cfg = a_stock_data_config()
    if not astock_cfg.get("enabled") or not astock_cfg.get("news_enabled"):
        return 0, []
    runtime_cfg = config()
    timeout = float(runtime_cfg.get("news_timeout_seconds", 8))
    retries = int(runtime_cfg.get("news_retry_count", 2))
    min_interval = float(astock_cfg.get("eastmoney_min_interval", 1.2))
    imported = 0
    outcomes: list[dict[str, Any]] = []

    rows, err, latency = provider_fetch_global_news(
        page_size=int(astock_cfg.get("global_news_page_size", 30)),
        timeout=timeout,
        retries=retries,
        min_interval=min_interval,
    )
    count = import_news_items(rows)
    imported += count
    status = "OK" if count else "NO_DATA" if not err else "PROVIDER_UNAVAILABLE"
    update_source_health("a-stock-data:global_news", status, latency, err if not count else err, success=bool(count))
    outcomes.append({"name": "a-stock-data:global_news", "status": status, "imported": count, "error": err})

    watch_symbols = [r["symbol"] for r in DB.all("SELECT DISTINCT symbol FROM watchlist ORDER BY symbol") if is_equity_symbol(r["symbol"])]
    watch_symbols = watch_symbols[: int(astock_cfg.get("stock_news_symbols", 5))]
    if watch_symbols:
        rows, err, latency = provider_fetch_stock_news(
            watch_symbols,
            page_size=int(astock_cfg.get("stock_news_page_size", 5)),
            timeout=timeout,
            retries=retries,
            min_interval=min_interval,
        )
        count = import_news_items(rows)
        imported += count
        status = "OK" if count else "NO_DATA" if not err else "PROVIDER_UNAVAILABLE"
        update_source_health("a-stock-data:stock_news", status, latency, err if not count else err, success=bool(count))
        outcomes.append({"name": "a-stock-data:stock_news", "status": status, "imported": count, "symbols": watch_symbols, "error": err})

    return imported, outcomes


def refresh_news_sources() -> dict[str, Any]:
    cfg = config()
    timeout = float(cfg.get("news_timeout_seconds", 8))
    retries = int(cfg.get("news_retry_count", 2))
    imported = 0
    outcomes: list[dict[str, Any]] = []
    builtin_imported, builtin_outcomes = refresh_a_stock_data_news()
    imported += builtin_imported
    outcomes.extend(builtin_outcomes)
    sources = []
    for idx, entry in enumerate(cfg.get("rss_sources") or []):
        sources.append(("rss", source_config(entry, f"rss_{idx + 1}")))
    for idx, entry in enumerate(cfg.get("json_news_sources") or []):
        sources.append(("json", source_config(entry, f"json_news_{idx + 1}")))
    if not sources and not outcomes:
        update_source_health("news", "NO_SOURCES", 0, "未配置rss_sources或json_news_sources", success=False)
        return {"ok": False, "status": "NO_SOURCES", "imported": 0, "sources": []}
    for kind, meta in sources:
        name = f"news:{meta.get('name') or meta.get('url') or kind}"
        url = str(meta.get("url") or "").strip()
        if not url:
            update_source_health(name, "CONFIG_ERROR", 0, "缺少url", success=False)
            outcomes.append({"name": name, "status": "CONFIG_ERROR", "imported": 0})
            continue
        text, err, latency = fetch_text_with_retries(url, timeout, retries)
        if err or text is None:
            update_source_health(name, "PROVIDER_UNAVAILABLE", latency, err, success=False)
            outcomes.append({"name": name, "status": "PROVIDER_UNAVAILABLE", "error": err, "imported": 0})
            continue
        try:
            items = parse_rss_items(text, meta) if kind == "rss" else parse_json_news_items(text, meta)
            count = import_news_items(items)
            status = "OK" if count else "NO_DATA"
            update_source_health(name, status, latency, None if count else "源可访问但未解析出新闻", success=bool(count))
            imported += count
            outcomes.append({"name": name, "status": status, "imported": count})
        except Exception as exc:
            update_source_health(name, "SCHEMA_CHANGED", latency, f"{type(exc).__name__}: {exc}", success=False)
            outcomes.append({"name": name, "status": "SCHEMA_CHANGED", "error": f"{type(exc).__name__}: {exc}", "imported": 0})
    rebuild_alerts()
    aggregate_error = None if imported else "新闻源可请求但未导入有效新闻"
    update_source_health("news", "OK" if imported else "NO_DATA", 0, aggregate_error, success=imported > 0)
    return {"ok": imported > 0, "imported": imported, "sources": outcomes}


FULLWIDTH_TRANS = str.maketrans("０１２３４５６７８９．，：；＋－", "0123456789.,:;+-")


def normalize_ocr_text(text: str) -> str:
    cleaned = str(text or "").translate(FULLWIDTH_TRANS)
    cleaned = cleaned.replace("｜", "|").replace("﹣", "-").replace("—", "-")
    cleaned = cleaned.replace("·", ".")
    cleaned = cleaned.replace("\u3000", " ")
    cleaned = re.sub(r"(?<=\d)\s*[,，]\s*(?=\d{3}(?:\D|$))", "", cleaned)
    cleaned = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", cleaned)
    cleaned = re.sub(r"(?<=\d),(?=\d{3}\b)", "", cleaned)
    return cleaned


def compact_ocr_text(text: str) -> str:
    compact = re.sub(r"\s+", "", normalize_ocr_text(text))
    return compact.replace(",", "").replace("，", "")


def image_bytes_from_data_uri(image_data: str) -> tuple[bytes | None, str | None]:
    if not image_data:
        return None, "缺少图片数据"
    raw = image_data.split(",", 1)[1] if "," in image_data[:80] else image_data
    try:
        return base64.b64decode(raw, validate=True), None
    except (binascii.Error, ValueError) as exc:
        return None, f"图片Base64无效: {exc}"


def normalize_ocr_code_token(value: str) -> str:
    token = str(value or "").translate(FULLWIDTH_TRANS)
    trans = str.maketrans({
        "S": "5", "s": "5", "$": "5",
        "O": "0", "o": "0", "D": "0", "Q": "0",
        "I": "1", "l": "1", "|": "1",
        "B": "8", "Z": "2", "G": "6",
    })
    code = re.sub(r"\D", "", token.translate(trans))
    if len(code) >= 6:
        code = code[:6]
    if len(code) == 6 and code.startswith("5"):
        code = "6" + code[1:]
    return code if re.fullmatch(r"\d{6}", code or "") else ""


def clean_ocr_name(value: str) -> str:
    text = str(value or "").translate(FULLWIDTH_TRANS)
    text = re.sub(r"[\s|,，.。·:：;；]+", "", text)
    text = re.sub(r"^[^A-Za-z\u4e00-\u9fff]+|[^A-Za-z\u4e00-\u9fff]+$", "", text)
    return text[:20]


def ocr_number_groups(value: str) -> list[str]:
    text = str(value or "").translate(FULLWIDTH_TRANS)
    text = text.translate(str.maketrans({"S": "5", "s": "5", "O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1", "|": "1", "B": "8"}))
    return re.findall(r"\d+", text)


def parse_ocr_decimal(value: str, *, default_decimals: int = 3) -> float | None:
    text = str(value or "").translate(FULLWIDTH_TRANS)
    text = text.translate(str.maketrans({"S": "5", "s": "5", "O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1", "|": "1", "B": "8"}))
    sep_matches = list(re.finditer(r"[\.,，．·]", text))
    if sep_matches:
        sep = sep_matches[-1]
        left_groups = re.findall(r"\d+", text[:sep.start()])
        right_groups = re.findall(r"\d+", text[sep.end():])
        if left_groups and right_groups:
            if len(left_groups[-1]) == 1 and len(left_groups) >= 2 and len(left_groups[-2]) == 1:
                integer = left_groups[-2] + left_groups[-1]
            else:
                integer = left_groups[-1]
            decimal = "".join(right_groups)[:default_decimals]
            return float(f"{integer}.{decimal}")
    groups = ocr_number_groups(text)
    if not groups:
        return None
    digits = "".join(groups[-2:]) if len(groups) >= 2 and len(groups[-1]) <= default_decimals else groups[-1]
    if len(digits) > default_decimals:
        return float(digits[:-default_decimals] + "." + digits[-default_decimals:])
    return float(digits)


def parse_ocr_quantity(groups: list[str]) -> int:
    if len(groups) >= 3 and all(len(group) == 1 for group in groups[:3]):
        if len(groups) >= 6 and groups[:3] == groups[3:6]:
            return int("".join(groups[:3]))
        if int("".join(groups[:3])) >= 10:
            return int("".join(groups[:3]))
    return int(float(groups[0]))


def known_stock_name(symbol: str, fallback: str = "") -> str:
    for table in ["quote_validation", "stock_scores", "fund_consensus", "stock_valuations", "fund_report_holdings"]:
        try:
            row = DB.one(f"SELECT name FROM {table} WHERE symbol=? AND name<>'' LIMIT 1", (symbol,))
        except Exception:
            row = None
        if row and row.get("name"):
            return str(row["name"])
    corrections = {
        "002532": "天山铝业",
        "002594": "比亚迪",
        "002738": "中矿资源",
        "300750": "宁德时代",
        "600036": "招商银行",
        "600988": "赤峰黄金",
        "601108": "财通证券",
        "688475": "萤石网络",
    }
    if symbol in corrections:
        return corrections[symbol]
    return fallback.replace("钅吕", "铝")


ASSET_TYPE_LABELS = {
    "stock": "股票",
    "fund": "基金",
    "gold": "黄金",
    "wealth": "理财",
    "other": "其他",
}


def is_six_digit_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def normalize_asset_type(asset_type: Any = None, symbol: Any = "", name: Any = "", context: Any = "") -> str:
    raw = str(asset_type or "").strip().lower()
    aliases = {
        "stock": "stock", "股票": "stock", "证券": "stock", "a股": "stock", "a-share": "stock",
        "fund": "fund", "基金": "fund", "etf": "fund", "lof": "fund", "指数基金": "fund",
        "gold": "gold", "黄金": "gold", "贵金属": "gold",
        "wealth": "wealth", "理财": "wealth", "现金管理": "wealth", "存款": "wealth",
        "other": "other", "其他": "other",
    }
    if raw in aliases:
        return aliases[raw]
    text = f"{symbol} {name} {context}"
    name_text = str(name or "")
    if re.search(r"黄金账户|实物金|持仓克重|元/克|金价", text):
        return "gold"
    if re.search(r"ETF|LOF|基金|联接|债券|货币|指数|中证|沪深|创业板|科创|科技50|黄金产业|医疗|医药|产业|50|300|500|1000", name_text, re.I):
        return "fund"
    code = normalize_symbol(str(symbol or ""))
    if is_six_digit_symbol(code):
        return "stock" if is_equity_symbol(code) else "fund"
    if re.search(r"理财|现金管理|定期|存款|收益凭证", text):
        return "wealth"
    if re.search(r"ETF|LOF|基金|联接|债券|货币|指数|中证|沪深|创业板|科创|科技50|黄金产业", text, re.I):
        return "fund"
    return "other"


def synthetic_position_symbol(asset_type: str, name: str) -> str:
    digest = hashlib.sha256(f"{asset_type}|{name}".encode("utf-8")).hexdigest()[:8].upper()
    return f"{asset_type.upper()}-{digest}"


def normalize_position_symbol(value: Any, asset_type: str, name: str = "") -> str:
    raw = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z]+-[A-Za-z0-9]{4,24}", raw):
        return raw.upper()
    if is_six_digit_symbol(raw) or re.search(r"\d{6}", raw):
        return normalize_symbol(raw)
    if raw and asset_type not in {"stock", "fund"}:
        cleaned = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "", raw)[:32]
        if cleaned:
            return cleaned
    return synthetic_position_symbol(asset_type, name or raw or asset_type)


def first_non_market_theme(symbol: str) -> str:
    for table in ["stock_scores", "fund_report_holdings", "fund_consensus"]:
        try:
            row = DB.one(f"SELECT industry FROM {table} WHERE symbol=? AND COALESCE(industry,'')<>'' LIMIT 1", (symbol,))
        except Exception:
            row = None
        if row and row.get("industry") and row["industry"] not in {"市场热度", "观察池"}:
            return str(row["industry"])
    return ""


def infer_stock_theme(symbol: str, name: str = "") -> str:
    db_theme = first_non_market_theme(symbol)
    if db_theme:
        return db_theme
    text = f"{symbol} {name}"
    rules = [
        ("新能源-动力电池", ["宁德时代", "电池", "锂电", "动力电池"]),
        ("新能源车", ["比亚迪", "汽车", "整车", "新能源车"]),
        ("资源-黄金有色", ["赤峰黄金", "黄金", "贵金属"]),
        ("资源-有色金属", ["天山铝业", "中矿资源", "铝", "铜", "锂", "钴", "矿", "资源"]),
        ("金融-银行", ["招商银行", "银行"]),
        ("金融-证券", ["财通证券", "证券", "券商"]),
        ("科技-物联网/安防", ["萤石网络", "安防", "物联", "智能家居"]),
        ("半导体", ["半导体", "芯片", "集成电路", "封测"]),
        ("医药医疗", ["医药", "医疗", "生物", "创新药"]),
        ("消费", ["食品", "消费", "白酒", "家电"]),
    ]
    for theme, keys in rules:
        if any(key in text for key in keys):
            return theme
    return "未分类"


def infer_asset_theme(symbol: str, name: str = "", asset_type: str = "stock", context: str = "") -> str:
    text = f"{name} {context}"
    if asset_type == "stock":
        return infer_stock_theme(symbol, name)
    if asset_type == "gold":
        return "黄金/贵金属"
    if asset_type == "wealth":
        if re.search(r"现金|货币|活期", text):
            return "现金管理/货币"
        if re.search(r"定期|固收|债", text):
            return "固收理财"
        return "银行理财/其他理财"
    if asset_type == "fund":
        rules = [
            ("黄金/贵金属", ["黄金", "贵金属"]),
            ("科技指数", ["科技", "科创", "芯片", "半导体", "信息技术"]),
            ("医药医疗", ["医疗", "医药", "生物"]),
            ("新能源", ["新能源", "电池", "锂"]),
            ("宽基指数", ["沪深", "中证", "上证", "创业板", "科创50", "科技50", "50", "300", "500", "1000"]),
            ("债券/固收", ["债", "固收"]),
            ("货币基金", ["货币", "现金"]),
        ]
        for theme, keys in rules:
            if any(key in text for key in keys):
                return theme
        return "基金/待分类"
    return "其他资产"


def normalize_price_value(value: float | None) -> float | None:
    if value is None or value <= 0:
        return None
    if abs(value - round(value)) < 1e-6 and value >= 10000:
        return round(value / 100, 4)
    return round(value, 4)


def signed_numbers(value: str) -> list[float]:
    out: list[float] = []
    for token in re.findall(r"[-+]?\d+(?:\.\d+)?", normalize_ocr_text(value)):
        try:
            out.append(float(token))
        except ValueError:
            pass
    return out


def compact_grid_lines(lines: list[int], min_gap: int = 3) -> list[int]:
    if not lines:
        return []
    groups: list[list[int]] = [[lines[0]]]
    for value in lines[1:]:
        if value - groups[-1][-1] <= min_gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [int(round(sum(group) / len(group))) for group in groups]


def detect_broker_table_grid(image: Any) -> tuple[list[int], list[int]]:
    rgb = image.convert("RGB")
    width, height = rgb.size

    def is_grid_pixel(x: int, y: int) -> bool:
        r, g, b = rgb.getpixel((x, y))
        avg = (r + g + b) // 3
        return abs(r - g) < 5 and abs(g - b) < 5 and 190 <= avg <= 230

    y_candidates = []
    for y in range(max(0, height // 4), height):
        count = sum(1 for x in range(0, width, 2) if is_grid_pixel(x, y))
        if count > width // 5:
            y_candidates.append(y)
    x_candidates = []
    y_start = min(y_candidates) if y_candidates else max(0, height // 3)
    for x in range(width):
        count = sum(1 for y in range(y_start, height, 2) if is_grid_pixel(x, y))
        if count > max(12, (height - y_start) // 8):
            x_candidates.append(x)
    return compact_grid_lines(x_candidates), compact_grid_lines(y_candidates)


def ocr_crop_text(image: Any, box: tuple[int, int, int, int], scale: int = 5) -> str:
    crop = image.crop(box)
    if crop.width <= 0 or crop.height <= 0:
        return ""
    crop = crop.resize((crop.width * scale, crop.height * scale))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    text, _ = ocr_text_with_windows_ocr(buf.getvalue())
    return text or ""


def parse_broker_row_text(text: str) -> dict[str, Any] | None:
    raw = normalize_ocr_text(text)
    code_match = re.search(r"[0-9SODQBGIl|Z$]{6}", raw, flags=re.I)
    if not code_match:
        return None
    symbol = normalize_ocr_code_token(code_match.group(0))
    if symbol == "000000":
        return None
    rest = raw[code_match.end():]
    first_number = re.search(r"\d+", rest.translate(str.maketrans({"S": "5", "s": "5"})))
    name = clean_ocr_name(rest[:first_number.start()] if first_number else "")
    groups = ocr_number_groups(rest)
    if len(groups) < 2:
        return None
    quantity = parse_ocr_quantity(groups)
    average_cost = parse_ocr_decimal(rest)
    if not average_cost or average_cost <= 0 or quantity <= 0:
        return None
    name = known_stock_name(symbol, name)
    asset_type = normalize_asset_type(None, symbol, name, raw)
    return {
        "symbol": symbol,
        "name": name,
        "asset_type": asset_type,
        "theme": infer_asset_theme(symbol, name, asset_type, raw),
        "quantity": quantity,
        "average_cost": round(average_cost, 4),
        "current_price": None,
        "stop_price": None,
        "take_profit_price": None,
        "score": 50,
        "note": "由持仓截图表格识别导入；请人工复核数量和成本价",
    }


def recognize_broker_table_positions(image_bytes: bytes) -> tuple[list[dict[str, Any]], str, list[str]]:
    try:
        from PIL import Image
    except Exception as exc:
        return [], "", [f"表格识别需要 Pillow: {exc}"]
    try:
        image = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        return [], "", [f"图片读取失败: {type(exc).__name__}: {exc}"]
    x_lines, y_lines = detect_broker_table_grid(image)
    if len(x_lines) < 7 or len(y_lines) < 3:
        return [], "", ["未检测到标准持仓表格网格，已回退通用OCR"]
    positions: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw_rows: list[str] = []
    seen: set[str] = set()
    for top, bottom in zip(y_lines[1:], y_lines[2:]):
        if bottom - top < 18:
            continue
        box = (x_lines[0] + 1, top + 1, x_lines[6] - 1, bottom - 1)
        row_text = ocr_crop_text(image, box)
        if not row_text.strip():
            continue
        raw_rows.append(row_text.strip())
        item = parse_broker_row_text(row_text)
        if not item:
            warnings.append(f"表格行未解析: {row_text.strip()[:80]}")
            continue
        if len(x_lines) >= 8:
            cost_text = ocr_crop_text(image, (x_lines[5] + 1, top + 1, x_lines[6] - 1, bottom - 1))
            price_text = ocr_crop_text(image, (x_lines[6] + 1, top + 1, x_lines[7] - 1, bottom - 1))
            cost = parse_ocr_decimal(cost_text)
            current = parse_ocr_decimal(price_text)
            if cost and cost > 0:
                item["average_cost"] = round(cost, 4)
            if current and current > 0:
                item["current_price"] = round(current, 4)
            if price_text.strip():
                raw_rows[-1] = f"{raw_rows[-1]} | 市价 {price_text.strip()}"
        if item["symbol"] in seen:
            continue
        positions.append(item)
        seen.add(item["symbol"])
    if not positions:
        warnings.append("检测到表格网格，但未解析出有效持仓行")
    return positions, "\n".join(raw_rows), warnings


def infer_position_numbers(line: str, numbers: list[float]) -> tuple[int | None, int | None]:
    if len(numbers) < 2:
        return None, None
    lower = line.lower()
    integer_indexes = [idx for idx, value in enumerate(numbers) if value > 0 and abs(value - round(value)) < 1e-6]
    decimal_indexes = [idx for idx, value in enumerate(numbers) if 0 < value < 10000 and abs(value - round(value)) > 1e-6]
    quantity_idx = integer_indexes[0] if integer_indexes else None
    if re.search(r"市值|盈亏|参考|最新|现价|价格", line) and len(integer_indexes) >= 2:
        quantity_idx = integer_indexes[0]
    if quantity_idx is None:
        return None, None
    avg_idx = None
    for idx in range(quantity_idx + 1, len(numbers)):
        value = numbers[idx]
        if abs(value - round(value)) < 1e-6 and 10000 <= value <= 999999:
            avg_idx = idx
            break
    if re.search(r"成本价|成本|持仓成本|买入均价|成本均价", line):
        tail_candidates = [idx for idx in decimal_indexes if idx > quantity_idx]
        if tail_candidates:
            avg_idx = tail_candidates[0]
    if avg_idx is None:
        for idx in range(quantity_idx + 1, len(numbers)):
            value = numbers[idx]
            if 0 < value < 10000:
                if len(numbers) >= quantity_idx + 3 and idx == quantity_idx + 1 and abs(value - round(value)) < 1e-6:
                    continue
                avg_idx = idx
                break
    if avg_idx is None and decimal_indexes:
        avg_idx = decimal_indexes[0]
    return quantity_idx, avg_idx


def infer_current_price_index(numbers: list[float], quantity_idx: int | None, avg_idx: int | None) -> int | None:
    if quantity_idx is None or avg_idx is None:
        return None
    for idx in range(avg_idx + 1, len(numbers)):
        value = numbers[idx]
        if 0 < value < 100000:
            return idx
    return None


def parse_position_text(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    positions: list[dict[str, Any]] = []
    seen: set[str] = set()
    header_context = ""
    for raw_line in normalize_ocr_text(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.search(r"(?<!\d)(?:SH|SZ|BJ)?\.?(\d{6})(?!\d)", line, flags=re.I)
        if not match:
            if re.search(r"代码|证券|持仓|数量|成本|市值|现价|最新价|盈亏", line):
                header_context = line
            continue
        symbol = normalize_symbol(match.group(1))
        if symbol == "000000" or symbol in seen:
            continue
        tail = line[match.end():].strip(" :：|,，\t")
        parts = [p for p in re.split(r"[\s,，|;；]+", tail) if p]
        name_parts = []
        for part in parts:
            if re.search(r"\d", part):
                break
            name_parts.append(part)
        name = "".join(name_parts)[:20]
        number_tokens = re.findall(r"[-+]?\d+(?:\.\d+)?", tail)
        numbers = []
        for token in number_tokens:
            try:
                numbers.append(float(token))
            except ValueError:
                continue
        if len(numbers) < 2:
            warnings.append(f"{symbol} 缺少数量或成本价，已跳过")
            continue
        quantity_idx, avg_idx = infer_position_numbers(f"{header_context} {line}", numbers)
        if quantity_idx is None:
            warnings.append(f"{symbol} 未识别到持仓数量，已跳过")
            continue
        if avg_idx is None:
            warnings.append(f"{symbol} 未识别到成本价，已跳过")
            continue
        current_idx = infer_current_price_index(numbers, quantity_idx, avg_idx)
        name = known_stock_name(symbol, name)
        asset_type = normalize_asset_type(None, symbol, name, line)
        current_price = normalize_price_value(numbers[current_idx]) if current_idx is not None else None
        positions.append({
            "symbol": symbol,
            "name": name,
            "asset_type": asset_type,
            "theme": infer_asset_theme(symbol, name, asset_type, line),
            "quantity": int(round(numbers[quantity_idx])),
            "average_cost": round(numbers[avg_idx] / 100 if abs(numbers[avg_idx] - round(numbers[avg_idx])) < 1e-6 and numbers[avg_idx] >= 10000 else numbers[avg_idx], 4),
            "current_price": current_price,
            "stop_price": None,
            "take_profit_price": None,
            "score": 50,
            "note": "由持仓截图识别导入；请人工复核数量和成本价",
        })
        seen.add(symbol)
    if not positions:
        warnings.append("未识别出有效持仓行；请粘贴OCR文本或安装可用OCR引擎后重试")
    return positions, warnings


def parse_gold_account_position(text: str) -> list[dict[str, Any]]:
    raw = compact_ocr_text(text)
    if not re.search(r"黄金账户|持仓克重|元/克|实时买入价|实时卖出价", raw):
        return []
    name_match = re.search(r"[\u4e00-\u9fff]{0,8}黄金账户", raw)
    name = name_match.group(0) if name_match else "黄金账户"

    def match_number(pattern: str) -> float | None:
        match = re.search(pattern, raw)
        if not match:
            return None
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return None

    quantity = match_number(r"持仓克重\s*=?\s*([0-9]+(?:\.[0-9]{1,4})?)")
    average_cost = match_number(r"成本均价\s*=?\s*([0-9]+(?:\.[0-9]{1,2})?)")
    sell_price = match_number(r"实时卖出价\s*([0-9]+(?:\.[0-9]{1,2})?)")
    buy_price = match_number(r"实时买入价\s*([0-9]+(?:\.[0-9]{1,2})?)")
    holding_amount = match_number(r"持仓金额\D*([0-9]+(?:\.[0-9]{1,2})?)")
    current_price = sell_price or buy_price
    if not quantity and holding_amount and current_price:
        quantity = holding_amount / current_price
    if not average_cost:
        average_cost = current_price
    if not quantity or not average_cost:
        return []
    symbol = "GOLD-CMB" if "招行" in name else synthetic_position_symbol("gold", name)
    return [{
        "symbol": symbol,
        "name": name,
        "asset_type": "gold",
        "theme": "黄金/贵金属",
        "quantity": round(quantity, 4),
        "average_cost": round(average_cost, 4),
        "current_price": round(current_price, 4) if current_price else None,
        "stop_price": None,
        "take_profit_price": None,
        "score": 50,
        "note": "由黄金账户截图识别导入；现价优先使用实时卖出价，请人工复核",
    }]


PRODUCT_NAME_SKIP = {
    "买入", "卖出", "撤单", "持仓", "查询", "证券", "理财", "资金", "普通交易",
    "查看已清仓股票", "市值", "盈亏比例", "持仓可用", "成本现价",
}


def clean_product_name(value: str) -> str:
    text = str(value or "").translate(FULLWIDTH_TRANS)
    text = re.sub(r"[\s|,，.。·:：;；/／]+", "", text)
    text = re.sub(r"^[^A-Za-z0-9\u4e00-\u9fff]+|[^A-Za-z0-9\u4e00-\u9fff]+$", "", text)
    return text[:24]


def looks_like_product_name(line: str) -> bool:
    name = clean_product_name(line)
    if len(name) < 2 or name in PRODUCT_NAME_SKIP:
        return False
    if re.search(r"总资产|可用|可取|仓位|交易|账户|收益|提醒|委托|按钮|实时报价", line):
        return False
    return bool(re.search(r"黄金|科技|中证|沪深|上证|创业|科创|医疗|医药|基金|ETF|LOF|债|货币|现金|理财|产业|50|300|500|1000", name, re.I))


def product_position_from_row(name: str, row_text: str, context: str = "") -> dict[str, Any] | None:
    name = clean_product_name(name)
    nums = signed_numbers(row_text)
    if len(nums) < 4:
        return None
    positive_tail = [n for n in nums[-4:] if n > 0]
    if len(positive_tail) < 2:
        return None
    average_cost = positive_tail[-2]
    current_price = positive_tail[-1]
    quantity = None
    for value in reversed(nums[:-2]):
        if value > 0 and abs(value - round(value)) < 1e-6:
            quantity = int(round(value))
            break
    if quantity is None:
        for value in reversed(nums[:-2]):
            if value > 0:
                quantity = value
                break
    if not quantity or average_cost <= 0:
        return None
    asset_type = normalize_asset_type(None, "", name, context or row_text)
    if asset_type == "stock":
        asset_type = "fund"
    symbol = synthetic_position_symbol(asset_type, name)
    return {
        "symbol": symbol,
        "name": name,
        "asset_type": asset_type,
        "theme": infer_asset_theme(symbol, name, asset_type, row_text),
        "quantity": quantity,
        "average_cost": round(average_cost, 4),
        "current_price": round(current_price, 4) if current_price > 0 else None,
        "stop_price": None,
        "take_profit_price": None,
        "score": 50,
        "note": "由基金/理财持仓截图识别导入；请人工复核产品名称、份额和净值",
    }


def spaced_label_pattern(label: str) -> str:
    parts = []
    for char in label:
        if char.isspace():
            continue
        parts.append(re.escape(char))
    return r"\s*".join(parts)


def segment_after_label(raw: str, label: str, stop_labels: list[str]) -> str:
    match = re.search(spaced_label_pattern(label), raw)
    if not match:
        return ""
    start = match.end()
    end = len(raw)
    for stop in stop_labels:
        stop_match = re.search(spaced_label_pattern(stop), raw[start:])
        if stop_match:
            end = min(end, start + stop_match.start())
    return raw[start:end]


def parse_mobile_column_products(text: str) -> list[dict[str, Any]]:
    raw = normalize_ocr_text(text)
    compact = compact_ocr_text(text)
    if not all(key in compact for key in ["市值", "持仓/可用", "成本/现价"]):
        return []
    value_segment = compact.split("市值", 1)[1]
    for stop in ["可取", "理财", "持仓总盈亏", "盈亏/比例"]:
        if stop in value_segment:
            value_segment = value_segment.split(stop, 1)[0]
    pairs: list[tuple[str, float]] = []
    for match in re.finditer(r"([\u4e00-\u9fffA-Za-z]{2,10}(?:50|300|500|1000)?)(\d+(?:\.\d{2}))", value_segment):
        name = clean_product_name(match.group(1).replace("市值", ""))
        if not looks_like_product_name(name):
            continue
        try:
            market_value = float(match.group(2))
        except ValueError:
            continue
        pairs.append((name, market_value))
    if not pairs:
        return []

    qty_segment = segment_after_label(raw, "持仓/可用", ["当日盈亏", "成本/现价"])
    price_segment = segment_after_label(raw, "成本/现价", ["查看", "看已清", "股票"])
    qty_numbers = [n for n in signed_numbers(qty_segment) if n > 0]
    price_numbers = [n for n in signed_numbers(price_segment) if n > 0]
    if len(qty_numbers) < len(pairs) or len(price_numbers) < len(pairs) * 2:
        return []

    out: list[dict[str, Any]] = []
    for idx, (name, _market_value) in enumerate(pairs):
        quantity = qty_numbers[idx * 2] if len(qty_numbers) >= (idx + 1) * 2 else qty_numbers[idx]
        average_cost = price_numbers[idx * 2]
        current_price = price_numbers[idx * 2 + 1]
        item = product_position_from_row(name, f"{name} {quantity} {quantity} {average_cost} {current_price}", compact)
        if item:
            item["quantity"] = int(quantity) if abs(quantity - round(quantity)) < 1e-6 else round(quantity, 4)
            item["average_cost"] = round(average_cost, 4)
            item["current_price"] = round(current_price, 4)
            out.append(item)
    return out


def parse_product_positions_text(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    raw = normalize_ocr_text(text)
    positions = parse_gold_account_position(raw)
    warnings: list[str] = []
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    seen_names = {p["name"] for p in positions}

    for item in parse_mobile_column_products(raw):
        if item["name"] not in seen_names:
            positions.append(item)
            seen_names.add(item["name"])

    for line in lines:
        match = re.match(r"^([\u4e00-\u9fffA-Za-z0-9+/._-]{2,24})\s+(.+)$", line)
        if not match:
            continue
        name = clean_product_name(match.group(1))
        if name in seen_names or not looks_like_product_name(name):
            continue
        item = product_position_from_row(name, line)
        if item:
            positions.append(item)
            seen_names.add(name)

    for idx, line in enumerate(lines):
        if len(signed_numbers(line)) >= 4:
            continue
        if not looks_like_product_name(line):
            continue
        name = clean_product_name(line)
        if name in seen_names:
            continue
        window = " ".join(lines[idx: idx + 9])
        item = product_position_from_row(name, window, raw)
        if item:
            positions.append(item)
            seen_names.add(name)

    return positions, warnings


def ocr_text_from_image_data(image_data: str) -> tuple[str | None, str | None, str | None]:
    image_bytes, decode_err = image_bytes_from_data_uri(image_data)
    if decode_err or image_bytes is None:
        return None, None, decode_err
    cfg = config().get("ocr", {})
    errors: list[str] = []
    try:
        from PIL import Image
        import pytesseract
        cmd = str(cfg.get("tesseract_cmd") or "").strip()
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        image = Image.open(io.BytesIO(image_bytes))
        if max(image.size) < 1800:
            ratio = 1800 / max(image.size)
            image = image.resize((int(image.width * ratio), int(image.height * ratio)))
        image = image.convert("L")
        text = pytesseract.image_to_string(image, lang=str(cfg.get("languages") or "chi_sim+eng"))
        if text.strip():
            return text, "pytesseract", None
        errors.append("pytesseract 未识别出文字")
    except Exception as exc:
        errors.append(f"pytesseract不可用: {type(exc).__name__}: {exc}")

    text, err = ocr_text_with_tesseract_cli(image_bytes, cfg)
    if text:
        return text, "tesseract-cli", None
    if err:
        errors.append(err)

    text, err = ocr_text_with_windows_ocr(image_bytes)
    if text:
        return text, "windows-ocr", None
    if err:
        errors.append(err)
    return None, "ocr-fallback", "；".join(errors) + "；可先粘贴券商软件自带OCR文本，或安装 Tesseract 后重试"


def ocr_text_with_tesseract_cli(image_bytes: bytes, cfg: dict[str, Any]) -> tuple[str | None, str | None]:
    cmd = str(cfg.get("tesseract_cmd") or "").strip() or shutil.which("tesseract")
    if not cmd:
        return None, "未找到 tesseract 命令行程序"
    suffix = ".png"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [cmd, tmp_path, "stdout", "-l", str(cfg.get("languages") or "chi_sim+eng"), "--psm", "6"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout, None
        return None, f"tesseract-cli失败: {proc.stderr.strip() or proc.stdout.strip() or proc.returncode}"
    except Exception as exc:
        return None, f"tesseract-cli异常: {type(exc).__name__}: {exc}"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def ocr_text_with_windows_ocr(image_bytes: bytes) -> tuple[str | None, str | None]:
    if os.name != "nt":
        return None, "Windows OCR 仅在 Windows 可用"
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        tmp.write(image_bytes)
        image_path = tmp.name
    script = r"""
$ErrorActionPreference = 'Stop'
$path = $env:ASTOCK_OCR_IMAGE
if (-not $path) { throw 'empty image path' }
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
[void][Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
[void][Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
function Await-WinRt($operation, [Type]$resultType) {
  $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 } |
    Select-Object -First 1
  $task = $method.MakeGenericMethod($resultType).Invoke($null, @($operation))
  return $task.GetAwaiter().GetResult()
}
$fileOp = [Windows.Storage.StorageFile]::GetFileFromPathAsync($path)
$file = Await-WinRt $fileOp ([Windows.Storage.StorageFile])
$streamOp = $file.OpenReadAsync()
$stream = Await-WinRt $streamOp ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
$decoderOp = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
$decoder = Await-WinRt $decoderOp ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmapOp = $decoder.GetSoftwareBitmapAsync()
$bitmap = Await-WinRt $bitmapOp ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'Windows OCR engine unavailable' }
$resultOp = $engine.RecognizeAsync($bitmap)
$result = Await-WinRt $resultOp ([Windows.Media.Ocr.OcrResult])
$result.Text
"""
    try:
        env = dict(os.environ)
        env["ASTOCK_OCR_IMAGE"] = image_path
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=25,
            env=env,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout, None
        return None, f"Windows OCR失败: {proc.stderr.strip() or proc.stdout.strip() or proc.returncode}"
    except Exception as exc:
        return None, f"Windows OCR异常: {type(exc).__name__}: {exc}"
    finally:
        try:
            os.unlink(image_path)
        except OSError:
            pass


def normalize_position_payload(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        asset_type = normalize_asset_type(item.get("asset_type"), item.get("symbol", ""), name, item.get("theme", ""))
        symbol = normalize_position_symbol(item.get("symbol", ""), asset_type, name)
        if asset_type == "stock" and symbol == "000000":
            continue
        quantity = float(item.get("quantity") or 0)
        average_cost = float(item.get("average_cost") or 0)
        if quantity <= 0 or average_cost <= 0:
            continue
        current_price = item.get("current_price")
        try:
            current_price = float(current_price) if current_price not in (None, "") else None
        except (TypeError, ValueError):
            current_price = None
        theme = str(item.get("theme") or "").strip()
        if not theme or theme == "截图导入":
            theme = infer_asset_theme(symbol, name, asset_type, str(item.get("note") or ""))
        normalized.append({
            "symbol": symbol,
            "name": name,
            "asset_type": asset_type,
            "asset_type_label": ASSET_TYPE_LABELS.get(asset_type, asset_type),
            "theme": theme,
            "quantity": quantity,
            "average_cost": average_cost,
            "current_price": current_price,
            "stop_price": item.get("stop_price"),
            "take_profit_price": item.get("take_profit_price"),
            "score": float(item.get("score", 50)),
            "note": str(item.get("note") or "由持仓截图识别导入；请人工复核数量和成本价"),
        })
    return normalized


def upsert_position(item: dict[str, Any], user_id: str = LEGACY_OWNER_ID) -> str:
    asset_type = normalize_asset_type(item.get("asset_type"), item.get("symbol", ""), item.get("name", ""), item.get("theme", ""))
    symbol = normalize_position_symbol(item["symbol"], asset_type, str(item.get("name") or ""))
    DB.execute("""INSERT INTO positions(owner_id,symbol,name,theme,asset_type,current_price,quantity,average_cost,stop_price,take_profit_price,score,note,updated_at)
     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner_id,symbol) DO UPDATE SET name=excluded.name,theme=excluded.theme,asset_type=excluded.asset_type,
     current_price=COALESCE(excluded.current_price,positions.current_price),quantity=excluded.quantity,
     average_cost=excluded.average_cost,stop_price=excluded.stop_price,take_profit_price=excluded.take_profit_price,
     score=excluded.score,note=excluded.note,updated_at=excluded.updated_at""",
     (user_id, symbol, item.get("name", ""), item.get("theme", "未分类"), asset_type, item.get("current_price"),
      float(item["quantity"]), float(item["average_cost"]), item.get("stop_price"), item.get("take_profit_price"),
      float(item.get("score", 50)), item.get("note", ""), now_iso()))
    return symbol


def backfill_position_metadata(user_id: str = LEGACY_OWNER_ID) -> dict[str, Any]:
    rows = DB.all("SELECT symbol,name,theme,asset_type FROM positions WHERE owner_id=? ORDER BY symbol", (user_id,))
    updated = 0
    for row in rows:
        old_theme = str(row.get("theme") or "").strip()
        old_type = str(row.get("asset_type") or "").strip()
        name = str(row.get("name") or "").strip()
        asset_type = normalize_asset_type(old_type, row.get("symbol", ""), name, old_theme)
        if asset_type == "stock" and is_six_digit_symbol(row.get("symbol")):
            name = known_stock_name(row["symbol"], name)
        theme = old_theme
        if not theme or theme in {"截图导入", "未分类", "其他资产", "基金/待分类"}:
            theme = infer_asset_theme(row["symbol"], name, asset_type, old_theme)
        if theme != old_theme or asset_type != old_type or name != str(row.get("name") or ""):
            DB.execute(
                "UPDATE positions SET name=?,theme=?,asset_type=?,updated_at=? WHERE owner_id=? AND symbol=?",
                (name, theme, asset_type, now_iso(), user_id, row["symbol"]),
            )
            updated += 1
    if updated:
        rebuild_alerts(user_id)
    return {"ok": True, "updated": updated}


def recognize_positions(body: dict[str, Any], user_id: str = LEGACY_OWNER_ID) -> dict[str, Any]:
    warnings: list[str] = []
    engine = None
    text = str(body.get("text") or "")
    table_positions: list[dict[str, Any]] = []
    if not text and body.get("image_data"):
        image_bytes, decode_err = image_bytes_from_data_uri(str(body.get("image_data")))
        if decode_err or image_bytes is None:
            warnings.append(decode_err or "图片读取失败")
        else:
            table_positions, table_text, table_warnings = recognize_broker_table_positions(image_bytes)
            warnings.extend(table_warnings)
            if table_positions:
                text = table_text
                engine = "broker-table-windows-ocr"
            else:
                text, engine, err = ocr_text_from_image_data(str(body.get("image_data")))
                if err:
                    warnings.append(err)
                text = text or ""
    if body.get("positions"):
        positions = normalize_position_payload(body.get("positions") or [])
    elif table_positions:
        positions = normalize_position_payload(table_positions)
    else:
        positions, parse_warnings = parse_position_text(text)
        warnings.extend(parse_warnings)
        product_positions, product_warnings = parse_product_positions_text(text)
        warnings.extend(product_warnings)
        if product_positions:
            warnings = [w for w in warnings if not w.startswith("未识别出有效持仓行")]
        seen = {item["symbol"] for item in positions}
        for item in product_positions:
            if item["symbol"] not in seen:
                positions.append(item)
                seen.add(item["symbol"])
        positions = normalize_position_payload(positions)
    imported = 0
    if body.get("apply"):
        quote_refresh_needed = False
        for item in normalize_position_payload(positions):
            upsert_position(item, user_id)
            if is_six_digit_symbol(item.get("symbol")):
                DB.execute("INSERT OR IGNORE INTO watchlist(owner_id,symbol,name) VALUES(?,?,?)", (user_id, item["symbol"], item.get("name", "")))
                quote_refresh_needed = True
            imported += 1
        if imported:
            if quote_refresh_needed:
                try:
                    refresh_quotes()
                except Exception as exc:
                    warnings.append(f"已导入，但实时行情刷新失败: {type(exc).__name__}: {exc}")
            rebuild_alerts(user_id)
    return {"ok": bool(positions), "engine": engine, "text": text, "positions": positions, "warnings": warnings, "imported": imported}



def fetch_tencent(symbols: list[str], timeout: float = 6.0) -> tuple[list[Quote], str | None, float]:
    """兼容旧调用入口；具体实现位于 providers/tencent.py。"""
    cfg = config()
    return provider_fetch_tencent(
        symbols,
        timeout=timeout,
        retries=int(cfg.get("quote_retry_count", 2)),
        batch_size=int(cfg.get("quote_batch_size", 60)),
    )


def fetch_mootdx(symbols: list[str]) -> tuple[list[Quote], str | None, float]:
    """兼容旧调用入口；具体实现位于 providers/mootdx_provider.py。"""
    cfg = config()
    return provider_fetch_mootdx(
        symbols,
        server=str(cfg.get("mootdx_server") or ""),
        retries=int(cfg.get("quote_retry_count", 2)),
        batch_size=int(cfg.get("quote_batch_size", 60)),
    )


def save_provider_quotes(provider: str, rows: list[Quote], error: str | None, latency: float, expected_symbols: list[str] | None = None):
    expected = set(expected_symbols or [])
    seen = {q.symbol for q in rows}
    for q in rows:
        DB.execute(
            """INSERT INTO quotes(provider,symbol,name,last_price,prev_close,open_price,high,low,volume,amount,bid1,ask1,quote_time,fetch_time,status,error)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(provider,symbol) DO UPDATE SET name=excluded.name,last_price=excluded.last_price,prev_close=excluded.prev_close,
               open_price=excluded.open_price,high=excluded.high,low=excluded.low,volume=excluded.volume,amount=excluded.amount,
               bid1=excluded.bid1,ask1=excluded.ask1,quote_time=excluded.quote_time,fetch_time=excluded.fetch_time,status=excluded.status,error=excluded.error""",
            (provider, q.symbol, q.name or "", q.last_price, q.prev_close, q.open_price, q.high, q.low,
             q.volume, q.amount, q.bid1, q.ask1, q.quote_time, q.fetch_time, q.status, q.error)
        )
    for symbol in sorted(expected - seen):
        existing = DB.one("SELECT provider,symbol FROM quotes WHERE provider=? AND symbol=?", (provider, symbol))
        status = "STALE" if existing else "NO_DATA"
        if existing:
            DB.execute("UPDATE quotes SET status=?,error=?,fetch_time=? WHERE provider=? AND symbol=?", (status, error or "本次刷新未返回该代码", now_iso(), provider, symbol))
        else:
            DB.execute("INSERT OR REPLACE INTO quotes(provider,symbol,status,error,fetch_time) VALUES(?,?,?,?,?)", (provider, symbol, status, error or "本次刷新未返回该代码", now_iso()))
    health_status = "OK" if rows and not error and not (expected - seen) else "PARTIAL" if rows else "PROVIDER_UNAVAILABLE"
    update_source_health(provider, health_status, latency, error, success=bool(rows))


def validate_quotes(symbols: list[str]):
    cfg = config()
    warn = float(cfg["price_warn_deviation"])
    block = float(cfg["price_block_deviation"])
    stale_seconds = float(cfg.get("quote_stale_seconds", 20))
    for symbol in symbols:
        rows = DB.all("SELECT * FROM quotes WHERE symbol=? ORDER BY provider", (symbol,))
        fresh_rows = []
        stale_providers = []
        for row in rows:
            if row.get("status") == "OK" and row_is_fresh(row, stale_seconds):
                fresh_rows.append(row)
            elif row.get("status") == "OK":
                stale_providers.append(row.get("provider"))
                DB.execute("UPDATE quotes SET status=?,error=? WHERE provider=? AND symbol=?", ("STALE", "行情超过有效期", row.get("provider"), symbol))
        by_provider = {r["provider"]: r for r in fresh_rows}
        primary = by_provider.get("mootdx")
        secondary = by_provider.get("tencent")
        reasons = []
        deviation = None
        level = "BLOCK"
        chosen = None
        if primary and secondary:
            p1, p2 = primary["last_price"], secondary["last_price"]
            if p1 and p2:
                deviation = abs(p1-p2)/p2
                chosen = primary
                if deviation <= warn:
                    level = "OK"
                    reasons.append("双源价格一致")
                elif deviation <= block:
                    level = "WARN"
                    reasons.append("双源存在轻微偏差")
                else:
                    level = "BLOCK"
                    reasons.append("双源价格偏差超限")
        elif primary or secondary:
            chosen = primary or secondary
            level = "WARN"
            reasons.append("仅有单一新鲜行情源，禁止强买入信号")
        else:
            reasons.append("无有效新鲜行情")
        if stale_providers:
            reasons.append("过期行情已屏蔽: " + ",".join(str(x) for x in stale_providers if x))
        name = (chosen or {}).get("name", "") if isinstance(chosen, dict) else ""
        price = (chosen or {}).get("last_price") if isinstance(chosen, dict) else None
        DB.execute(
            """INSERT INTO quote_validation(symbol,name,last_price,level,primary_provider,secondary_provider,deviation,reasons,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET name=excluded.name,last_price=excluded.last_price,
               level=excluded.level,primary_provider=excluded.primary_provider,secondary_provider=excluded.secondary_provider,
               deviation=excluded.deviation,reasons=excluded.reasons,updated_at=excluded.updated_at""",
            (symbol, name, price, level, "mootdx" if primary else None, "tencent" if secondary else None,
             deviation, json.dumps(reasons, ensure_ascii=False), now_iso())
        )


def refresh_quotes() -> dict[str, Any]:
    symbols = {r["symbol"] for r in DB.all("SELECT symbol FROM watchlist") if is_six_digit_symbol(r["symbol"])}
    symbols |= {r["symbol"] for r in DB.all("SELECT symbol FROM positions") if is_six_digit_symbol(r["symbol"])}
    symbols = sorted(symbols)
    if not symbols:
        return {"symbols": [], "tencent": {"count": 0, "error": None}, "mootdx": {"count": 0, "error": None}}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            "tencent": pool.submit(fetch_tencent, symbols),
            "mootdx": pool.submit(fetch_mootdx, symbols),
        }
        t_rows, t_err, t_latency = futures["tencent"].result()
        m_rows, m_err, m_latency = futures["mootdx"].result()
    save_provider_quotes("tencent", t_rows, t_err, t_latency, symbols)
    save_provider_quotes("mootdx", m_rows, m_err, m_latency, symbols)
    validate_quotes(symbols)
    rebuild_alerts()
    return {"symbols": symbols, "tencent": {"count": len(t_rows), "error": t_err}, "mootdx": {"count": len(m_rows), "error": m_err}}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _market_signal_score(rank: int | None, pct: float | None, rank_chg: int | None) -> dict[str, float]:
    rank_value = max(1, int(rank or 999))
    pct_value = float(pct or 0)
    base = 88 - min(rank_value - 1, 60) * 0.55
    trend = 72 + max(0, 25 - rank_value) * 0.7
    if rank_chg is not None and rank_chg < 0:
        trend += 4
    if pct_value > 7:
        risk = 68
    elif pct_value < -5:
        risk = 62
    else:
        risk = 45 + abs(pct_value) * 1.8
    total = base + (trend - 70) * 0.12 - max(0, risk - 55) * 0.18
    return {
        "trend": round(_clamp(trend, 35, 95), 2),
        "risk": round(_clamp(risk, 30, 88), 2),
        "total": round(_clamp(total, 50, 88), 2),
    }


def refresh_market_signals() -> dict[str, Any]:
    astock_cfg = a_stock_data_config()
    if not astock_cfg.get("enabled") or not astock_cfg.get("signal_enabled"):
        update_source_health("a-stock-data:hot_rank", "DISABLED", 0, "a_stock_data.signal_enabled=false", success=False)
        return {"ok": False, "status": "DISABLED", "imported": 0, "watchlist_added": 0}
    rows, err, latency = provider_fetch_hot_rank(
        top=int(astock_cfg.get("hot_rank_top", 30)),
        timeout=float(config().get("news_timeout_seconds", 8)),
        retries=int(config().get("news_retry_count", 2)),
        min_interval=float(astock_cfg.get("eastmoney_min_interval", 1.2)),
    )
    if not rows:
        update_source_health("a-stock-data:hot_rank", "PROVIDER_UNAVAILABLE", latency, err, success=False)
        return {"ok": False, "status": "PROVIDER_UNAVAILABLE", "error": err, "imported": 0, "watchlist_added": 0}

    DB.execute("DELETE FROM stock_scores WHERE source_status='A_STOCK_DATA_SIGNAL'")
    imported = 0
    watchlist_added = 0
    watch_limit = int(astock_cfg.get("signal_watchlist_limit", 10))
    for row in rows:
        symbol = normalize_symbol(row.get("symbol", ""))
        if symbol == "000000":
            continue
        score = _market_signal_score(row.get("rank"), row.get("pct"), row.get("rank_chg"))
        rank = row.get("rank") or 999
        pct = row.get("pct")
        name = row.get("name") or ""
        grade = "市场热度线索" if score["total"] >= 70 else "待观察"
        DB.execute(
            """INSERT OR REPLACE INTO stock_scores(symbol,name,industry,quality,growth,valuation,trend,risk,fund_signal,total_score,grade,data_date,source_status,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                symbol, name, "市场热度", None, None, None, score["trend"], score["risk"], 0,
                score["total"], grade, now_iso(), "A_STOCK_DATA_SIGNAL", now_iso()
            ),
        )
        if imported < watch_limit:
            DB.execute("INSERT OR IGNORE INTO watchlist(owner_id,symbol,name) VALUES(?,?,?)", (SYSTEM_OWNER_ID, symbol, name))
            if name:
                DB.execute("UPDATE watchlist SET name=CASE WHEN name='' THEN ? ELSE name END WHERE owner_id=? AND symbol=?", (name, SYSTEM_OWNER_ID, symbol))
            watchlist_added += 1
        imported += 1
        if pct is not None and abs(float(pct)) >= 7:
            title = f"{symbol} {name} 热度榜第{rank}名，涨跌幅{float(pct):.2f}%"
            DB.execute(
                """INSERT OR REPLACE INTO news_events(event_id,title,source,source_level,published_at,original_url,symbols,themes,opportunity_score,risk_score,verification,is_original,source_root,fetched_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    hashlib.sha256(title.encode("utf-8")).hexdigest()[:24], title, "东方财富人气榜", "C",
                    now_iso(), "", json.dumps([symbol], ensure_ascii=False), json.dumps(["市场热度"], ensure_ascii=False),
                    55 if float(pct) > 0 else 0, 60 if abs(float(pct)) >= 9 else 35,
                    "PENDING", 0, "eastmoney_hot_rank", now_iso()
                ),
            )

    update_source_health("a-stock-data:hot_rank", "OK", latency, err, success=True)
    rebuild_alerts()
    return {
        "ok": True,
        "status": "OK",
        "imported": imported,
        "watchlist_added": min(watchlist_added, watch_limit),
        "error": err,
    }


def refresh_all_sources() -> dict[str, Any]:
    selection = refresh_market_signals()
    quotes = refresh_quotes()
    news = refresh_news_sources()
    valuation = refresh_stock_valuations()
    return {"ok": any([selection.get("ok"), bool(quotes.get("symbols")), news.get("ok"), valuation.get("ok")]), "selection": selection, "quotes": quotes, "news": news, "valuation": valuation}


def current_report_period() -> str:
    today = datetime.now(CN_TZ).date()
    quarter = (today.month - 1) // 3 + 1
    return f"{today.year}Q{quarter}"


def stable_id(*parts: Any, length: int = 24) -> str:
    raw = "|".join(str(p or "") for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def latest_fund_report_period() -> str | None:
    row = DB.one("SELECT report_period FROM fund_reports ORDER BY report_period DESC LIMIT 1")
    return row.get("report_period") if row else None


def fund_code_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[,，\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []
    out: list[str] = []
    for item in raw_items:
        code = normalize_symbol(str(item or ""))
        if code != "000000" and code not in out:
            out.append(code)
    return out


def year_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[,，\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []
    out: list[str] = []
    for item in raw_items:
        match = re.search(r"\d{4}", str(item or ""))
        if match and match.group(0) not in out:
            out.append(match.group(0))
    return out


def infer_holding_flags(item: dict[str, Any]) -> tuple[int, int, float | None]:
    change = item.get("change_shares")
    if change is None:
        change = item.get("share_change")
    try:
        change_value = float(change) if change not in (None, "", "--") else None
    except (TypeError, ValueError):
        change_value = None
    status = str(item.get("holding_status") or item.get("status") or "").upper()
    visible_new = 1 if item.get("visible_new") or status in {"NEW", "VISIBLE_NEW", "新增可见"} else 0
    confirmed = 1 if item.get("confirmed_increase") or (change_value is not None and change_value > 0) or status in {"INCREASE", "增持"} else 0
    return visible_new, confirmed, change_value


def import_official_fund_payload(payload: dict[str, Any]) -> dict[str, int]:
    imported = {"managers": 0, "products": 0, "reports": 0, "holdings": 0, "consensus": 0, "stocks": 0}
    for item in payload.get("managers", []) or []:
        name = str(item.get("name") or "").strip()
        company = str(item.get("company") or "").strip()
        if not name or not company:
            continue
        manager_id = item.get("manager_id") or stable_id(name, company, item.get("source_url"), length=16)
        score = float(item.get("score", item.get("manager_score", 60)))
        DB.execute(
            "INSERT OR REPLACE INTO fund_managers(manager_id,name,company,score,tenure_years,report_period,source_url,evidence_status,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (manager_id, name, company, score, item.get("tenure_years"), item.get("report_period"), item.get("source_url"), item.get("evidence_status", "VERIFIED"), now_iso()),
        )
        imported["managers"] += 1

    for item in payload.get("products", []) or payload.get("funds", []) or []:
        fund_code = normalize_symbol(item.get("fund_code") or item.get("code") or "")
        if fund_code == "000000":
            continue
        manager_name = item.get("manager_name") or item.get("manager") or ""
        manager_id = item.get("manager_id") or (stable_id(manager_name, item.get("company"), length=16) if manager_name else "")
        DB.execute(
            """INSERT OR REPLACE INTO fund_products(fund_code,name,company,manager_id,manager_name,category,strategy_track,benchmark,asset_size,fee_rate,source_url,evidence_status,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                fund_code, item.get("name", ""), item.get("company", ""), manager_id, manager_name,
                item.get("category", ""), item.get("strategy_track", ""), item.get("benchmark", ""),
                item.get("asset_size"), item.get("fee_rate"), item.get("source_url", ""),
                item.get("evidence_status", "VERIFIED"), now_iso(),
            ),
        )
        imported["products"] += 1

    report_lookup: dict[str, dict[str, Any]] = {}
    for item in payload.get("reports", []) or []:
        period = str(item.get("report_period") or item.get("period") or "").strip()
        if not period:
            continue
        fund_code = normalize_symbol(item.get("fund_code") or item.get("code") or "")
        manager_name = item.get("manager_name") or item.get("manager") or ""
        manager_id = item.get("manager_id") or (stable_id(manager_name, item.get("company"), length=16) if manager_name else "")
        report_id = item.get("report_id") or stable_id(fund_code, period, item.get("source_url"), manager_id)
        report_lookup[report_id] = {**item, "report_id": report_id, "fund_code": fund_code, "manager_id": manager_id, "manager_name": manager_name, "report_period": period}
        DB.execute(
            """INSERT OR REPLACE INTO fund_reports(report_id,fund_code,fund_name,manager_id,manager_name,company,report_period,report_type,announcement_date,source_url,pdf_sha256,parser_status,coverage,evidence_status,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                report_id, fund_code, item.get("fund_name") or item.get("name", ""), manager_id, manager_name,
                item.get("company", ""), period, item.get("report_type", "quarterly"), item.get("announcement_date", ""),
                item.get("source_url", ""), item.get("pdf_sha256", ""), item.get("parser_status", "IMPORTED"),
                float(item.get("coverage", 0) or 0), item.get("evidence_status", "VERIFIED"), now_iso(),
            ),
        )
        imported["reports"] += 1

    if report_lookup:
        DB.executemany("DELETE FROM fund_report_holdings WHERE report_id=?", [(report_id,) for report_id in report_lookup])

    for item in payload.get("holdings", []) or []:
        symbol = normalize_symbol(item.get("symbol") or item.get("stock_code") or "")
        if symbol == "000000":
            continue
        report_id = item.get("report_id")
        if not report_id:
            fund_code = normalize_symbol(item.get("fund_code") or item.get("code") or "")
            period = item.get("report_period") or item.get("period")
            report_id = stable_id(fund_code, period, item.get("source_url"), item.get("manager_id") or item.get("manager_name"))
            if not DB.one("SELECT report_id FROM fund_reports WHERE report_id=?", (report_id,)):
                DB.execute(
                    """INSERT OR REPLACE INTO fund_reports(report_id,fund_code,fund_name,manager_id,manager_name,company,report_period,source_url,evidence_status,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        report_id, fund_code, item.get("fund_name", ""), item.get("manager_id", ""),
                        item.get("manager_name", ""), item.get("company", ""), period or current_report_period(),
                        item.get("source_url", ""), item.get("evidence_status", "VERIFIED"), now_iso(),
                    ),
                )
                imported["reports"] += 1
        visible_new, confirmed, change_value = infer_holding_flags(item)
        DB.execute(
            """INSERT OR REPLACE INTO fund_report_holdings(report_id,symbol,name,industry,holding_rank,market_value,nav_ratio,shares,previous_shares,change_shares,change_ratio,holding_status,visible_new,confirmed_increase,source_page,evidence_status,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                report_id, symbol, item.get("name", ""), item.get("industry", ""), item.get("holding_rank") or item.get("rank"),
                item.get("market_value"), item.get("nav_ratio"), item.get("shares"), item.get("previous_shares"),
                change_value, item.get("change_ratio"), item.get("holding_status", "VISIBLE"),
                visible_new, confirmed, item.get("source_page", ""), item.get("evidence_status", "VERIFIED"), now_iso(),
            ),
        )
        imported["holdings"] += 1

    rebuilt = rebuild_fund_consensus(payload.get("report_period") or latest_fund_report_period())
    imported["consensus"] = rebuilt["consensus"]
    imported["stocks"] = rebuilt["stocks"]
    rebuild_alerts()
    return imported


def import_stock_due_diligence_items(items: list[dict[str, Any]]) -> int:
    imported = 0
    for item in items:
        symbol = normalize_symbol(item.get("symbol") or item.get("stock_code") or "")
        if symbol == "000000":
            continue
        existing = DB.one("SELECT * FROM stock_due_diligence WHERE symbol=?", (symbol,)) or {}

        def keep(key: str, default: Any = None) -> Any:
            value = item.get(key)
            if value is None or value == "":
                return existing.get(key, default)
            return value

        def keep_bool(key: str) -> int:
            if key in item and item.get(key) is not None:
                return 1 if item.get(key) else 0
            return int(existing.get(key) or 0)

        DB.execute(
            """INSERT OR REPLACE INTO stock_due_diligence(
                symbol,name,profit_trend,cashflow_quality,debt_risk,industry_outlook,competitive_position,
                valuation_percentile,price_drawdown_pct,post_disclosure_runup_pct,
                earnings_decline,receivable_inventory_goodwill_risk,governance_risk,industry_decline_risk,
                source_url,evidence_status,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                symbol, keep("name", ""), keep("profit_trend"), keep("cashflow_quality"),
                keep("debt_risk"), keep("industry_outlook"), keep("competitive_position"),
                keep("valuation_percentile"), keep("price_drawdown_pct"), keep("post_disclosure_runup_pct"),
                keep_bool("earnings_decline"),
                keep_bool("receivable_inventory_goodwill_risk"),
                keep_bool("governance_risk"),
                keep_bool("industry_decline_risk"),
                keep("source_url", ""), keep("evidence_status", "VERIFIED"), now_iso(),
            ),
        )
        imported += 1
    if imported:
        rebuild_fund_consensus(latest_fund_report_period())
        rebuild_alerts()
    return imported


def latest_disclosure_dates_for_symbols(symbols: list[str]) -> dict[str, str]:
    cleaned = [normalize_symbol(x) for x in symbols if normalize_symbol(x) != "000000"]
    if not cleaned:
        return {}
    placeholders = ",".join("?" for _ in cleaned)
    rows = DB.all(
        f"""SELECT h.symbol, MAX(NULLIF(r.announcement_date, '')) AS announcement_date
            FROM fund_report_holdings h JOIN fund_reports r ON r.report_id=h.report_id
            WHERE h.symbol IN ({placeholders})
            GROUP BY h.symbol""",
        tuple(cleaned),
    )
    return {row["symbol"]: row.get("announcement_date") for row in rows if row.get("announcement_date")}


def valuation_symbol_pool(limit: int | None = None) -> list[str]:
    symbols: list[str] = []
    for row in DB.all(
        """SELECT symbol FROM stock_scores
           ORDER BY CASE source_status WHEN 'OFFICIAL_FUND_REPORT' THEN 0 ELSE 1 END,
                    COALESCE(total_score, 0) DESC LIMIT 300"""
    ):
        symbol = normalize_symbol(row.get("symbol"))
        if is_equity_symbol(symbol) and symbol not in symbols:
            symbols.append(symbol)
    for row in DB.all("SELECT symbol FROM fund_report_holdings ORDER BY updated_at DESC LIMIT 300"):
        symbol = normalize_symbol(row.get("symbol"))
        if is_equity_symbol(symbol) and symbol not in symbols:
            symbols.append(symbol)
    for row in DB.all("SELECT DISTINCT symbol FROM watchlist ORDER BY symbol LIMIT 200"):
        symbol = normalize_symbol(row.get("symbol"))
        if is_equity_symbol(symbol) and symbol not in symbols:
            symbols.append(symbol)
    return symbols[:limit] if limit else symbols


def upsert_stock_valuations(rows: list[dict[str, Any]]) -> int:
    imported = 0
    diligence_items: list[dict[str, Any]] = []
    for row in rows:
        symbol = normalize_symbol(row.get("symbol"))
        if symbol == "000000":
            continue
        DB.execute(
            """INSERT OR REPLACE INTO stock_valuations(
                symbol,name,pe_ttm,pb,price_percentile,valuation_percentile,
                price_drawdown_pct,post_disclosure_runup_pct,lookback_days,
                source,source_url,evidence_status,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                symbol, row.get("name", ""), row.get("pe_ttm"), row.get("pb"), row.get("price_percentile"),
                row.get("valuation_percentile"), row.get("price_drawdown_pct"), row.get("post_disclosure_runup_pct"),
                row.get("lookback_days"), row.get("source", ""), row.get("source_url", ""),
                row.get("evidence_status", "PARTIAL_VALUATION_PROXY"), now_iso(),
            ),
        )
        diligence_items.append(
            {
                "symbol": symbol,
                "name": row.get("name", ""),
                "valuation_percentile": row.get("valuation_percentile"),
                "price_drawdown_pct": row.get("price_drawdown_pct"),
                "post_disclosure_runup_pct": row.get("post_disclosure_runup_pct"),
                "source_url": row.get("source_url", ""),
                "evidence_status": row.get("evidence_status", "PARTIAL_VALUATION_PROXY"),
            }
        )
        imported += 1
    if diligence_items:
        import_stock_due_diligence_items(diligence_items)
    return imported


def refresh_stock_valuations(symbols: list[str] | None = None) -> dict[str, Any]:
    astock_cfg = a_stock_data_config()
    if not astock_cfg.get("enabled") or not astock_cfg.get("valuation_enabled", True):
        update_source_health("a-stock-data:stock_valuation", "DISABLED", 0, "valuation_enabled=false", success=False)
        return {"ok": False, "status": "DISABLED", "imported": 0}
    limit = int(astock_cfg.get("valuation_symbol_limit", 80))
    target_symbols = [normalize_symbol(x) for x in (symbols or valuation_symbol_pool(limit)) if is_equity_symbol(normalize_symbol(x))]
    deduped: list[str] = []
    for symbol in target_symbols:
        if symbol not in deduped:
            deduped.append(symbol)
    if not deduped:
        update_source_health("a-stock-data:stock_valuation", "NO_SYMBOLS", 0, "无可估值A股代码", success=False)
        return {"ok": False, "status": "NO_SYMBOLS", "imported": 0}
    rows, err, latency = provider_fetch_stock_valuation(
        deduped[:limit],
        disclosure_dates=latest_disclosure_dates_for_symbols(deduped[:limit]),
        lookback_days=int(astock_cfg.get("valuation_lookback_days", 1095)),
        timeout=float(config().get("news_timeout_seconds", 8)),
        retries=int(config().get("news_retry_count", 2)),
        min_interval=float(astock_cfg.get("eastmoney_min_interval", 1.2)),
    )
    imported = upsert_stock_valuations(rows)
    status = "OK" if imported else "NO_DATA"
    update_source_health("a-stock-data:stock_valuation", status, latency, err, success=bool(imported))
    return {"ok": bool(imported), "status": status, "imported": imported, "symbols": deduped[:limit], "error": err}


def period_key(period: str | None) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})Q([1-4])", str(period or ""))
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def split_manager_names(value: str | None) -> list[str]:
    names = []
    for raw in re.split(r"[、,，/\s]+", str(value or "")):
        name = raw.strip()
        if re.fullmatch(r"[\u4e00-\u9fff·]{2,8}", name) and name not in names:
            names.append(name)
    return names


def manager_records_for_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        company = item.get("company") or ""
        for name in split_manager_names(item.get("manager_name")):
            row = DB.one("SELECT * FROM fund_managers WHERE name=? AND company=?", (name, company))
            if row:
                key = row.get("manager_id") or f"{name}|{company}"
                if key not in seen:
                    seen.add(key)
                    records.append(row)
    return records


def consecutive_increase_for_symbol(symbol: str, report_period: str) -> int:
    rows = DB.all(
        """SELECT h.symbol,h.confirmed_increase,r.fund_code,r.report_period
           FROM fund_report_holdings h JOIN fund_reports r ON r.report_id=h.report_id
           WHERE h.symbol=? ORDER BY r.fund_code,r.report_period""",
        (symbol,),
    )
    by_fund: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_fund.setdefault(row.get("fund_code") or "", []).append(row)
    count = 0
    for fund_rows in by_fund.values():
        fund_rows.sort(key=lambda x: period_key(x.get("report_period")))
        for idx, row in enumerate(fund_rows):
            if row.get("report_period") != report_period or idx == 0:
                continue
            prev = fund_rows[idx - 1]
            if int(row.get("confirmed_increase") or 0) and int(prev.get("confirmed_increase") or 0):
                count += 1
    return count


def due_diligence_for_symbol(symbol: str) -> dict[str, Any]:
    row = DB.one("SELECT * FROM stock_due_diligence WHERE symbol=?", (symbol,)) or {}
    flags: list[str] = []
    if not row:
        return {
            "fundamental_signal": 45,
            "valuation_signal": 45,
            "valuation_percentile": None,
            "price_drawdown_pct": None,
            "post_disclosure_runup_pct": None,
            "fundamental_status": "UNKNOWN",
            "valuation_status": "UNKNOWN",
            "exclusion_flags": [],
            "notes": ["缺少公司盈利、现金流、负债、行业景气和估值分位证据"],
        }
    if int(row.get("earnings_decline") or 0) or (row.get("profit_trend") is not None and float(row.get("profit_trend") or 0) < 40):
        flags.append("利润连续下滑或盈利趋势破坏")
    if int(row.get("receivable_inventory_goodwill_risk") or 0):
        flags.append("应收账款/存货/商誉异常")
    if int(row.get("governance_risk") or 0):
        flags.append("管理层减持或治理风险")
    if int(row.get("industry_decline_risk") or 0):
        flags.append("行业长期衰退风险")
    if row.get("post_disclosure_runup_pct") is not None and float(row.get("post_disclosure_runup_pct") or 0) >= 30:
        flags.append("新进重仓后股价已大涨30%以上")

    fundamental_parts = [
        row.get("profit_trend"),
        row.get("cashflow_quality"),
        100 - float(row.get("debt_risk")) if row.get("debt_risk") is not None else None,
        row.get("industry_outlook"),
        row.get("competitive_position"),
    ]
    fundamental_values = [float(x) for x in fundamental_parts if x is not None]
    fundamental_signal = round(sum(fundamental_values) / len(fundamental_values), 2) if fundamental_values else 45
    valuation_percentile = row.get("valuation_percentile")
    price_drawdown_pct = row.get("price_drawdown_pct")
    if valuation_percentile is None:
        valuation_signal = 45
        valuation_status = "UNKNOWN"
    else:
        valuation_signal = round(_clamp(100 - float(valuation_percentile), 0, 100), 2)
        has_pullback = price_drawdown_pct is None or float(price_drawdown_pct or 0) <= -5
        valuation_status = "PASS" if float(valuation_percentile) <= 50 and has_pullback and "利润连续下滑或盈利趋势破坏" not in flags else "FAIL"
    fundamental_status = "PASS" if fundamental_signal >= 60 and not any(x in flags for x in ["利润连续下滑或盈利趋势破坏", "应收账款/存货/商誉异常", "管理层减持或治理风险", "行业长期衰退风险"]) else "FAIL"
    notes = []
    if valuation_status == "UNKNOWN":
        notes.append("缺少历史估值分位或价格回撤证据")
    elif valuation_percentile is not None:
        notes.append(f"估值代理分位{float(valuation_percentile):.1f}%，价格回撤{float(price_drawdown_pct or 0):.1f}%")
    if flags:
        notes.append("触发排除项：" + "；".join(flags))
    return {
        "fundamental_signal": fundamental_signal,
        "valuation_signal": valuation_signal,
        "valuation_percentile": valuation_percentile,
        "price_drawdown_pct": price_drawdown_pct,
        "post_disclosure_runup_pct": row.get("post_disclosure_runup_pct"),
        "fundamental_status": fundamental_status,
        "valuation_status": valuation_status,
        "exclusion_flags": flags,
        "notes": notes,
    }


def triple_confirmation_score(symbol: str, items: list[dict[str, Any]], report_period: str, consensus_score: float) -> dict[str, Any]:
    managers = manager_records_for_items(items)
    excellent = [m for m in managers if float(m.get("score") or 0) >= 70 and float(m.get("tenure_years") or 0) >= 3]
    companies = {x.get("company") for x in items if x.get("company")}
    products = {x.get("fund_code") for x in items if x.get("fund_code")}
    style_values = {x for x in (item.get("strategy_track") or item.get("category") or "" for item in items) if x}
    style_count = max(len(style_values), min(len(products), len(excellent)))
    consecutive = consecutive_increase_for_symbol(symbol, report_period)
    confirmed = sum(1 for x in items if int(x.get("confirmed_increase") or 0))
    visible_new = sum(1 for x in items if int(x.get("visible_new") or 0))
    manager_signal = _clamp(32 + len(excellent) * 14 + len(companies) * 5 + confirmed * 6 + consecutive * 12 + style_count * 4 + visible_new * 2, 0, 100)
    manager_status = "PASS" if len(excellent) >= 2 and style_count >= 2 and consecutive >= 2 else "PARTIAL" if len(excellent) >= 1 and (confirmed or consecutive) else "WEAK"
    due = due_diligence_for_symbol(symbol)
    triple_status = "TRIPLE_CONFIRMED" if manager_status == "PASS" and due["fundamental_status"] == "PASS" and due["valuation_status"] == "PASS" and not due["exclusion_flags"] else "EXCLUDED" if due["exclusion_flags"] else "RESEARCH_ONLY_NEEDS_FUNDAMENTAL_VALUATION" if manager_status in {"PASS", "PARTIAL"} else "WATCH_ONLY"
    notes = [
        f"基金经理层：{manager_status}，优秀经理{len(excellent)}位，连续增持{consecutive}条，多公司{len(companies)}家。",
        f"基本面层：{due['fundamental_status']}。",
        f"估值价格层：{due['valuation_status']}。",
    ] + due["notes"]
    if triple_status != "EXCLUDED":
        notes.append("根据已有结论推断出；基金季报增持与低估值只支持进入观察池，仍需公司财报、行业景气和价格行为继续验证。")
    return {
        "manager_signal": round(manager_signal, 2),
        "fundamental_signal": due["fundamental_signal"],
        "valuation_signal": due["valuation_signal"],
        "valuation_percentile": due["valuation_percentile"],
        "price_drawdown_pct": due["price_drawdown_pct"],
        "post_disclosure_runup_pct": due["post_disclosure_runup_pct"],
        "manager_status": manager_status,
        "triple_confirm_status": triple_status,
        "consecutive_increase": consecutive,
        "excellent_manager_count": len(excellent),
        "style_count": style_count,
        "exclusion_flags": due["exclusion_flags"],
        "notes": notes,
    }


def rebuild_fund_consensus(report_period: str | None = None) -> dict[str, int]:
    if not report_period:
        report_period = latest_fund_report_period()
    if not report_period:
        return {"consensus": 0, "stocks": 0}
    rows = DB.all(
        """SELECT h.*, r.report_period, r.fund_code, r.manager_id, r.manager_name, r.company, r.source_url,
                  r.evidence_status AS report_evidence, p.category, p.strategy_track
           FROM fund_report_holdings h JOIN fund_reports r ON r.report_id=h.report_id
           LEFT JOIN fund_products p ON p.fund_code=r.fund_code
           WHERE r.report_period=?""",
        (report_period,),
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["symbol"], []).append(row)
    consensus_count = 0
    stock_count = 0
    DB.execute("DELETE FROM stock_scores WHERE source_status='OFFICIAL_FUND_REPORT'")
    for symbol, items in grouped.items():
        managers = {x.get("manager_id") or x.get("manager_name") for x in items if x.get("manager_id") or x.get("manager_name")}
        companies = {x.get("company") for x in items if x.get("company")}
        confirmed = sum(1 for x in items if int(x.get("confirmed_increase") or 0))
        visible_new = sum(1 for x in items if int(x.get("visible_new") or 0))
        manager_count = len(managers)
        company_count = len(companies)
        quote = DB.one("SELECT * FROM quote_validation WHERE symbol=?", (symbol,)) or {}
        name = next((x.get("name") for x in items if x.get("name")), "") or quote.get("name", "")
        industry = next((x.get("industry") for x in items if x.get("industry")), "")
        evidence_ok = all(str(x.get("report_evidence", "")).upper() in {"VERIFIED", "OFFICIAL", "HIGH_CONFIDENCE"} for x in items)
        consensus_score = _clamp(42 + manager_count * 9 + company_count * 6 + confirmed * 8 + visible_new * 4, 0, 100)
        triple = triple_confirmation_score(symbol, items, report_period, consensus_score)
        evidence_status = "VERIFIED" if evidence_ok else "PARTIAL"
        source_url = next((x.get("source_url") for x in items if x.get("source_url")), "")
        DB.execute(
            """INSERT OR REPLACE INTO fund_consensus(symbol,name,report_period,manager_count,company_count,confirmed_increase,new_visible,consecutive_increase,excellent_manager_count,style_count,triple_confirm_status,consensus_score,source_url,evidence_status,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                symbol, name, report_period, manager_count, company_count, confirmed, visible_new,
                triple["consecutive_increase"], triple["excellent_manager_count"], triple["style_count"],
                triple["triple_confirm_status"], round(consensus_score, 2), source_url, evidence_status, now_iso(),
            ),
        )
        consensus_count += 1
        quote_level = quote.get("level", "BLOCK")
        trend = 72 if quote_level == "OK" else 62 if quote_level == "WARN" else 45
        base_risk = 42 if quote_level == "OK" else 58 if quote_level == "WARN" else 82
        risk = min(95, base_risk + len(triple["exclusion_flags"]) * 16)
        quality = triple["fundamental_signal"]
        growth = triple["fundamental_signal"]
        valuation = triple["valuation_signal"]
        fund_signal = round(consensus_score, 2)
        total = _clamp(triple["manager_signal"] * 0.36 + quality * 0.26 + valuation * 0.24 + trend * 0.08 + (100 - risk) * 0.06)
        if triple.get("valuation_percentile") is not None and float(triple["valuation_percentile"]) <= float(a_stock_data_config().get("undervalued_percentile", 35)):
            total += 6
        if triple.get("price_drawdown_pct") is not None and float(triple["price_drawdown_pct"]) <= -15:
            total += 2
        total = _clamp(total, 0, 100)
        if triple["triple_confirm_status"] == "EXCLUDED":
            total = min(total, 35)
            grade = "D-排除"
        elif triple["triple_confirm_status"] == "TRIPLE_CONFIRMED":
            total = max(total, 78)
            grade = "B-三重确认观察" if total < 85 else "A-优先尽调"
        elif triple["triple_confirm_status"] == "RESEARCH_ONLY_NEEDS_FUNDAMENTAL_VALUATION":
            total = min(total, 68)
            grade = "C-观察待财务估值确认"
        else:
            total = min(total, 58)
            grade = "C-仅基金线索观察"
        total = round(total, 2)
        DB.execute(
            """INSERT OR REPLACE INTO stock_scores(symbol,name,industry,quality,growth,valuation,trend,risk,fund_signal,total_score,grade,data_date,manager_signal,fundamental_signal,valuation_signal,triple_confirm_status,exclusion_flags,scoring_notes,source_status,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                symbol, name, industry, quality, growth, valuation, trend, risk, fund_signal, total, grade, report_period,
                triple["manager_signal"], triple["fundamental_signal"], triple["valuation_signal"],
                triple["triple_confirm_status"], json.dumps(triple["exclusion_flags"], ensure_ascii=False),
                "；".join(triple["notes"]), "OFFICIAL_FUND_REPORT", now_iso(),
            ),
        )
        DB.execute("INSERT OR IGNORE INTO watchlist(owner_id,symbol,name) VALUES(?,?,?)", (SYSTEM_OWNER_ID, symbol, name))
        stock_count += 1
    return {"consensus": consensus_count, "stocks": stock_count}


def industry_chain_for(item: dict[str, Any]) -> dict[str, Any]:
    text = f"{item.get('industry','')} {item.get('name','')}"
    templates = [
        ("半导体", ["半导体", "芯片", "集成电路", "封测", "硅", "设备", "材料"], ["硅片/特气/靶材/光刻胶/设备零部件", "设计/制造/封测/设备/材料", "消费电子/汽车电子/AI算力/工业控制"]),
        ("新能源车", ["锂", "电池", "新能源", "电解液", "正极", "负极"], ["锂矿/镍钴锰/隔膜/电解液", "电芯/电池包/热管理/电驱", "整车/储能/充换电"]),
        ("医药", ["医药", "生物", "创新药", "医疗"], ["靶点/原料药/CRO/CDMO", "药品/器械/诊断/服务", "医院/药店/医保/海外市场"]),
        ("显示电子", ["显示", "面板", "OLED", "京东方"], ["玻璃基板/偏光片/驱动IC/材料", "LCD/OLED/Mini LED面板", "手机/电视/车载/工控显示"]),
        ("金融地产", ["银行", "证券", "保险", "地产"], ["资金成本/资本约束/资产质量", "信贷/投行/资管/保险承保", "企业融资/居民财富/实体需求"]),
    ]
    for name, keys, nodes in templates:
        if any(k in text for k in keys):
            return {
                "chain": name,
                "upstream": nodes[0],
                "midstream": nodes[1],
                "downstream": nodes[2],
                "catalysts": ["景气度改善", "订单/价格/库存边际变化", "政策或技术周期催化"],
                "risks": ["估值透支", "需求低于预期", "竞争格局恶化", "公开季报披露滞后"],
                "checks": ["核验公告与财报", "比较同行估值和利润弹性", "跟踪成交量、机构持仓延续性和产业数据"],
            }
    return {
        "chain": item.get("industry") or "待分类产业链",
        "upstream": "原材料/核心资源/上游设备",
        "midstream": "制造、服务或平台环节",
        "downstream": "终端客户、渠道和应用场景",
        "catalysts": ["行业景气度变化", "业绩或订单验证", "政策与事件催化"],
        "risks": ["数据覆盖不足", "估值与盈利不匹配", "基金季报披露滞后"],
        "checks": ["补充行业研究和公司公告", "核验财务质量", "观察价格行为和资金延续性"],
    }


def trade_plan_for_stock(item: dict[str, Any]) -> dict[str, Any]:
    symbol = normalize_symbol(item.get("symbol", ""))
    quote = DB.one("SELECT * FROM quote_validation WHERE symbol=?", (symbol,)) or {}
    price = quote.get("last_price")
    level = quote.get("level", "BLOCK")
    total = float(item.get("total_score") or 0)
    risk = float(item.get("risk") or 70)
    source = item.get("source_status") or ""
    triple_status = item.get("triple_confirm_status") or "UNVERIFIED"
    flags = item.get("exclusion_flags") or "[]"
    if isinstance(flags, str):
        try:
            flags = json.loads(flags)
        except Exception:
            flags = [flags] if flags else []
    if triple_status == "EXCLUDED":
        return {
            "symbol": symbol,
            "action": "排除",
            "entry": "触发排除项：" + "；".join(flags),
            "stop": None,
            "take_profit": None,
            "max_weight": 0,
            "tranche": "0",
            "reason": "不满足三重确认",
        }
    if not price or level == "BLOCK":
        return {
            "symbol": symbol,
            "action": "等待",
            "entry": "缺少有效行情或行情被拦截，不能给出执行区间",
            "stop": None,
            "take_profit": None,
            "max_weight": 0,
            "tranche": "0",
            "reason": "行情验证未通过",
        }
    if source == "OFFICIAL_FUND_REPORT" and triple_status != "TRIPLE_CONFIRMED":
        return {
            "symbol": symbol,
            "action": "观察",
            "entry": "仅基金季报线索，需补基本面、估值和价格回撤证据；不追季报披露后的热门股",
            "stop": None,
            "take_profit": None,
            "max_weight": 0.02 if triple_status == "RESEARCH_ONLY_NEEDS_FUNDAMENTAL_VALUATION" else 0,
            "tranche": "观察组合，不执行正式买入；初学者单只股票不超过总资金2%-5%",
            "reason": item.get("scoring_notes") or "三重确认未完成",
        }
    base_weight = 0.05 if source == "OFFICIAL_FUND_REPORT" else 0.02
    score_adj = _clamp((total - 60) / 40, 0, 1)
    risk_adj = _clamp((85 - risk) / 55, 0.25, 1)
    quote_adj = 0.6 if level == "WARN" else 1.0
    max_weight = round(base_weight * (0.45 + 0.55 * score_adj) * risk_adj * quote_adj, 4)
    pullback_low = price * 0.97
    pullback_high = price * 0.99
    breakout = price * 1.03
    stop = price * (0.92 if risk < 55 else 0.90)
    take1 = price * 1.12
    take2 = price * 1.20
    action = "重点跟踪" if total >= 72 else "观察"
    if source == "OFFICIAL_FUND_REPORT" and total >= 82 and level == "OK" and triple_status == "TRIPLE_CONFIRMED":
        action = "优先尽调"
    return {
        "symbol": symbol,
        "action": action,
        "entry": f"回撤观察区 {pullback_low:.2f}-{pullback_high:.2f}；放量突破观察价 {breakout:.2f}",
        "stop": round(stop, 2),
        "take_profit": f"{take1:.2f} / {take2:.2f}",
        "max_weight": max_weight,
        "tranche": "1/3试探 + 1/3确认 + 1/3回踩，不追高一次买满",
        "reason": "基于基金共识/市场信号、行情验证和风险分的规则化交易计划",
    }


def fund_research_data(limit: int = 50) -> dict[str, Any]:
    managers = DB.all("SELECT * FROM fund_managers ORDER BY score DESC LIMIT 100")
    products = DB.all("SELECT * FROM fund_products ORDER BY company,name LIMIT 200")
    reports = DB.all("SELECT * FROM fund_reports ORDER BY report_period DESC, announcement_date DESC LIMIT 200")
    holdings = DB.all(
        """SELECT h.*, r.report_period, r.fund_code, r.fund_name, r.manager_name, r.company
           FROM fund_report_holdings h JOIN fund_reports r ON r.report_id=h.report_id
           ORDER BY r.report_period DESC, h.holding_rank LIMIT 500"""
    )
    stocks = DB.all(
        """SELECT * FROM stock_scores WHERE source_status='OFFICIAL_FUND_REPORT'
           ORDER BY CASE triple_confirm_status
                        WHEN 'TRIPLE_CONFIRMED' THEN 0
                        WHEN 'RESEARCH_ONLY_NEEDS_FUNDAMENTAL_VALUATION' THEN 1
                        WHEN 'WATCH_ONLY' THEN 2
                        WHEN 'EXCLUDED' THEN 4
                        ELSE 3 END,
                    COALESCE(valuation_signal, 0) DESC,
                    COALESCE(total_score, 0) DESC
           LIMIT ?""",
        (limit,),
    )
    valuation_map: dict[str, dict[str, Any]] = {}
    if stocks:
        placeholders = ",".join("?" for _ in stocks)
        valuation_map = {
            row["symbol"]: row
            for row in DB.all(
                f"SELECT * FROM stock_valuations WHERE symbol IN ({placeholders})",
                tuple(stock["symbol"] for stock in stocks),
            )
        }
    enriched = []
    for stock in stocks:
        enriched.append({**stock, "valuation_detail": valuation_map.get(stock["symbol"], {}), "trade_plan": trade_plan_for_stock(stock), "industry_chain": industry_chain_for(stock)})
    if reports and not holdings:
        message = "已导入官方基金季报元数据，但尚未解析出前十大持仓；请查看解析状态或安装 PDF 解析依赖。"
    elif reports:
        message = "已基于官方披露数据生成研究结果。"
    else:
        message = "尚未导入官方基金季报；请输入基金代码后同步，市场热度不能替代基金经理持仓证据。"
    return {
        "status": "READY" if reports or managers else "NO_OFFICIAL_FUND_DATA",
        "report_period": latest_fund_report_period(),
        "managers": managers,
        "products": products,
        "reports": reports,
        "holdings": holdings,
        "stocks": enriched,
        "message": message,
    }


def fund_research_report_markdown() -> str:
    data = fund_research_data(limit=30)
    lines = [
        f"# 基金经理与季度持仓研究报告（{datetime.now(CN_TZ).date()}）",
        "",
        "## 结论边界",
        "- 本报告只基于已入库的官方披露或结构化导入数据；公开季报持仓存在披露滞后。",
        "- 买卖点为规则化交易计划，不构成个性化投资建议。",
        "- 未导入官方季报时，不输出基金经理正式排名。",
        "",
        f"## 数据状态",
        f"- 最新报告期：{data.get('report_period') or '无'}",
        f"- 基金经理：{len(data['managers'])} 位；基金产品：{len(data['products'])} 只；报告：{len(data['reports'])} 份；持仓明细：{len(data['holdings'])} 条",
        "",
        "## 高分基金经理",
    ]
    for manager in data["managers"][:10]:
        lines.append(f"- {manager['name']}｜{manager['company']}｜得分 {manager['score']}｜任期 {manager.get('tenure_years') or 'NA'} 年｜证据 {manager.get('evidence_status')}")
    if not data["managers"]:
        lines.append("- 暂无官方经理数据。")
    lines += ["", "## 多基金新增/增持股票排名"]
    for stock in data["stocks"][:15]:
        plan = stock["trade_plan"]
        chain = stock["industry_chain"]
        lines.append(f"- {stock['symbol']} {stock.get('name') or ''}｜总分 {stock.get('total_score')}｜基金信号 {stock.get('fund_signal')}｜{stock.get('grade')}｜{plan['action']}｜仓位上限 {plan['max_weight']:.1%}")
        lines.append(f"  - 买卖计划：{plan['entry']}；止损 {plan['stop'] or 'NA'}；止盈 {plan['take_profit'] or 'NA'}。")
        lines.append(f"  - 产业链：{chain['chain']}；上游 {chain['upstream']}；中游 {chain['midstream']}；下游 {chain['downstream']}。")
    if not data["stocks"]:
        lines.append("- 暂无股票评分。")
    lines += ["", "## 风险提示", "- 需要继续核验公告、估值、财务质量、成交结构和基金经理实际任期归因。"]
    return "\n".join(lines)


def sync_official_fund_reports(options: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = fund_report_sync_config()
    options = options or {}
    manual = bool(options)
    if not cfg.get("enabled") and not manual:
        update_source_health("fund_report_sync", "DISABLED", 0, "fund_report_sync.enabled=false", success=False)
        return {"ok": False, "status": "DISABLED", "imported": {}}
    sources = options.get("structured_json_sources") or cfg.get("structured_json_sources") or []
    codes = fund_code_list(options.get("fund_codes") or options.get("codes") or cfg.get("fund_codes"))
    report_years = year_list(options.get("report_years") or cfg.get("report_years"))
    total = {"managers": 0, "products": 0, "reports": 0, "holdings": 0, "consensus": 0, "stocks": 0}
    outcomes = []
    timeout = float(config().get("news_timeout_seconds", 8))
    retries = int(config().get("news_retry_count", 2))
    discover_latest = bool(options.get("discover_latest", cfg.get("discover_latest", False)))
    discover_limit = min(max(int(options.get("discover_latest_limit") or cfg.get("discover_latest_limit", 20)), 1), 80)
    if discover_latest:
        discovered, discover_err, discover_latency = provider_discover_latest_quarterly_fund_codes(
            limit=discover_limit,
            timeout=timeout,
            retries=retries,
            min_interval=float(cfg.get("official_min_interval", 0.6)),
        )
        for code in discovered:
            if code not in codes:
                codes.append(code)
        update_source_health("fund:eid.csrc.gov.cn:latest", "OK" if discovered else "NO_DATA", discover_latency, discover_err, success=bool(discovered))
        outcomes.append({"name": "fund:eid.csrc.gov.cn:latest", "status": "OK" if discovered else "NO_DATA", "fund_codes": discovered, "error": discover_err})
    if not sources and not codes:
        update_source_health("fund_report_sync", "CONFIG_REQUIRED", 0, "请配置官方解析后的 structured_json_sources", success=False)
        return {"ok": False, "status": "CONFIG_REQUIRED", "message": "请输入基金代码、勾选官方最新季报发现，或配置官方结构化 JSON 源；不会从非官方页面推测季报持仓。", "imported": {}}

    if codes:
        try:
            payload, eid_outcomes, err, latency = provider_fetch_official_fund_reports(
                codes,
                max_reports_per_fund=int(options.get("max_reports_per_fund") or cfg.get("max_reports_per_fund", 4)),
                report_years=report_years,
                parse_pdf=bool(options.get("parse_pdf", cfg.get("parse_pdf", True))),
                timeout=timeout,
                retries=retries,
                min_interval=float(cfg.get("official_min_interval", 0.6)),
            )
            imported = import_official_fund_payload(payload)
            for key, value in imported.items():
                total[key] = total.get(key, 0) + int(value)
            status = "OK" if imported.get("reports") else "NO_DATA"
            if imported.get("reports") and not imported.get("holdings"):
                status = "METADATA_ONLY"
            update_source_health("fund:eid.csrc.gov.cn", status, latency, err, success=bool(imported.get("reports")))
            outcomes.append({"name": "fund:eid.csrc.gov.cn", "status": status, "fund_codes": codes, "imported": imported, "reports": eid_outcomes, "error": err})
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            update_source_health("fund:eid.csrc.gov.cn", "PROVIDER_UNAVAILABLE", 0, err, success=False)
            outcomes.append({"name": "fund:eid.csrc.gov.cn", "status": "PROVIDER_UNAVAILABLE", "fund_codes": codes, "error": err})

    for idx, entry in enumerate(sources):
        meta = source_config(entry, f"fund_json_{idx + 1}", "A")
        url = str(meta.get("url") or "").strip()
        if not url:
            outcomes.append({"name": meta.get("name"), "status": "CONFIG_ERROR"})
            continue
        text, err, latency = fetch_text_with_retries(url, timeout, retries)
        name = f"fund:{meta.get('name') or url}"
        if err or text is None:
            update_source_health(name, "PROVIDER_UNAVAILABLE", latency, err, success=False)
            outcomes.append({"name": name, "status": "PROVIDER_UNAVAILABLE", "error": err})
            continue
        try:
            imported = import_official_fund_payload(json.loads(text))
            for key, value in imported.items():
                total[key] = total.get(key, 0) + int(value)
            update_source_health(name, "OK", latency, None, success=True)
            outcomes.append({"name": name, "status": "OK", "imported": imported})
        except Exception as exc:
            update_source_health(name, "SCHEMA_CHANGED", latency, f"{type(exc).__name__}: {exc}", success=False)
            outcomes.append({"name": name, "status": "SCHEMA_CHANGED", "error": f"{type(exc).__name__}: {exc}"})
    if total.get("reports") and not total.get("holdings"):
        final_status = "METADATA_ONLY"
        final_error = "已导入官方报告元数据，但未解析出持仓明细；可能缺少 PDF 解析依赖或 PDF 表格不可抽取。"
    elif any(total.values()):
        final_status = "OK"
        final_error = None
    else:
        final_status = "NO_DATA"
        final_error = "未导入有效基金披露数据"
    valuation_result = None
    if total.get("holdings") and a_stock_data_config().get("valuation_enabled", True):
        valuation_result = refresh_stock_valuations()
    update_source_health("fund_report_sync", final_status, 0, final_error, success=any(total.values()))
    return {"ok": any(total.values()), "status": final_status, "message": final_error, "imported": total, "valuation": valuation_result, "sources": outcomes}


def parse_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
        if isinstance(parsed, list):
            return [str(x) for x in parsed if str(x).strip()]
    except Exception:
        pass
    return [str(value)] if str(value).strip() else []


def position_action_for(pos: dict[str, Any], profile: dict[str, float]) -> dict[str, Any]:
    asset_type = pos.get("asset_type") or "stock"
    if asset_type != "stock":
        current = pos.get("current_price")
        cost = float(pos.get("average_cost") or 0)
        pnl_pct = pos.get("pnl_pct")
        label = ASSET_TYPE_LABELS.get(asset_type, asset_type)
        if current and cost and pnl_pct is not None and pnl_pct <= -0.15:
            return {"action": "持有复核", "diagnosis": f"{label}回撤较大", "severity": "WARN", "message": "非股票资产使用截图价或手动价估算，先核对产品规则、流动性和赎回成本", "reason": "回撤较大，不直接按股票止损逻辑处理"}
        return {"action": "持有跟踪", "diagnosis": f"{label}资产", "severity": "INFO", "message": "已导入到组合统计；买卖判断需结合产品说明书、赎回费、到期日和流动性", "reason": "非股票资产不套用个股三重确认模型"}
    symbol = normalize_symbol(pos.get("symbol"))
    current = pos.get("current_price")
    cost = float(pos.get("average_cost") or 0)
    stop = pos.get("stop_price")
    pnl_pct = pos.get("pnl_pct")
    weight = pos.get("portfolio_weight") or 0
    quote_level = pos.get("quote_level") or "BLOCK"
    score = DB.one("SELECT * FROM stock_scores WHERE symbol=?", (symbol,)) or {}
    due = DB.one("SELECT * FROM stock_due_diligence WHERE symbol=?", (symbol,)) or {}
    valuation = DB.one("SELECT * FROM stock_valuations WHERE symbol=?", (symbol,)) or {}
    risk_news = DB.all(
        """SELECT title,verification,risk_score FROM news_events
           WHERE symbols LIKE ? AND (verification='CONTRADICTED' OR risk_score>=70)
           ORDER BY published_at DESC LIMIT 3""",
        (f"%{symbol}%",),
    )
    flags = parse_json_list(score.get("exclusion_flags"))
    for key, label in [
        ("earnings_decline", "利润连续下滑或盈利趋势破坏"),
        ("receivable_inventory_goodwill_risk", "应收账款/存货/商誉异常"),
        ("governance_risk", "管理层减持或治理风险"),
        ("industry_decline_risk", "行业长期衰退风险"),
    ]:
        if int(due.get(key) or 0) and label not in flags:
            flags.append(label)
    if risk_news and "高风险新闻待核验" not in flags:
        flags.append("高风险新闻待核验")
    fundamental_break = bool(flags) or score.get("triple_confirm_status") == "EXCLUDED"
    low_valuation = valuation.get("valuation_percentile") is not None and float(valuation.get("valuation_percentile") or 100) <= float(a_stock_data_config().get("undervalued_percentile", 35))
    high_score = float(score.get("total_score") or pos.get("score") or 0) >= 68 or float(score.get("valuation_signal") or 0) >= 75
    below_stop = bool(current and stop and float(current) <= float(stop))
    near_stop = bool(current and stop and float(current) <= float(stop) * 1.03)

    if quote_level == "BLOCK":
        return {"action": "等待行情", "diagnosis": "行情失效", "severity": "HIGH", "message": "行情未通过验证，右侧不提示加仓", "reason": "缺少可靠实时价格"}
    if fundamental_break:
        return {"action": "暂停加仓/止损复核", "diagnosis": "基本盘风险", "severity": "CRITICAL" if below_stop else "HIGH", "message": "触发基本面、治理、行业或新闻风险", "reason": "；".join(flags)}
    if below_stop:
        return {"action": "止损卖出复核", "diagnosis": "价格破位", "severity": "CRITICAL", "message": "跌破止损线，先按纪律处理，不按洗盘解释", "reason": f"现价{float(current):.2f} <= 止损{float(stop):.2f}"}
    if near_stop:
        return {"action": "不加仓", "diagnosis": "临近止损", "severity": "WARN", "message": "价格接近止损线，等待放量修复或减仓纪律", "reason": "离止损线过近"}
    if high_score and low_valuation and weight < profile["stock_max"] * 0.8 and (pnl_pct is None or pnl_pct > -0.08):
        return {"action": "右侧加仓观察", "diagnosis": "低估值+未破位", "severity": "INFO", "message": "可按小仓位分批观察，仍需财报和成交确认", "reason": "评分和估值条件较好，未触发基本盘风险"}
    if low_valuation and pnl_pct is not None and pnl_pct < 0:
        return {"action": "持有观察", "diagnosis": "震荡洗盘观察", "severity": "INFO", "message": "未见基本盘破裂，价格仍在止损线上方", "reason": "低估值回撤但未触发硬风险"}
    return {"action": "持有/不加仓", "diagnosis": "常规跟踪", "severity": "INFO", "message": "未达到加仓或止损条件", "reason": "等待新信号"}


def build_position_candidates(limit: int = 8, user_id: str = LEGACY_OWNER_ID) -> list[dict[str, Any]]:
    held = {
        normalize_symbol(x["symbol"])
        for x in DB.all("SELECT symbol FROM positions WHERE owner_id=? AND COALESCE(asset_type,'stock')='stock'", (user_id,))
    }
    rows = DB.all(
        """SELECT s.*, v.pe_ttm, v.pb, v.valuation_percentile, v.price_drawdown_pct
           FROM stock_scores s LEFT JOIN stock_valuations v ON v.symbol=s.symbol
           WHERE s.source_status='OFFICIAL_FUND_REPORT'
           ORDER BY CASE s.triple_confirm_status
                        WHEN 'TRIPLE_CONFIRMED' THEN 0
                        WHEN 'RESEARCH_ONLY_NEEDS_FUNDAMENTAL_VALUATION' THEN 1
                        WHEN 'WATCH_ONLY' THEN 2
                        ELSE 3 END,
                    COALESCE(v.valuation_percentile, 100) ASC,
                    COALESCE(s.total_score, 0) DESC
           LIMIT 80"""
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        symbol = normalize_symbol(row.get("symbol"))
        if symbol in held or row.get("triple_confirm_status") == "EXCLUDED":
            continue
        valuation_percentile = row.get("valuation_percentile")
        if valuation_percentile is None or float(valuation_percentile) > float(a_stock_data_config().get("undervalued_percentile", 35)):
            continue
        total = float(row.get("total_score") or 0)
        if total < 58:
            continue
        status = row.get("triple_confirm_status")
        if status == "TRIPLE_CONFIRMED":
            action = "试探建仓候选"
            severity = "HIGH"
        elif status == "RESEARCH_ONLY_NEEDS_FUNDAMENTAL_VALUATION":
            action = "观察建仓候选"
            severity = "INFO"
        else:
            action = "低估值观察"
            severity = "INFO"
        out.append({
            "symbol": symbol,
            "name": row.get("name") or "",
            "total_score": total,
            "valuation_percentile": valuation_percentile,
            "pe_ttm": row.get("pe_ttm"),
            "pb": row.get("pb"),
            "price_drawdown_pct": row.get("price_drawdown_pct"),
            "triple_confirm_status": status,
            "action": action,
            "severity": severity,
            "reason": "根据已有结论推断出；高分低估值只能进入观察或试探建仓候选，仍需基本面和成交确认。",
        })
        if len(out) >= limit:
            break
    return out


def portfolio(user_id: str = LEGACY_OWNER_ID) -> dict[str, Any]:
    s = DB.settings(user_id)
    total_capital = float(s.get("total_capital", 100000))
    positions = DB.all("SELECT * FROM positions WHERE owner_id=? ORDER BY symbol", (user_id,))
    result = []
    market_value = 0.0
    cost_value = 0.0
    themes: dict[str, float] = {}
    for p in positions:
        v = DB.one("SELECT * FROM quote_validation WHERE symbol=?", (p["symbol"],)) or {}
        asset_type = p.get("asset_type") or "stock"
        stored_current = p.get("current_price")
        quote_current = v.get("last_price")
        if quote_current:
            current = quote_current
            level = v.get("level", "BLOCK")
        elif stored_current:
            current = stored_current
            level = "SCREENSHOT" if asset_type != "stock" else "SNAPSHOT"
        else:
            current = None
            level = "BLOCK" if asset_type == "stock" else "MANUAL_NEEDED"
        qty = float(p["quantity"])
        cost = float(p["average_cost"])
        mv = current * qty if current else None
        cv = cost * qty
        pnl = mv - cv if mv is not None else None
        pnl_pct = pnl/cv if pnl is not None and cv else None
        weight = mv/total_capital if mv is not None and total_capital else None
        market_value += mv or 0
        cost_value += cv
        themes[p["theme"]] = themes.get(p["theme"], 0) + (mv or 0)
        item = {**p, "current_price": current, "market_value": mv, "cost_value": cv,
                "unrealized_pnl": pnl, "pnl_pct": pnl_pct, "portfolio_weight": weight,
                "quote_level": level, "asset_type_label": ASSET_TYPE_LABELS.get(asset_type, asset_type)}
        item["position_action"] = position_action_for(item, RISK_PROFILES.get(s.get("risk_profile"), RISK_PROFILES["balanced"]))
        result.append(item)
    return {
        "settings": s, "positions": result, "position_count": len(result),
        "total_market_value": market_value, "total_cost_value": cost_value,
        "unrealized_pnl": market_value-cost_value if result else 0,
        "invested_weight": market_value/total_capital if total_capital else 0,
        "estimated_cash": max(total_capital-market_value, 0),
        "theme_exposure": {k: v/total_capital for k, v in themes.items()} if total_capital else {}
    }


RISK_PROFILES = {
    "conservative": {"trade_risk": 0.0035, "portfolio_max": 0.45, "stock_max": 0.06, "theme_max": 0.08},
    "balanced": {"trade_risk": 0.0060, "portfolio_max": 0.60, "stock_max": 0.10, "theme_max": 0.12},
    "aggressive": {"trade_risk": 0.0090, "portfolio_max": 0.72, "stock_max": 0.14, "theme_max": 0.16},
}


def position_size(payload: dict[str, Any]) -> dict[str, Any]:
    total = float(payload.get("total_capital") or 0)
    invested = float(payload.get("current_invested") or 0)
    theme = float(payload.get("current_theme_exposure") or 0)
    stock = float(payload.get("current_stock_exposure") or 0)
    entry = float(payload.get("entry_price") or 0)
    stop = float(payload.get("stop_price") or 0)
    score = float(payload.get("score") or 0)
    lot = int(payload.get("lot_size") or 100)
    profile = RISK_PROFILES.get(payload.get("risk_profile"), RISK_PROFILES["balanced"])
    quote_validation = payload.get("quote_validation", "BLOCK")
    news_verification = payload.get("news_verification", "UNVERIFIED")
    is_new_theme = bool(payload.get("is_new_theme", False))

    blocks = []
    if total <= 0 or entry <= 0 or stop <= 0 or stop >= entry:
        blocks.append("资金或入场/止损参数无效")
    if quote_validation != "OK":
        blocks.append("行情未通过双源验证")
    if is_new_theme and news_verification not in {"CONFIRMED", "HIGH_CONFIDENCE"}:
        blocks.append("新题材新闻证据未确认")
    if score < 60:
        blocks.append("综合得分低于60")
    if blocks:
        return {"allowed": False, "reason": "；".join(blocks), "executable_amount": 0, "executable_shares": 0,
                "risk_budget": total*profile["trade_risk"], "target_weight": 0, "tranche_amounts": [0,0,0]}

    risk_budget = total*profile["trade_risk"]
    risk_per_share = entry-stop
    risk_shares = math.floor((risk_budget/risk_per_share)/lot)*lot
    score_factor = min(max((score-50)/40, 0.25), 1.0)
    stock_cap = max(total*profile["stock_max"]-stock, 0)
    theme_cap = max(total*profile["theme_max"]-theme, 0)
    portfolio_cap = max(total*profile["portfolio_max"]-invested, 0)
    max_amount = min(risk_shares*entry, stock_cap, theme_cap, portfolio_cap)*score_factor
    shares = max(math.floor((max_amount/entry)/lot)*lot, 0)
    amount = shares*entry
    allowed = shares > 0
    return {"allowed": allowed, "reason": "通过风险预算、单股、题材和组合上限" if allowed else "剩余风险预算不足",
            "executable_amount": round(amount,2), "executable_shares": shares, "risk_budget": round(risk_budget,2),
            "target_weight": amount/total if total else 0,
            "tranche_amounts": [round(amount*0.3,2),round(amount*0.3,2),round(amount*0.4,2)]}


def verification_for_event(source_level: str, independent_sources: int, contradicted: bool = False) -> str:
    if contradicted:
        return "CONTRADICTED"
    if source_level == "A":
        return "CONFIRMED"
    if source_level == "B" and independent_sources >= 2:
        return "HIGH_CONFIDENCE"
    if source_level in {"A", "B"}:
        return "PENDING"
    return "UNVERIFIED"


def alert_owner_ids() -> list[str]:
    ids = {row["user_id"] for row in DB.all("SELECT user_id FROM users")}
    ids |= {row["owner_id"] for row in DB.all("SELECT DISTINCT owner_id FROM positions") if row.get("owner_id") and row.get("owner_id") != SYSTEM_OWNER_ID}
    ids |= {row["owner_id"] for row in DB.all("SELECT DISTINCT owner_id FROM watchlist") if row.get("owner_id") and row.get("owner_id") != SYSTEM_OWNER_ID}
    return sorted(ids or {LEGACY_OWNER_ID})


def rebuild_alerts(user_id: str | None = None):
    if user_id is None:
        for owner_id in alert_owner_ids():
            rebuild_alerts(owner_id)
        return
    DB.execute("DELETE FROM alerts WHERE owner_id=? AND acknowledged=0", (user_id,))
    p = portfolio(user_id)
    settings = p["settings"]
    profile = RISK_PROFILES.get(settings.get("risk_profile"), RISK_PROFILES["balanced"])
    alerts = []
    if p["invested_weight"] > profile["portfolio_max"]:
        alerts.append(("PORTFOLIO", "HIGH", None, "组合总仓位超限", f"当前仓位{p['invested_weight']:.1%}，超过{profile['portfolio_max']:.0%}上限", "暂停新增仓位并检查减仓顺序"))
    for pos in p["positions"]:
        action = pos.get("position_action") or {}
        if (pos.get("asset_type") or "stock") == "stock" and pos["quote_level"] == "BLOCK":
            alerts.append(("DATA", "HIGH", pos["symbol"], "行情验证失败", "该持仓没有通过双源行情验证", "暂停该股票新增操作"))
        if pos.get("current_price") and pos.get("stop_price") and pos["current_price"] <= pos["stop_price"]:
            alerts.append(("RISK", "CRITICAL", pos["symbol"], "触及止损线", f"现价{pos['current_price']:.2f}不高于止损价{pos['stop_price']:.2f}", "立即人工复核交易计划"))
        if (pos.get("asset_type") or "stock") == "stock" and (pos.get("portfolio_weight") or 0) > profile["stock_max"]:
            alerts.append(("RISK", "HIGH", pos["symbol"], "单股仓位超限", f"仓位{pos['portfolio_weight']:.1%}超过{profile['stock_max']:.0%}上限", "停止加仓，评估集中度"))
        if action.get("action") in {"右侧加仓观察", "止损卖出复核", "暂停加仓/止损复核", "不加仓"}:
            alerts.append((
                "POSITION_ACTION",
                action.get("severity", "INFO"),
                pos["symbol"],
                f"{action.get('action')}：{action.get('diagnosis')}",
                action.get("message") or "",
                action.get("reason") or "",
            ))
    for candidate in build_position_candidates(limit=5, user_id=user_id):
        alerts.append((
            "BUILD_POSITION",
            candidate.get("severity", "INFO"),
            candidate["symbol"],
            candidate["action"],
            f"{candidate['name']} 总分{candidate['total_score']:.1f}，估值分位{float(candidate['valuation_percentile']):.1f}%，回撤{float(candidate.get('price_drawdown_pct') or 0):.1f}%",
            candidate["reason"],
        ))
    events = DB.all("SELECT * FROM news_events ORDER BY published_at DESC LIMIT 100")
    held = {x["symbol"] for x in p["positions"]}
    watched = {x["symbol"] for x in DB.all("SELECT symbol FROM watchlist WHERE owner_id=?", (user_id,))}
    for e in events:
        symbols = set(json.loads(e["symbols"] or "[]"))
        relevant = symbols & (held | watched)
        if not relevant:
            continue
        if e["verification"] == "CONTRADICTED" or e["risk_score"] >= 70:
            for sym in relevant:
                alerts.append(("NEWS_RISK", "HIGH", sym, "新闻风险涉及持仓/观察股", e["title"], "打开原文并核验公告"))
        elif e["verification"] in {"CONFIRMED","HIGH_CONFIDENCE"} and e["opportunity_score"] >= float(settings.get("opportunity_min_score",70)) and e["risk_score"] <= float(settings.get("opportunity_max_risk",35)):
            for sym in relevant:
                alerts.append(("OPPORTUNITY", "INFO", sym, "已验证机会线索", e["title"], "进入研究池，等待量价确认"))
    rows=[]
    for cat, sev, sym, title, msg, action in alerts:
        raw=f"{user_id}|{cat}|{sev}|{sym}|{title}|{msg}"
        aid=hashlib.sha256(raw.encode()).hexdigest()[:16]
        rows.append((user_id,aid,cat,sev,sym,title,msg,action,now_iso(),0))
    if rows:
        DB.executemany("INSERT OR REPLACE INTO alerts(owner_id,alert_id,category,severity,symbol,title,message,action,created_at,acknowledged) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)


def selection_data(user_id: str = LEGACY_OWNER_ID) -> dict[str, Any]:
    managers = DB.all("SELECT * FROM fund_managers ORDER BY score DESC LIMIT 100")
    consensus = DB.all("SELECT * FROM fund_consensus ORDER BY consensus_score DESC LIMIT 200")
    scores = DB.all(
        """SELECT * FROM stock_scores
           ORDER BY CASE triple_confirm_status
                        WHEN 'TRIPLE_CONFIRMED' THEN 0
                        WHEN 'RESEARCH_ONLY_NEEDS_FUNDAMENTAL_VALUATION' THEN 1
                        WHEN 'WATCH_ONLY' THEN 2
                        WHEN 'EXCLUDED' THEN 4
                        ELSE 3 END,
                    COALESCE(valuation_signal, 0) DESC,
                    COALESCE(total_score, 0) DESC
           LIMIT 300"""
    )
    combined=[]
    cons={x["symbol"]:x for x in consensus}
    valuation_map: dict[str, dict[str, Any]] = {}
    if scores:
        placeholders = ",".join("?" for _ in scores)
        valuation_map = {
            row["symbol"]: row
            for row in DB.all(
                f"SELECT * FROM stock_valuations WHERE symbol IN ({placeholders})",
                tuple(score["symbol"] for score in scores),
            )
        }
    for s in scores:
        c=cons.get(s["symbol"],{})
        combined.append({**s,"manager_count":c.get("manager_count",0),"company_count":c.get("company_count",0),
                         "confirmed_increase":c.get("confirmed_increase",0),"new_visible":c.get("new_visible",0),
                         "consecutive_increase":c.get("consecutive_increase",0),
                         "excellent_manager_count":c.get("excellent_manager_count",0),
                         "style_count":c.get("style_count",0),
                         "valuation_detail":valuation_map.get(s["symbol"],{}),
                         "consensus_score":c.get("consensus_score"),"fund_report_period":c.get("report_period"),
                         "fund_evidence_status":c.get("evidence_status","UNVERIFIED")})
    if not combined:
        qrows = DB.all(
            """SELECT qv.*, w.symbol AS watched FROM quote_validation qv
               JOIN watchlist w ON w.symbol=qv.symbol AND w.owner_id=?
               ORDER BY CASE qv.level WHEN 'OK' THEN 1 WHEN 'WARN' THEN 2 ELSE 3 END, qv.symbol
               LIMIT 100""",
            (user_id,),
        )
        for q in qrows:
            combined.append({
                "symbol": q["symbol"], "name": q.get("name", ""), "industry": "观察池",
                "quality": None, "growth": None, "valuation": None, "trend": None,
                "risk": 45 if q.get("level") == "OK" else 65 if q.get("level") == "WARN" else 90,
                "fund_signal": 0, "total_score": None, "grade": "行情观察",
                "data_date": q.get("updated_at"), "source_status": "WATCHLIST_ONLY",
                "manager_count": 0, "company_count": 0, "confirmed_increase": 0,
                "new_visible": 0, "consensus_score": None, "fund_report_period": None,
                "fund_evidence_status": "NO_OFFICIAL_DATA",
            })
    official_scores = [x for x in scores if x.get("source_status") not in {"A_STOCK_DATA_SIGNAL"}]
    status="READY" if managers or consensus or official_scores else "NO_OFFICIAL_DATA"
    if status != "READY" and scores:
        status = "MARKET_SIGNAL_ONLY"
    elif status != "READY" and combined:
        status = "WATCHLIST_ONLY"
    messages = {
        "READY": "仅显示数据库中带来源状态的数据。",
        "MARKET_SIGNAL_ONLY": "已接入 a-stock-data 市场热度/资金线索；这不是官方基金经理持仓推荐，需继续核验估值、公告和成交结构。",
        "WATCHLIST_ONLY": "已显示观察池行情行，仅用于待研究列表；正式推荐需要导入官方基金季报/股票评分。",
        "NO_OFFICIAL_DATA": "尚未导入或同步官方基金季度报告；系统不会展示示例经理或虚构股票。"
    }
    return {"status":status,"managers":managers,"consensus":consensus,"stocks":combined,
            "message":messages.get(status, messages["NO_OFFICIAL_DATA"])}


def user_symbol_set(user_id: str) -> set[str]:
    symbols = {row["symbol"] for row in DB.all("SELECT symbol FROM watchlist WHERE owner_id=?", (user_id,))}
    symbols |= {row["symbol"] for row in DB.all("SELECT symbol FROM positions WHERE owner_id=?", (user_id,))}
    return symbols


def dashboard(user_id: str, user: dict[str, Any] | None = None) -> dict[str, Any]:
    symbols = sorted(user_symbol_set(user_id))
    if symbols:
        placeholders = ",".join("?" for _ in symbols)
        qrows = DB.all(f"SELECT * FROM quote_validation WHERE symbol IN ({placeholders}) ORDER BY symbol", tuple(symbols))
    else:
        qrows = []
    for q in qrows:
        q["reasons"] = json.loads(q.get("reasons") or "[]")
    events=DB.all("SELECT * FROM news_events ORDER BY published_at DESC LIMIT 100")
    for e in events:
        e["symbols"]=json.loads(e.get("symbols") or "[]")
        e["themes"]=json.loads(e.get("themes") or "[]")
    return {
        "auth": auth_status(user),
        "watchlist":[x["symbol"] for x in DB.all("SELECT symbol FROM watchlist WHERE owner_id=? ORDER BY symbol", (user_id,))],
        "quotes":qrows,
        "portfolio":portfolio(user_id),
        "selection":selection_data(user_id),
        "build_candidates": build_position_candidates(limit=8, user_id=user_id),
        "fund": fund_research_data(limit=30),
        "events":events,
        "alerts":DB.all("SELECT * FROM alerts WHERE owner_id=? AND acknowledged=0 ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'WARN' THEN 3 ELSE 4 END, created_at DESC", (user_id,)),
        "source_health":DB.all("SELECT * FROM source_health ORDER BY name"),
        "settings":DB.settings(user_id),
        "server_time":now_iso()
    }


def post_json(url: str, payload: dict[str, Any], headers: dict[str,str] | None = None, timeout: float = 8.0):
    body=json.dumps(payload,ensure_ascii=False).encode("utf-8")
    req=urllib.request.Request(url,data=body,headers={"Content-Type":"application/json",**(headers or {})},method="POST")
    with urllib.request.urlopen(req,timeout=timeout) as response:
        return response.status, response.read().decode("utf-8",errors="replace")


def send_notification(message: str, channel: str | None = None) -> dict[str, Any]:
    cfg=config()["notification"]
    outcomes=[]
    targets=[channel] if channel else ["wecom","dingtalk","feishu","telegram","generic"]
    for name in targets:
        try:
            if name=="wecom" and cfg.get("wecom_webhook"):
                status,_=post_json(cfg["wecom_webhook"],{"msgtype":"text","text":{"content":message}})
            elif name=="dingtalk" and cfg.get("dingtalk_webhook"):
                status,_=post_json(cfg["dingtalk_webhook"],{"msgtype":"text","text":{"content":message}})
            elif name=="feishu" and cfg.get("feishu_webhook"):
                status,_=post_json(cfg["feishu_webhook"],{"msg_type":"text","content":{"text":message}})
            elif name=="telegram" and cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"):
                url=f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage"
                status,_=post_json(url,{"chat_id":cfg["telegram_chat_id"],"text":message})
            elif name=="generic" and cfg.get("generic_webhook"):
                status,_=post_json(cfg["generic_webhook"],{"text":message,"timestamp":now_iso()})
            else:
                outcomes.append({"channel":name,"status":"NOT_CONFIGURED"}); continue
            outcomes.append({"channel":name,"status":"OK" if 200<=status<300 else f"HTTP_{status}"})
        except Exception as exc:
            outcomes.append({"channel":name,"status":"FAILED","error":f"{type(exc).__name__}: {exc}"})
    return {"results":outcomes}


class Handler(BaseHTTPRequestHandler):
    server_version = "AStockWebTerminal/4.0"

    def log_message(self, fmt, *args):
        print(f"[{now_iso()}] {self.address_string()} {fmt % args}")

    def send_bytes(self, data: bytes, content_type: str, status: int = 200, headers: dict[str, str] | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: Any, status: int = 200, headers: dict[str, str] | None = None):
        self.send_bytes(json.dumps(payload,ensure_ascii=False,default=str).encode("utf-8"),"application/json; charset=utf-8",status,headers)

    def cookie_token(self) -> str | None:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            if "=" not in part:
                continue
            key, value = part.strip().split("=", 1)
            if key == SESSION_COOKIE:
                return urllib.parse.unquote(value)
        return None

    def current_user(self) -> dict[str, Any] | None:
        return user_from_session_token(self.cookie_token())

    def require_user(self) -> dict[str, Any] | None:
        user = self.current_user()
        if not user:
            self.send_json({"error": "authentication required", "auth": auth_status(None)}, HTTPStatus.UNAUTHORIZED)
            return None
        return user

    def session_headers(self, token: str | None) -> dict[str, str]:
        if token:
            max_age = SESSION_DAYS * 86400
            cookie = f"{SESSION_COOKIE}={urllib.parse.quote(token)}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
        else:
            cookie = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        return {"Set-Cookie": cookie}

    def body_json(self) -> dict[str, Any]:
        length=int(self.headers.get("Content-Length","0") or 0)
        if length > 0:
            raw = self.rfile.read(length)
        elif (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            chunks = []
            while True:
                size_line = self.rfile.readline().strip()
                if not size_line:
                    break
                size = int(size_line.split(b";", 1)[0], 16)
                if size == 0:
                    self.rfile.readline()
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline()
            raw = b"".join(chunks)
        else:
            return {}
        if not raw.strip():
            return {}
        return json.loads(raw.decode("utf-8"))

    def serve_static(self, rel: str):
        path=(STATIC/rel).resolve()
        if not str(path).startswith(str(STATIC.resolve())) or not path.exists() or not path.is_file():
            self.send_json({"error":"not found"},404); return
        ext=path.suffix.lower()
        ctype={".html":"text/html; charset=utf-8",".css":"text/css; charset=utf-8",".js":"application/javascript; charset=utf-8",".json":"application/json; charset=utf-8",".svg":"image/svg+xml",".png":"image/png",".ico":"image/x-icon"}.get(ext,"application/octet-stream")
        self.send_bytes(path.read_bytes(),ctype)

    def do_GET(self):
        path=urllib.parse.urlparse(self.path).path
        if path=="/": return self.serve_static("index.html")
        if path.startswith("/static/"): return self.serve_static(path[len("/static/"):])
        if path=="/manifest.json": return self.serve_static("manifest.json")
        if path=="/service-worker.js": return self.serve_static("service-worker.js")
        if path=="/api/health": return self.send_json({"status":"OK","version":__version__,"time":now_iso()})
        if path=="/api/auth/status": return self.send_json(auth_status(self.current_user()))
        user = self.require_user()
        if not user:
            return
        user_id = user["user_id"]
        if path=="/api/dashboard": return self.send_json(dashboard(user_id, user))
        if path=="/api/selection": return self.send_json(selection_data(user_id))
        if path=="/api/fund/research": return self.send_json(fund_research_data(limit=100))
        if path=="/api/fund/research-report":
            return self.send_bytes(fund_research_report_markdown().encode("utf-8"),"text/markdown; charset=utf-8")
        if path=="/api/report":
            d=dashboard(user_id, user); p=d["portfolio"]; sel=d["selection"]
            lines=[f"# A股智能投研日报 ({datetime.now(CN_TZ).date()})","",f"- 组合仓位：{p['invested_weight']:.1%}",f"- 浮动盈亏：{p['unrealized_pnl']:.2f}",f"- 活动提醒：{len(d['alerts'])}",f"- 选股系统状态：{sel['status']}","","## 重点候选"]
            for s in sel["stocks"][:10]: lines.append(f"- {s['symbol']} {s.get('name','')}：总分 {s.get('total_score')}，基金共识 {s.get('consensus_score')}")
            return self.send_bytes("\n".join(lines).encode("utf-8"),"text/markdown; charset=utf-8")
        return self.send_json({"error":"not found"},404)

    def do_DELETE(self):
        path=urllib.parse.urlparse(self.path).path
        user = self.require_user()
        if not user:
            return
        user_id = user["user_id"]
        if path.startswith("/api/positions/"):
            raw_symbol = urllib.parse.unquote(path.rsplit("/",1)[-1])
            symbol = normalize_symbol(raw_symbol) if is_six_digit_symbol(raw_symbol) else raw_symbol
            DB.execute("DELETE FROM positions WHERE owner_id=? AND symbol=?",(user_id, symbol)); rebuild_alerts(user_id); return self.send_json({"ok":True})
        return self.send_json({"error":"not found"},404)

    def do_POST(self):
        path=urllib.parse.urlparse(self.path).path
        try: body=self.body_json()
        except Exception as exc: return self.send_json({"error":f"invalid json: {exc}"},400)
        try:
            if path=="/api/auth/register":
                user = create_user(body.get("username", ""), body.get("password", ""))
                token = create_session(user["user_id"])
                return self.send_json({"ok": True, "auth": auth_status(user)}, headers=self.session_headers(token))
            if path=="/api/auth/login":
                user = authenticate_user(body.get("username", ""), body.get("password", ""))
                if not user:
                    return self.send_json({"error": "用户名或密码错误"}, HTTPStatus.UNAUTHORIZED)
                token = create_session(user["user_id"])
                return self.send_json({"ok": True, "auth": auth_status(user)}, headers=self.session_headers(token))
            if path=="/api/auth/logout":
                token = self.cookie_token()
                if token:
                    DB.execute("DELETE FROM sessions WHERE token_hash=?", (hash_token(token),))
                return self.send_json({"ok": True, "auth": auth_status(None)}, headers=self.session_headers(None))
            user = self.require_user()
            if not user:
                return
            user_id = user["user_id"]
            if path=="/api/data/refresh": return self.send_json(refresh_all_sources())
            if path=="/api/refresh": return self.send_json(refresh_quotes())
            if path=="/api/watchlist":
                symbols=sorted({normalize_symbol(x) for x in body.get("symbols",[]) if normalize_symbol(x)!="000000"})
                DB.execute("DELETE FROM watchlist WHERE owner_id=?", (user_id,))
                if symbols: DB.executemany("INSERT INTO watchlist(owner_id,symbol) VALUES(?,?)",[(user_id, x) for x in symbols])
                return self.send_json({"ok":True,"symbols":symbols})
            if path=="/api/positions":
                asset_type = normalize_asset_type(body.get("asset_type"), body.get("symbol", ""), body.get("name", ""), body.get("theme", ""))
                symbol = normalize_position_symbol(body["symbol"], asset_type, str(body.get("name") or ""))
                upsert_position({**body, "symbol": symbol, "asset_type": asset_type}, user_id)
                if is_six_digit_symbol(symbol):
                    DB.execute("INSERT OR IGNORE INTO watchlist(owner_id,symbol,name) VALUES(?,?,?)", (user_id, symbol, body.get("name", "")))
                rebuild_alerts(user_id); return self.send_json({"ok":True,"symbol":symbol})
            if path=="/api/positions/backfill-metadata":
                return self.send_json(backfill_position_metadata(user_id))
            if path=="/api/positions/recognize":
                return self.send_json(recognize_positions(body, user_id))
            if path=="/api/settings": DB.set_settings(body, user_id); rebuild_alerts(user_id); return self.send_json({"ok":True,"settings":DB.settings(user_id)})
            if path=="/api/position-size": return self.send_json(position_size(body))
            if path=="/api/alerts/ack": DB.execute("UPDATE alerts SET acknowledged=1 WHERE owner_id=? AND alert_id=?",(user_id, body.get("alert_id"))); return self.send_json({"ok":True})
            if path=="/api/notifications/test": return self.send_json(send_notification(body.get("message","A股网页版通知测试"),body.get("channel")))
            if path=="/api/news/import":
                imported=import_news_items(body.get("items",[]))
                rebuild_alerts(); return self.send_json({"ok":True,"imported":imported})
            if path=="/api/news/refresh":
                return self.send_json(refresh_news_sources())
            if path=="/api/fund/import-official":
                imported = import_official_fund_payload(body)
                return self.send_json({"ok": any(imported.values()), "imported": imported, "fund": fund_research_data(limit=50)})
            if path=="/api/fund/sync":
                return self.send_json(sync_official_fund_reports(body))
            if path=="/api/stocks/valuation/refresh":
                symbols = body.get("symbols") or body.get("codes") or None
                if isinstance(symbols, str):
                    symbols = symbols.split(",")
                return self.send_json(refresh_stock_valuations(symbols))
            if path=="/api/stocks/due-diligence/import":
                imported = import_stock_due_diligence_items(body.get("items", []))
                return self.send_json({"ok": bool(imported), "imported": imported, "selection": selection_data(), "fund": fund_research_data(limit=50)})
            if path=="/api/selection/refresh":
                result = refresh_market_signals()
                quote_result = refresh_quotes() if result.get("ok") else None
                return self.send_json({"ok": result.get("ok", False), "selection": result, "quotes": quote_result})
            if path=="/api/selection/import":
                imported={"managers":0,"consensus":0,"stocks":0}
                for x in body.get("managers",[]):
                    mid=x.get("manager_id") or hashlib.sha256((x["name"]+x["company"]).encode()).hexdigest()[:16]
                    DB.execute("INSERT OR REPLACE INTO fund_managers(manager_id,name,company,score,tenure_years,report_period,source_url,evidence_status,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(mid,x["name"],x["company"],float(x["score"]),x.get("tenure_years"),x.get("report_period"),x.get("source_url"),x.get("evidence_status","VERIFIED"),now_iso())); imported["managers"]+=1
                for x in body.get("consensus",[]):
                    sym=normalize_symbol(x["symbol"])
                    DB.execute("INSERT OR REPLACE INTO fund_consensus(symbol,name,report_period,manager_count,company_count,confirmed_increase,new_visible,consensus_score,source_url,evidence_status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(sym,x.get("name",""),x.get("report_period"),int(x.get("manager_count",0)),int(x.get("company_count",0)),int(x.get("confirmed_increase",0)),int(x.get("new_visible",0)),float(x.get("consensus_score",0)),x.get("source_url"),x.get("evidence_status","VERIFIED"),now_iso())); imported["consensus"]+=1
                for x in body.get("stocks",[]):
                    sym=normalize_symbol(x["symbol"]); total=float(x.get("total_score",0)); grade=x.get("grade") or ("A-强" if total>=80 else "B-较强" if total>=70 else "C-中性" if total>=60 else "D-偏弱" if total>=50 else "E-弱")
                    DB.execute("INSERT OR REPLACE INTO stock_scores(symbol,name,industry,quality,growth,valuation,trend,risk,fund_signal,total_score,grade,data_date,source_status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(sym,x.get("name",""),x.get("industry",""),x.get("quality"),x.get("growth"),x.get("valuation"),x.get("trend"),x.get("risk"),x.get("fund_signal"),total,grade,x.get("data_date"),x.get("source_status","VERIFIED"),now_iso())); imported["stocks"]+=1
                return self.send_json({"ok":True,"imported":imported})
            if path=="/api/fund-sync":
                return self.send_json(sync_official_fund_reports(body))
            return self.send_json({"error":"not found"},404)
        except ValueError as exc: return self.send_json({"error":str(exc)},400)
        except KeyError as exc: return self.send_json({"error":f"missing field: {exc}"},400)
        except Exception as exc: return self.send_json({"error":f"{type(exc).__name__}: {exc}"},500)


_BACKGROUND_STARTED = False
_BACKGROUND_LOCK = threading.Lock()


def start_background_refresh() -> None:
    global _BACKGROUND_STARTED
    with _BACKGROUND_LOCK:
        if _BACKGROUND_STARTED:
            return
        _BACKGROUND_STARTED = True

    def worker() -> None:
        last_quotes = 0.0
        last_news = 0.0
        last_signals = 0.0
        last_funds = 0.0
        last_valuation = 0.0
        while True:
            try:
                cfg = config()
                astock_cfg = a_stock_data_config()
                fund_cfg = fund_report_sync_config()
                if not astock_cfg.get("background_refresh", True):
                    time.sleep(5)
                    continue
                now = time.monotonic()
                if now - last_quotes >= max(3, int(cfg.get("refresh_seconds", 10))):
                    refresh_quotes()
                    last_quotes = now
                if astock_cfg.get("enabled") and astock_cfg.get("signal_enabled") and now - last_signals >= max(60, int(astock_cfg.get("signal_refresh_seconds", 300))):
                    refresh_market_signals()
                    last_signals = now
                if astock_cfg.get("enabled") and astock_cfg.get("valuation_enabled", True) and now - last_valuation >= max(900, int(astock_cfg.get("valuation_refresh_seconds", 3600))):
                    refresh_stock_valuations()
                    last_valuation = now
                if astock_cfg.get("enabled") and astock_cfg.get("news_enabled") and now - last_news >= max(60, int(astock_cfg.get("news_refresh_seconds", 120))):
                    refresh_news_sources()
                    last_news = now
                if fund_cfg.get("enabled") and now - last_funds >= max(3600, int(fund_cfg.get("check_interval_seconds", 86400))):
                    sync_official_fund_reports()
                    last_funds = now
            except Exception as exc:
                update_source_health("background_refresh", "ERROR", 0, f"{type(exc).__name__}: {exc}", success=False)
            time.sleep(1)

    threading.Thread(target=worker, name="astock-background-refresh", daemon=True).start()


def run(host: str, port: int, open_browser: bool = True):
    start_background_refresh()
    server=ThreadingHTTPServer((host,port),Handler)
    url=f"http://{host}:{port}"
    print(f"A股智能投研网页版 V4.1.0 已启动: {url}")
    print("关闭窗口或按 Ctrl+C 停止。")
    if open_browser:
        threading.Timer(1.0,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="A股智能投研网页版")
    parser.add_argument("--host", default=config()["host"])
    parser.add_argument("--port", type=int, default=int(config()["port"]))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    run(args.host, args.port, not args.no_browser)


if __name__ == "__main__":
    main()
