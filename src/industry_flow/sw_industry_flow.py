"""Shenwan Level 2 aggregation of Tushare Shanghai/Shenzhen A-share money flow."""

from __future__ import annotations

import datetime as dt
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import data_sources

TUSHARE_API_URL = "https://api.tushare.pro"
SOURCE_NAME = "tushare_moneyflow_sw_l2"
DAILY_CONFIG = data_sources["industry"]["daily"]
MINIMUM_COVERAGE = float(DAILY_CONFIG["minimum_coverage"])
MONEYFLOW_API = DAILY_CONFIG["flow_api"]
DAILY_API = DAILY_CONFIG["universe_api"]
MEMBERSHIP_API = DAILY_CONFIG["membership_api"]
CALENDAR_API = DAILY_CONFIG["calendar_api"]
FLOW_FIELD = DAILY_CONFIG["flow_field"]
MONEYFLOW_FIELDS = f"ts_code,trade_date,{FLOW_FIELD}"
DAILY_FIELDS = "ts_code,trade_date"
MEMBERSHIP_FIELDS = (
    "l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,ts_code,name,"
    "in_date,out_date,is_new"
)
PERIOD_WINDOWS = tuple(
    (f"{int(window)}日", int(window))
    for window in DAILY_CONFIG["rolling_windows"]
)
PAGE_SIZE = 2000


def _date_key(value: Any) -> str:
    raw = str(value).strip()
    if len(raw) == 8 and raw.isdigit():
        return raw
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y%m%d")


def _resolve_token(token: str | None = None) -> str:
    resolved = (token if token is not None else os.getenv("TUSHARE_TOKEN", "")).strip()
    if not resolved:
        raise RuntimeError(
            "TUSHARE_TOKEN is not configured; add it to the local .env file"
        )
    return resolved


def _api_request(
    api_name: str,
    params: dict[str, Any],
    fields: str,
    *,
    token: str | None = None,
    session: Any | None = None,
    timeout: float = 30,
    retries: int = 2,
    retry_sleep: float = 0.5,
) -> pd.DataFrame:
    resolved_token = _resolve_token(token)
    client = session or requests.Session()
    payload = {
        "api_name": api_name,
        "token": resolved_token,
        "params": params,
        "fields": fields,
    }
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = client.post(TUSHARE_API_URL, json=payload, timeout=timeout)
            response.raise_for_status()
            body = response.json()
            if body.get("code") != 0:
                message = str(body.get("msg", "unknown error"))[:240]
                message = message.replace(resolved_token, "<redacted>")
                raise RuntimeError(
                    f"Tushare {api_name} API error ({body.get('code')}): {message}"
                )
            data = body.get("data") or {}
            columns = data.get("fields") or []
            rows = data.get("items") or []
            return pd.DataFrame.from_records(rows, columns=columns)
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(retry_sleep * (attempt + 1))
    message = str(last_error or "unknown error").replace(resolved_token, "<redacted>")
    raise RuntimeError(
        f"Tushare {api_name} request failed after {retries + 1} attempts: "
        f"{message[:240]}"
    ) from last_error


def fetch_tushare_daily_universe(
    trade_date: dt.date,
    *,
    token: str | None = None,
    session: Any | None = None,
    retries: int = 2,
) -> pd.DataFrame:
    """Fetch traded Shanghai/Shenzhen A-share codes for a session."""
    date_string = trade_date.strftime("%Y%m%d")
    frame = _api_request(
        DAILY_API,
        {"trade_date": date_string},
        DAILY_FIELDS,
        token=token,
        session=session,
        retries=retries,
    )
    _validate_trade_frame(frame, trade_date, {"ts_code", "trade_date"}, "daily")
    frame = frame.copy()
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    frame = frame.loc[frame["ts_code"].str.endswith((".SH", ".SZ"))]
    frame = frame.drop_duplicates("ts_code", keep="last")
    if frame.empty:
        raise RuntimeError(f"Tushare daily returned no A-share rows for {date_string}")
    return frame.reset_index(drop=True)


