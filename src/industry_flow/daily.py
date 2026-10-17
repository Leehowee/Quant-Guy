from __future__ import annotations

import datetime as dt
import pandas as pd

from .config import settings
from .digest import build_sw_industry_flow_digest
from .dingtalk import send_group_image_markdown
from .industry_report_image import render_industry_report_png
from .market_snapshot import capture_market_snapshot, save_market_snapshot
from .source import is_cn_trade_date
from .sw_industry_flow import (
    aggregate_sw_l2_daily_flow,
    build_sw_period_snapshot,
    fetch_open_trade_dates,
    fetch_tushare_daily_universe,
    fetch_tushare_moneyflow,
    refresh_sw_l2_membership_cache,
)
from .storage import (
    save_daily_csv,
    upsert_parquet,
    write_parquet_atomic,
)


def run_daily(send_dingtalk: bool = True, force_send: bool = False) -> None:
    settings.ensure_dirs()

    today = dt.date.today()

    if not is_cn_trade_date(today):
        print(f"[daily] {today} is not a CN trade date. Skip.")
        return

    print("[daily] refreshing Shenwan L2 membership history")
    memberships = refresh_sw_l2_membership_cache(settings.sw_l2_membership_file)
    print("[daily] fetching all available Shanghai/Shenzhen A-share Tushare moneyflow")
    daily_universe = fetch_tushare_daily_universe(today)
    moneyflow = fetch_tushare_moneyflow(today)
    daily = aggregate_sw_l2_daily_flow(
        today,
        daily_universe,
        moneyflow,
        memberships,
    )
    market_coverage = float(daily["market_coverage_pct"].iloc[0])
    mapping_coverage = float(daily["mapping_coverage_pct"].iloc[0])
    print(
        f"[daily] aggregated {len(daily)} Shenwan L2 sectors; "
        f"stock-flow coverage={market_coverage:.1f}%, "
        f"membership coverage={mapping_coverage:.1f}%"
    )

    market_snapshot = pd.DataFrame()
    market_unavailable_reason = None
    try:
        market_snapshot = capture_market_snapshot(today)
    except Exception as exc:
        market_unavailable_reason = "采集失败（网络或数据源不可用）；ETF候选观察保持数据不足。"
        print(f"[daily] market regime unavailable: {type(exc).__name__}: {exc}")
    else:
        try:
            market_path = save_market_snapshot(market_snapshot)
            print(f"[daily] market regime snapshot saved: {market_path}")
        except Exception as exc:
            print(f"[daily] market regime snapshot could not be saved: {type(exc).__name__}: {exc}")

    raw_file = (
        settings.raw_daily_dir
        / f"industry_flow_{today:%Y%m%d}_sw_l2_tushare.csv"
    )

    save_daily_csv(
        daily,
        raw_file,
    )

    print(f"[daily] raw snapshot saved: {raw_file}")

    history = upsert_parquet(
        daily,
        settings.sw_industry_flow_history_file,
        key=["trade_date", "industry_code"],
    )

    sessions = fetch_open_trade_dates(today, 20)
    features = build_sw_period_snapshot(
        history,
        sessions,
    )
    if features.empty:
        raise RuntimeError(
            "No sufficient Shenwan L2 flow snapshot could be built; "
            "run bin/run_sw_industry_backfill.py --sessions 20 to seed history"
        )

    write_parquet_atomic(
        features,
        settings.sw_industry_flow_features_file,
    )

    msg = build_sw_industry_flow_digest(
        features,
        top_n=settings.digest_top_n,
        market_snapshot=market_snapshot,
        trade_date=today,
        market_unavailable_reason=market_unavailable_reason,
    )
    if settings.dingtalk_keyword and settings.dingtalk_keyword not in msg:
        msg = f"{msg}\n{settings.dingtalk_keyword}"

    print(msg)

    if send_dingtalk:
        sent_marker = (
            settings.processed_dir
            / f"dingtalk_sw_l2_sent_{today:%Y%m%d}.txt"
        )
        if sent_marker.exists() and not force_send:
            print(f"[daily] DingTalk already sent for {today}; skip duplicate.")
        elif settings.dingtalk_app_configured and not settings.dingtalk_app_ready:
            raise RuntimeError(
                "Incomplete DingTalk app configuration for image delivery; set "
                "DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET, and "
                "DINGTALK_OPEN_CONVERSATION_ID."
            )
        elif settings.dingtalk_app_ready:
            image_path = settings.reports_dir / f"industry_flow_{today:%Y%m%d}.png"
            render_industry_report_png(
                features,
                today,
                image_path,
                top_n=settings.digest_top_n,
                market_snapshot=market_snapshot,
                market_unavailable_reason=market_unavailable_reason,
            )
            title = f"申万二级行业资金流日报 {today:%Y-%m-%d}"
            send_group_image_markdown(
                settings.dingtalk_client_id,
                settings.dingtalk_client_secret,
                settings.dingtalk_robot_code or settings.dingtalk_client_id,
                settings.dingtalk_open_conversation_id,
                image_path,
                title,
            )
            sent_marker.write_text(
                f"sent_at={dt.datetime.now().isoformat(timespec='seconds')}\n"
                f"image={image_path.name}\n",
                encoding="utf-8",
            )
            print(f"[daily] DingTalk infographic sent: {image_path}")
        else:
            print("[daily] DingTalk app credentials are not configured; skip image send.")
