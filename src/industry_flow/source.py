from __future__ import annotations

import datetime as dt
import math
import os
import time
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urlsplit

import pandas as pd
import requests

from .config import data_sources, resolve_akshare_api, settings


DAILY_RENAME = {
    "名称": "industry_name",
    "今日涨跌幅": "change_pct",
    "主力净流入-净额": "main_net_inflow",
    "主力净流入-净占比": "main_net_inflow_ratio",
    "超大单净流入-净额": "super_large_net_inflow",
    "超大单净流入-净占比": "super_large_net_inflow_ratio",
    "大单净流入-净额": "large_net_inflow",
    "大单净流入-净占比": "large_net_inflow_ratio",
    "中单净流入-净额": "medium_net_inflow",
    "中单净流入-净占比": "medium_net_inflow_ratio",
    "小单净流入-净额": "small_net_inflow",
    "小单净流入-净占比": "small_net_inflow_ratio",
    "主力净流入最大股": "top_main_inflow_stock",
    "今日主力净流入最大股": "top_main_inflow_stock",
}

HIST_RENAME = {
    "日期": "trade_date",
    "主力净流入-净额": "main_net_inflow",
    "主力净流入-净占比": "main_net_inflow_ratio",
    "超大单净流入-净额": "super_large_net_inflow",
    "超大单净流入-净占比": "super_large_net_inflow_ratio",
    "大单净流入-净额": "large_net_inflow",
    "大单净流入-净占比": "large_net_inflow_ratio",
    "中单净流入-净额": "medium_net_inflow",
    "中单净流入-净占比": "medium_net_inflow_ratio",
    "小单净流入-净额": "small_net_inflow",
    "小单净流入-净占比": "small_net_inflow_ratio",
}

NUMERIC_COLS = [
    "change_pct",
    "main_net_inflow",
    "main_net_inflow_ratio",
    "super_large_net_inflow",
    "super_large_net_inflow_ratio",
    "large_net_inflow",
    "large_net_inflow_ratio",
    "medium_net_inflow",
    "medium_net_inflow_ratio",
    "small_net_inflow",
    "small_net_inflow_ratio",
]