def fetch_tushare_moneyflow(
    trade_date: dt.date,
    *,
    token: str | None = None,
    session: Any | None = None,
    retries: int = 2,
) -> pd.DataFrame:
    """Fetch stock-level active-order net flow (万元) for one SH/SZ A-share session."""
    date_string = trade_date.strftime("%Y%m%d")
    frame = _api_request(
        MONEYFLOW_API,
        {"trade_date": date_string},
        MONEYFLOW_FIELDS,
        token=token,
        session=session,
        retries=retries,
    )
    _validate_trade_frame(
        frame, trade_date, {"ts_code", "trade_date", FLOW_FIELD}, "moneyflow"
    )
    frame = frame.copy()
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    frame = frame.loc[frame["ts_code"].str.endswith((".SH", ".SZ"))]
    frame[FLOW_FIELD] = pd.to_numeric(frame[FLOW_FIELD], errors="coerce")
    frame = frame.dropna(subset=[FLOW_FIELD])
    frame = frame.drop_duplicates("ts_code", keep="last")
    if frame.empty:
        raise RuntimeError(f"Tushare moneyflow returned no A-share rows for {date_string}")
    return frame.reset_index(drop=True)


def _validate_trade_frame(
    frame: pd.DataFrame,
    trade_date: dt.date,
    required: set[str],
    api_name: str,
) -> None:
    missing = required.difference(frame.columns)
    if missing:
        raise RuntimeError(
            f"Tushare {api_name} response lacks fields: {sorted(missing)}"
        )
    expected = trade_date.strftime("%Y%m%d")
    actual = frame["trade_date"].map(_date_key)
    if not actual.eq(expected).all():
        raise RuntimeError(
            f"Tushare {api_name} returned rows for dates other than {expected}"
        )


def fetch_sw_l2_memberships(
    *,
    is_new: str = "N",
    token: str | None = None,
    session: Any | None = None,
    page_size: int = PAGE_SIZE,
    max_pages: int = 100,
    retries: int = 2,
) -> pd.DataFrame:
    """Fetch paginated SW membership intervals; N means full membership history."""
    if is_new not in {"Y", "N"}:
        raise ValueError("is_new must be 'Y' or 'N'")
    pages: list[pd.DataFrame] = []
    for page in range(max_pages):
        frame = _api_request(
            MEMBERSHIP_API,
            {"is_new": is_new, "limit": page_size, "offset": page * page_size},
            MEMBERSHIP_FIELDS,
            token=token,
            session=session,
            retries=retries,
        )
        if frame.empty:
            break
        pages.append(frame)
        if len(frame) < page_size:
            break
    else:
        raise RuntimeError(
            f"Tushare index_member_all exceeded the {max_pages}-page safety limit"
        )
    if not pages:
        raise RuntimeError("Tushare index_member_all returned no membership rows")
    result = pd.concat(pages, ignore_index=True)
    expected = {"ts_code", "l2_code", "l2_name", "in_date", "out_date"}
    missing = expected.difference(result.columns)
    if missing:
        raise RuntimeError(
            f"Tushare membership response lacks fields: {sorted(missing)}"
        )
    result["ts_code"] = result["ts_code"].astype(str).str.strip().str.upper()
    return result.drop_duplicates(
        ["ts_code", "l2_code", "l3_code", "in_date"], keep="last"
    ).reset_index(drop=True)


def refresh_sw_l2_membership_cache(
    cache_path: Path,
    *,
    token: str | None = None,
    session: Any | None = None,
    force_full_history: bool = False,
) -> pd.DataFrame:
    """Load cached history and merge current constituents; seed full history once."""
    cached = pd.DataFrame()
    if cache_path.exists() and not force_full_history:
        cached = pd.read_csv(cache_path, dtype=str, keep_default_na=False)
    if cached.empty or force_full_history:
        updated = fetch_sw_l2_memberships(
            is_new="N", token=token, session=session
        )
    else:
        current = fetch_sw_l2_memberships(is_new="Y", token=token, session=session)
        updated = pd.concat([cached, current], ignore_index=True, sort=False)
        updated = updated.drop_duplicates(
            ["ts_code", "l2_code", "l3_code", "in_date"], keep="last"
        )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    updated.to_csv(cache_path, index=False, encoding="utf-8-sig")
    return updated.reset_index(drop=True)


