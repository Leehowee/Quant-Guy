from __future__ import annotations

import datetime as dt
import pandas as pd

from .config import settings
from .digest import build_ths_period_digest
from .dingtalk import send_group_image_markdown
from .features import build_features
from .industry_report_image import render_industry_report_png
from .source import (
    THS_PERIODS,
    fetch_ths_industry_flow,
    is_cn_trade_date,
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

    snapshots = []
    for period in THS_PERIODS:
        print(f"[daily] fetching THS industry flow: {period}")
        snapshots.append(fetch_ths_industry_flow(period, run_date=today))
    daily = pd.DataFrame.from_records(
        row
        for snapshot in snapshots
        for row in snapshot.to_dict(orient="records")
    )
    received_periods = set(daily["period"].dropna().astype(str))
    if received_periods != set(THS_PERIODS):
        raise RuntimeError(
            f"Incomplete THS periods: expected {list(THS_PERIODS)}, "
            f"received {sorted(received_periods)}"
        )

    raw_file = (
        settings.raw_daily_dir
        / f"industry_flow_{today:%Y%m%d}_akshare_ths_all_periods.csv"
    )

    save_daily_csv(
        daily,
        raw_file,
    )

    print(f"[daily] raw snapshot saved: {raw_file}")

    history = upsert_parquet(
        daily,
        settings.ths_history_file,
        key=["trade_date", "period", "industry_name"],
    )

    features = build_features(
        history,
        flow_column="net_amount_yi",
        rank_prefix="net_flow",
    )

    write_parquet_atomic(
        features,
        settings.ths_feature_file,
    )

    msg = build_ths_period_digest(
        features,
        top_n=settings.digest_top_n,
    )
    if settings.dingtalk_keyword and settings.dingtalk_keyword not in msg:
        msg = f"{msg}\n{settings.dingtalk_keyword}"

    print(msg)

    if send_dingtalk:
        sent_marker = (
            settings.processed_dir
            / f"dingtalk_ths_periods_sent_{today:%Y%m%d}.txt"
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
            )
            title = f"行业资金流日报 {today:%Y-%m-%d}"
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