_INDUSTRY_HISTORY_SOURCE = data_sources["industry"]["history"]
EASTMONEY_DAILY_URL = _INDUSTRY_HISTORY_SOURCE["daily_url"]
EASTMONEY_HISTORY_URL = _INDUSTRY_HISTORY_SOURCE["history_url"]
EASTMONEY_HEADERS = {
    "User-Agent": _INDUSTRY_HISTORY_SOURCE["user_agent"],
    "Referer": _INDUSTRY_HISTORY_SOURCE["referer"],
}
_INDUSTRY_CODES: dict[str, str] = {}
_EASTMONEY_DIRECT = os.getenv("EASTMONEY_DIRECT", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _make_eastmoney_session() -> requests.Session:
    session = requests.Session()
    if _EASTMONEY_DIRECT:
        # Bypass Python's proxy environment variables so RedStarV's TUN/domain
        # rules can route the request based on the Eastmoney hostname.
        session.trust_env = False
        return session

    # Prefer the configured SOCKS proxy for Eastmoney. In this environment
    # HTTPS_PROXY points to a proxy that returns 502 for Eastmoney's quote
    # hosts, while ALL_PROXY (SOCKS5) can reach them.
    proxy = os.getenv("EASTMONEY_PROXY", "").strip()
    if not proxy:
        all_proxy = (
            os.getenv("ALL_PROXY", "") or os.getenv("all_proxy", "")
        ).strip()
        if urlsplit(all_proxy).scheme.lower().startswith("socks"):
            proxy = all_proxy

    if proxy:
        session.trust_env = False
        session.proxies.update({"http": proxy, "https": proxy})
    return session


_EASTMONEY_SESSION = _make_eastmoney_session()
_DEFAULT_EASTMONEY_SESSION = requests.Session()


def _eastmoney_get(url: str, params: dict | None = None) -> requests.Response:
    sessions = [_EASTMONEY_SESSION]
    if not _EASTMONEY_DIRECT and not _EASTMONEY_SESSION.trust_env:
        # Fall back to the normal HTTPS proxy if the preferred SOCKS route is
        # temporarily unavailable (or vice versa when explicitly configured).
        sessions.append(_DEFAULT_EASTMONEY_SESSION)

    last_error: Exception | None = None
    for session in sessions:
        try:
            response = session.get(
                url,
                params=params,
                headers=EASTMONEY_HEADERS,
                timeout=20,
            )
            if response.status_code >= 500:
                response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc

    if last_error:
        raise last_error
    raise RuntimeError("No Eastmoney request route is configured")


def _eastmoney_json(url: str, params: dict) -> dict:
    response = _eastmoney_get(url, params=params)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or data.get("rc", 0) not in (0, None):
        raise RuntimeError(f"Eastmoney returned an invalid response: {data}")
    return data


def _fetch_daily_pages() -> pd.DataFrame:
    params = {
        "pn": 1,
        "pz": 100,
        "po": 1,
        "np": 1,
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fltt": 2,
        "invt": 2,
        "fid0": "f62",
        "fs": "m:90 t:2",
        "stat": 1,
        "fields": (
            "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,"
            "f84,f87,f204,f205,f124"
        ),
    }
    first = _eastmoney_json(EASTMONEY_DAILY_URL, params)
    payload = first.get("data") or {}
    total = int(payload.get("total") or 0)
    page_count = math.ceil(total / params["pz"])
    rows = list(payload.get("diff") or [])

    for page in range(2, page_count + 1):
        page_params = {**params, "pn": page}
        page_data = _eastmoney_json(EASTMONEY_DAILY_URL, page_params)
        rows.extend((page_data.get("data") or {}).get("diff") or [])
        time.sleep(0.1)

    if not rows:
        raise RuntimeError("Eastmoney daily industry flow returned no rows")

    raw = pd.DataFrame(rows)
    global _INDUSTRY_CODES
    _INDUSTRY_CODES = dict(
        zip(raw.get("f14", pd.Series(dtype=str)), raw.get("f12", pd.Series(dtype=str)))
    )
    field_map = {
        "f14": "industry_name",
        "f3": "change_pct",
        "f62": "main_net_inflow",
        "f184": "main_net_inflow_ratio",
        "f66": "super_large_net_inflow",
        "f69": "super_large_net_inflow_ratio",
        "f72": "large_net_inflow",
        "f75": "large_net_inflow_ratio",
        "f78": "medium_net_inflow",
        "f81": "medium_net_inflow_ratio",
        "f84": "small_net_inflow",
        "f87": "small_net_inflow_ratio",
        "f204": "top_main_inflow_stock",
    }
    return raw.rename(columns=field_map)


def _retry(fn: Callable[[], pd.DataFrame], label: str) -> pd.DataFrame:
    last_error = None
    for attempt in range(1, settings.request_retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            if attempt < settings.request_retries:
                time.sleep(settings.request_retry_sleep * attempt)
    raise RuntimeError(
        f"{label} failed after {settings.request_retries} attempts: {last_error}"
    )


def _to_numeric(df: pd.DataFrame) -> pd.DataFrame:
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def fetch_daily_industry_flow(run_date: dt.date | None = None) -> pd.DataFrame:
    # 获取当日全部行业资金流，不做业务筛选
    run_date = run_date or dt.date.today()

    raw = _retry(_fetch_daily_pages, "daily industry fund flow")

    if raw is None or raw.empty:
        raise RuntimeError("daily industry fund flow returned empty dataframe")

    df = raw.rename(columns=DAILY_RENAME).copy()
    df["trade_date"] = pd.Timestamp(run_date)
    df["source"] = "eastmoney_direct_daily"
    df["ingested_at"] = pd.Timestamp.now()

    expected = [
        "trade_date",
        "industry_name",
        "change_pct",
        "main_net_inflow",
        "main_net_inflow_ratio",
        "super_large_net_inflow",
        "super_large_net_inflow_ratio",
        "large_net_inflow",
        "large_net_inflow_ratio",
        "medium_net_inflow",
        "medium_net_inflow_ratio",
        "small_net_inflow",
        "small_net_inflow_ratio",
        "top_main_inflow_stock",
        "source",
        "ingested_at",
    ]

    df = _to_numeric(df)

    for col in expected:
        if col not in df.columns:
            df[col] = pd.NA

    df = df[expected]
    df["industry_name"] = df["industry_name"].astype(str).str.strip()
    return df


THS_PERIODS = data_sources["industry"]["daily"]["periods"]


def fetch_ths_industry_flow(
    period: str,
    run_date: dt.date | None = None,
) -> pd.DataFrame:
    """Fetch one same-day THS industry-flow ranking, in native 亿元 units."""
    if period not in THS_PERIODS:
        raise ValueError(f"Unsupported THS period: {period}")
    run_date = run_date or dt.date.today()
    raw = _retry(
        lambda: resolve_akshare_api(data_sources["industry"]["daily"]["api"])(
            symbol=THS_PERIODS[period]
        ),
        f"AKShare THS {period} industry fund flow",
    )
    if raw is None or raw.empty:
        raise RuntimeError(f"AKShare THS {period} industry flow returned no rows")

    field_map = {
        "序号": "provider_rank",
        "行业": "industry_name",
        "行业指数": "industry_index",
        "行业-涨跌幅": "change_pct",
        "阶段涨跌幅": "period_change_pct",
        "流入资金": "inflow_amount_yi",
        "流出资金": "outflow_amount_yi",
        "净额": "net_amount_yi",
        "公司家数": "company_count",
        "领涨股": "top_stock",
        "领涨股-涨跌幅": "top_stock_change_pct",
        "当前价": "top_stock_price",
    }
    df = raw.rename(columns=field_map).copy()
    df["trade_date"] = pd.Timestamp(run_date)
    df["period"] = period
    df["source"] = "akshare_ths_industry"
    df["ingested_at"] = pd.Timestamp.now()

    numeric_cols = (
        "industry_index",
        "provider_rank",
        "change_pct",
        "period_change_pct",
        "inflow_amount_yi",
        "outflow_amount_yi",
        "net_amount_yi",
        "company_count",
        "top_stock_change_pct",
        "top_stock_price",
    )
    for col in numeric_cols:
        if col in df.columns:
            if col == "period_change_pct":
                df[col] = df[col].astype(str).str.rstrip("%")
            df[col] = pd.to_numeric(df[col], errors="coerce")
    expected = [
        "trade_date", "period", "provider_rank", "industry_name",
        "change_pct", "period_change_pct", "inflow_amount_yi",
        "outflow_amount_yi", "net_amount_yi", "industry_index",
        "company_count", "top_stock", "top_stock_change_pct",
        "top_stock_price", "source", "ingested_at",
    ]
    for col in expected:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[expected]
    df["industry_name"] = df["industry_name"].astype(str).str.strip()
    return df


def fetch_daily_ths_industry_flow(
    run_date: dt.date | None = None,
) -> pd.DataFrame:
    """Backward-compatible wrapper for the THS instant ranking."""
    return fetch_ths_industry_flow("即时", run_date=run_date)


def fetch_current_industries() -> list[str]:
    try:
        df = fetch_daily_industry_flow()
        return (
            df["industry_name"]
            .dropna()
            .astype(str)
            .str.strip()
            .loc[lambda s: s.ne("")]
            .drop_duplicates()
            .tolist()
        )
    except Exception as exc:
        print(
            "[source] daily ranking API unavailable; falling back to "
            f"Eastmoney industry page: {exc}"
        )
        return _fetch_industries_from_page()


class _IndustryPageParser(HTMLParser):
    """Read the industry selector (not concept/region selectors) from hy.html."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.target_depth: int | None = None
        self.selector_done = False
        self.anchor_href: str | None = None
        self.anchor_text: list[str] = []
        self.industry_codes: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        if tag == "div":
            self.div_depth += 1
            if (
                self.target_depth is None
                and not self.selector_done
                and "pop-cont" in attrs.get("class", "").split()
            ):
                self.target_depth = self.div_depth
        elif (
            tag == "a"
            and self.target_depth is not None
            and self.div_depth >= self.target_depth
        ):
            href = attrs.get("href", "")
            if href.startswith("/bkzj/BK") and href.endswith(".html"):
                self.anchor_href = href
                self.anchor_text = []

    def handle_data(self, data: str) -> None:
        if self.anchor_href:
            self.anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.anchor_href:
            code = self.anchor_href.rsplit("/", 1)[-1][:-5]
            name = "".join(self.anchor_text).strip()
            if code and name:
                self.industry_codes[name] = code
            self.anchor_href = None
            self.anchor_text = []
        elif tag == "div":
            if self.target_depth == self.div_depth:
                self.target_depth = None
                self.selector_done = True
            self.div_depth = max(0, self.div_depth - 1)


def _fetch_industries_from_page() -> list[str]:
    global _INDUSTRY_CODES
    response = _eastmoney_get(
        _INDUSTRY_HISTORY_SOURCE["industry_list_url"],
    )
    response.raise_for_status()
    response.encoding = "utf-8"

    parser = _IndustryPageParser()
    parser.feed(response.text)
    if len(parser.industry_codes) < 50:
        raise RuntimeError(
            "Could not extract the industry selector from Eastmoney's page; "
            f"found {len(parser.industry_codes)} industry entries."
        )

    _INDUSTRY_CODES = parser.industry_codes
    print(f"[source] parsed {len(_INDUSTRY_CODES)} industries from hy.html")
    return list(_INDUSTRY_CODES)


def fetch_industry_flow_history(industry_name: str) -> pd.DataFrame:
    if industry_name not in _INDUSTRY_CODES:
        # Keep this function usable independently of fetch_current_industries.
        fetch_current_industries()
    industry_code = _INDUSTRY_CODES.get(industry_name)
    if not industry_code:
        raise RuntimeError(f"Eastmoney industry code not found: {industry_name}")

    params = {
        "lmt": 0,
        "klt": 101,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "secid": f"90.{industry_code}",
    }

    def fetch_history() -> pd.DataFrame:
        payload = _eastmoney_json(EASTMONEY_HISTORY_URL, params).get("data") or {}
        klines = payload.get("klines") or []
        if not klines:
            return pd.DataFrame()
        # Eastmoney returns date, main, small, medium, large, super-large,
        # followed by their corresponding net-inflow ratios.
        columns = [
            "trade_date",
            "main_net_inflow",
            "small_net_inflow",
            "medium_net_inflow",
            "large_net_inflow",
            "super_large_net_inflow",
            "main_net_inflow_ratio",
            "small_net_inflow_ratio",
            "medium_net_inflow_ratio",
            "large_net_inflow_ratio",
            "super_large_net_inflow_ratio",
        ]
        parsed = [line.split(",")[: len(columns)] for line in klines]
        return pd.DataFrame(parsed, columns=columns)

    raw = _retry(fetch_history, f"history fund flow: {industry_name}")
    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.copy()
    df["industry_name"] = industry_name
    df["change_pct"] = pd.NA
    df["top_main_inflow_stock"] = pd.NA
    df["source"] = "eastmoney_direct_hist"
    df["ingested_at"] = pd.Timestamp.now()

    if "trade_date" not in df.columns:
        raise RuntimeError(
            f"{industry_name}: history response lacks 日期/trade_date"
        )

    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = _to_numeric(df)

    expected = [
        "trade_date",
        "industry_name",
        "change_pct",
        "main_net_inflow",
        "main_net_inflow_ratio",
        "super_large_net_inflow",
        "super_large_net_inflow_ratio",
        "large_net_inflow",
        "large_net_inflow_ratio",
        "medium_net_inflow",
        "medium_net_inflow_ratio",
        "small_net_inflow",
        "small_net_inflow_ratio",
        "top_main_inflow_stock",
        "source",
        "ingested_at",
    ]

    for col in expected:
        if col not in df.columns:
            df[col] = pd.NA

    return df[expected].dropna(subset=["trade_date"])


def is_cn_trade_date(date_: dt.date | None = None) -> bool:
    # 交易日历异常时继续采集，避免漏数据；后续主键会去重
    date_ = date_ or dt.date.today()

    if date_.weekday() >= 5:
        return False

    try:
        cal = resolve_akshare_api(data_sources["trade_calendar"]["api"])()
        series = pd.to_datetime(cal["trade_date"], errors="coerce").dropna().dt.date
        if series.empty:
            return True
        # 若上游交易日历没有覆盖到当前年份/日期，不据此误判为休市。
        if max(series) < date_:
            return True
        return date_ in set(series)
    except Exception:
        return True