def select_sw_l2_memberships_asof(
    memberships: pd.DataFrame,
    trade_date: dt.date,
) -> pd.DataFrame:
    """Select one effective Level 2 membership per stock on a trade date."""
    required = {"ts_code", "l2_code", "l2_name", "in_date", "out_date"}
    missing = required.difference(memberships.columns)
    if missing:
        raise ValueError(f"Membership history missing columns: {sorted(missing)}")
    frame = memberships.copy()
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    frame["l2_code"] = frame["l2_code"].astype(str).str.strip()
    frame["l2_name"] = frame["l2_name"].astype(str).str.strip()
    frame["_in"] = pd.to_datetime(
        frame["in_date"].astype(str).str.strip(), format="%Y%m%d", errors="coerce"
    )
    frame["_out"] = pd.to_datetime(
        frame["out_date"].astype(str).str.strip(), format="%Y%m%d", errors="coerce"
    )
    target = pd.Timestamp(trade_date)
    frame = frame.loc[
        frame["ts_code"].ne("")
        & frame["l2_code"].ne("")
        & frame["l2_name"].ne("")
        & (frame["_in"].isna() | frame["_in"].le(target))
        & (frame["_out"].isna() | frame["_out"].ge(target))
    ].copy()
    if frame.empty:
        raise RuntimeError(f"No effective Shenwan L2 memberships for {trade_date}")

    # A stock may have multiple L3 history rows within one L2 sector. Collapse
    # those rows, then choose the latest effective L2 assignment if old rows
    # have blank out_date values in the provider response.
    frame = frame.sort_values(["ts_code", "_in", "l2_code"], na_position="first")
    latest_start = frame.groupby("ts_code", dropna=False)["_in"].transform("max")
    frame = frame.loc[frame["_in"].eq(latest_start) | (frame["_in"].isna() & latest_start.isna())]
    conflicts = frame.groupby("ts_code")["l2_code"].nunique()
    conflicts = conflicts.loc[conflicts.gt(1)]
    if not conflicts.empty:
        samples = ", ".join(conflicts.index.astype(str).tolist()[:5])
        raise RuntimeError(
            f"Conflicting active Shenwan L2 mappings for {len(conflicts)} stocks: {samples}"
        )
    return (
        frame.drop_duplicates("ts_code", keep="last")
        [["ts_code", "l2_code", "l2_name"]]
        .reset_index(drop=True)
    )


def aggregate_sw_l2_daily_flow(
    trade_date: dt.date,
    daily_universe: pd.DataFrame,
    moneyflow: pd.DataFrame,
    memberships: pd.DataFrame,
    *,
    minimum_coverage: float = MINIMUM_COVERAGE,
    ingested_at: str | None = None,
) -> pd.DataFrame:
    """Aggregate SH/SZ A-share stock money flow into effective SW L2 sectors."""
    if not 0 < minimum_coverage <= 1:
        raise ValueError("minimum_coverage must be in (0, 1]")
    _validate_trade_frame(
        daily_universe, trade_date, {"ts_code", "trade_date"}, "daily universe"
    )
    _validate_trade_frame(
        moneyflow,
        trade_date,
        {"ts_code", "trade_date", FLOW_FIELD},
        "moneyflow",
    )
    universe = daily_universe[["ts_code"]].copy()
    universe["ts_code"] = universe["ts_code"].astype(str).str.strip().str.upper()
    universe = universe.loc[universe["ts_code"].str.endswith((".SH", ".SZ"))]
    universe = universe.drop_duplicates("ts_code")
    if universe.empty:
        raise RuntimeError(f"No active Shanghai/Shenzhen A shares for {trade_date}")

    flow = moneyflow[["ts_code", FLOW_FIELD]].copy()
    flow["ts_code"] = flow["ts_code"].astype(str).str.strip().str.upper()
    flow[FLOW_FIELD] = pd.to_numeric(flow[FLOW_FIELD], errors="coerce")
    flow = flow.dropna(subset=[FLOW_FIELD]).drop_duplicates("ts_code", keep="last")
    flow = flow.merge(universe, on="ts_code", how="inner", validate="one_to_one")
    flow_coverage = len(flow) / len(universe)
    if flow_coverage < minimum_coverage:
        raise RuntimeError(
            f"Tushare moneyflow coverage is insufficient for {trade_date}: "
            f"{len(flow)}/{len(universe)} active A shares ({flow_coverage:.1%}), "
            f"minimum {minimum_coverage:.1%}"
        )

    mapping = select_sw_l2_memberships_asof(memberships, trade_date)
    mapped_universe = universe.merge(mapping, on="ts_code", how="left", validate="one_to_one")
    mapping_coverage = float(mapped_universe["l2_code"].notna().mean())
    if mapping_coverage < minimum_coverage:
        missing_codes = mapped_universe.loc[mapped_universe["l2_code"].isna(), "ts_code"]
        samples = ", ".join(missing_codes.astype(str).tolist()[:5])
        raise RuntimeError(
            f"Shenwan L2 mapping coverage is insufficient for {trade_date}: "
            f"{int(mapped_universe['l2_code'].notna().sum())}/{len(universe)} "
            f"({mapping_coverage:.1%}); examples: {samples}"
        )
    expected_by_industry = (
        mapped_universe.dropna(subset=["l2_code"])
        .groupby(["l2_code", "l2_name"], as_index=False)
        .agg(active_stock_count=("ts_code", "nunique"))
    )
    mapped_flow = flow.merge(mapping, on="ts_code", how="inner", validate="one_to_one")
    if mapped_flow.empty:
        raise RuntimeError(f"No moneyflow records map to Shenwan L2 on {trade_date}")
    grouped = (
        mapped_flow.groupby(["l2_code", "l2_name"], as_index=False)
        .agg(
            net_mf_amount_wan=(FLOW_FIELD, "sum"),
            flow_stock_count=("ts_code", "nunique"),
        )
    )
    grouped = expected_by_industry.merge(
        grouped, on=["l2_code", "l2_name"], how="left", validate="one_to_one"
    )
    grouped["flow_stock_count"] = grouped["flow_stock_count"].fillna(0).astype(int)
    grouped["net_mf_amount_wan"] = grouped["net_mf_amount_wan"].fillna(0)
    grouped["coverage_pct"] = (
        grouped["flow_stock_count"] / grouped["active_stock_count"] * 100
    )
    grouped["net_amount_yi"] = grouped["net_mf_amount_wan"] / 10000
    grouped["market_coverage_pct"] = flow_coverage * 100
    grouped["mapping_coverage_pct"] = mapping_coverage * 100
    grouped["trade_date"] = pd.Timestamp(trade_date)
    grouped["source"] = SOURCE_NAME
    grouped["ingested_at"] = ingested_at or dt.datetime.now().isoformat(timespec="seconds")
    grouped["flow_unit"] = "Tushare L2主动买卖净流入；原始单位万元"
    grouped["data_sufficient"] = grouped["coverage_pct"].ge(minimum_coverage * 100)
    return grouped.rename(columns={"l2_code": "industry_code", "l2_name": "industry_name"})[
        [
            "trade_date", "industry_code", "industry_name", "net_mf_amount_wan",
            "net_amount_yi", "active_stock_count", "flow_stock_count",
            "coverage_pct", "market_coverage_pct", "mapping_coverage_pct",
            "data_sufficient", "source", "flow_unit", "ingested_at",
        ]
    ].sort_values("industry_code").reset_index(drop=True)


def fetch_open_trade_dates(
    end_date: dt.date,
    count: int,
    *,
    token: str | None = None,
    session: Any | None = None,
) -> list[dt.date]:
    """Return the most recent open SSE sessions through end_date."""
    if count < 1:
        raise ValueError("count must be positive")
    start_date = end_date - dt.timedelta(days=max(45, count * 4))
    frame = _api_request(
        CALENDAR_API,
        {
            "exchange": "SSE",
            "start_date": start_date.strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d"),
            "is_open": "1",
        },
        "cal_date,is_open",
        token=token,
        session=session,
    )
    if not {"cal_date", "is_open"}.issubset(frame.columns):
        raise RuntimeError("Tushare trade_cal response lacks cal_date or is_open")
    opens = frame.loc[pd.to_numeric(frame["is_open"], errors="coerce").eq(1), "cal_date"]
    dates = sorted(
        {
            dt.datetime.strptime(str(value), "%Y%m%d").date()
            for value in opens
            if str(value).isdigit()
        }
    )
    dates = [value for value in dates if value <= end_date]
    if not dates:
        raise RuntimeError(f"Tushare trade_cal returned no open sessions through {end_date}")
    if len(dates) < count:
        raise RuntimeError(
            f"Tushare trade_cal returned only {len(dates)} sessions; need {count}"
        )
    return dates[-count:]


def build_sw_period_snapshot(
    history: pd.DataFrame,
    sessions: list[dt.date],
    *,
    minimum_coverage: float = MINIMUM_COVERAGE,
) -> pd.DataFrame:
    """Build cumulative 1/3/5/10/20-session rows from complete daily history."""
    if history.empty or not sessions:
        return pd.DataFrame()
    required = {
        "trade_date", "industry_code", "industry_name", "net_amount_yi",
        "coverage_pct", "active_stock_count", "flow_stock_count",
    }
    missing = required.difference(history.columns)
    if missing:
        raise ValueError(f"SW flow history missing columns: {sorted(missing)}")
    frame = history.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.strftime("%Y%m%d")
    frame["net_amount_yi"] = pd.to_numeric(frame["net_amount_yi"], errors="coerce")
    frame["coverage_pct"] = pd.to_numeric(frame["coverage_pct"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "industry_code", "net_amount_yi"])
    frame = frame.drop_duplicates(["trade_date", "industry_code"], keep="last")
    ordered_sessions = sorted(sessions)
    session_keys = [_date_key(value) for value in ordered_sessions]
    if len(set(session_keys)) != len(session_keys):
        raise ValueError("sessions must contain unique dates")

    latest_date = pd.Timestamp(ordered_sessions[-1])
    output: list[dict[str, Any]] = []
    for period, window in PERIOD_WINDOWS:
        selected_dates = session_keys[-window:]
        if len(selected_dates) != window:
            continue
        selected = frame.loc[frame["trade_date"].isin(selected_dates)].copy()
        if selected["trade_date"].nunique() != window:
            continue
        for industry_code, group in selected.groupby("industry_code", sort=False):
            if group["trade_date"].nunique() != window:
                continue
            if group["coverage_pct"].isna().any() or group["coverage_pct"].lt(minimum_coverage * 100).any():
                continue
            latest_row = group.loc[group["trade_date"].eq(selected_dates[-1])].iloc[-1]
            output.append({
                "trade_date": latest_date,
                "period": period,
                "industry_code": industry_code,
                "industry_name": latest_row["industry_name"],
                "net_amount_yi": float(group["net_amount_yi"].sum()),
                "coverage_pct": float(group["coverage_pct"].min()),
                "market_coverage_pct": float(
                    pd.to_numeric(group.get("market_coverage_pct", group["coverage_pct"]), errors="coerce").min()
                ),
                "mapping_coverage_pct": float(
                    pd.to_numeric(group.get("mapping_coverage_pct", group["coverage_pct"]), errors="coerce").min()
                ),
                "active_stock_count": int(pd.to_numeric(group["active_stock_count"], errors="coerce").sum()),
                "flow_stock_count": int(pd.to_numeric(group["flow_stock_count"], errors="coerce").sum()),
                "trade_dates_count": window,
                "source": SOURCE_NAME,
            })
    return pd.DataFrame.from_records(output)
