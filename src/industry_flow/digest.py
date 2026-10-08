from __future__ import annotations

import pandas as pd


def _fmt_yi(value) -> str:
    if pd.isna(value):
        return "NA"
    return f"{value / 1e8:+.2f}亿"


def _fmt_pct(value) -> str:
    if pd.isna(value):
        return "NA"
    return f"{value:+.2f}%"


def build_daily_digest(
    feature_df: pd.DataFrame,
    top_n: int = 5,
    flow_column: str = "main_net_inflow",
    flow_label: str = "主力资金",
    rank_prefix: str = "main_flow",
    amount_unit: str = "yuan",
    ratio_column: str | None = "main_net_inflow_ratio",
) -> str:
    # 仅生成描述性摘要，不产生买入/卖出判断
    if feature_df.empty:
        return "【行业资金流日报】暂无可用数据。"

    feature_df = feature_df.copy()
    feature_df["trade_date"] = pd.to_datetime(feature_df["trade_date"])

    latest_date = feature_df["trade_date"].max()
    today = feature_df[
        feature_df["trade_date"] == latest_date
    ].copy()
    rank_column = f"{rank_prefix}_rank"

    def fmt_amount(value) -> str:
        if pd.isna(value):
            return "NA"
        if amount_unit == "yi":
            return f"{value:+.2f}亿元"
        return _fmt_yi(value)

    def fmt_ratio(row) -> str:
        if ratio_column and ratio_column in row.index:
            return f" 占比{_fmt_pct(row[ratio_column])}"
        return ""

    lines = [
        f"【行业资金流日报 {latest_date:%Y-%m-%d}】",
        "",
        f"① 今日{flow_label}净流入前{top_n}",
    ]

    inflow = today.nlargest(top_n, flow_column)
    for i, row in enumerate(inflow.itertuples(index=False), 1):
        record = row._asdict()
        lines.append(
            f"{i}. {row.industry_name} "
            f"{fmt_amount(getattr(row, flow_column))}"
            f"{fmt_ratio(pd.Series(record))}"
        )

    lines += ["", f"② 今日{flow_label}净流出前{top_n}"]

    outflow = today.nsmallest(top_n, flow_column)
    for i, row in enumerate(outflow.itertuples(index=False), 1):
        record = row._asdict()
        lines.append(
            f"{i}. {row.industry_name} "
            f"{fmt_amount(getattr(row, flow_column))}"
            f"{fmt_ratio(pd.Series(record))}"
        )

    lines += ["", "③ 资金排名改善 / 转弱"]

    for horizon, column in (("1日", "rank_change_1d"), ("3日", "rank_change_3d")):
        if column not in today.columns:
            continue

        comparable = today.dropna(subset=[column, rank_column])
        improved = comparable[comparable[column] > 0].nlargest(top_n, column)
        weakened = comparable[comparable[column] < 0].nsmallest(top_n, column)

        lines.append(f"{horizon}变化：")
        if improved.empty and weakened.empty:
            lines.append("排名无变化" if not comparable.empty else "暂无可比较数据")
            continue

        for row in improved.itertuples(index=False):
            change = getattr(row, column)
            current_rank = getattr(row, rank_column)
            prev_rank = current_rank + change
            lines.append(
                f"↑ {row.industry_name}: "
                f"{prev_rank:.0f} → {current_rank:.0f}"
            )

        for row in weakened.itertuples(index=False):
            change = getattr(row, column)
            current_rank = getattr(row, rank_column)
            prev_rank = current_rank + change
            lines.append(
                f"↓ {row.industry_name}: "
                f"{prev_rank:.0f} → {current_rank:.0f}"
            )

    lines += ["", "④ 资金持续性"]

    persistent = today[
        today["positive_streak"] >= 3
    ].sort_values(
        ["positive_streak", rank_column],
        ascending=[False, True],
    )

    if persistent.empty:
        lines.append(f"今日无连续3日及以上净流入行业。")
    else:
        show = persistent.head(max(top_n, 8))
        items = [
            f"{r.industry_name}({int(r.positive_streak)}日)"
            for r in show.itertuples(index=False)
        ]
        lines.append(
            "连续3日及以上净流入：" + "、".join(items)
        )

    lines += [
        "",
        "说明：以上仅为行业资金流统计摘要，不构成买卖信号。",
    ]

    return "\n".join(lines)


def build_ths_period_digest(
    feature_df: pd.DataFrame,
    top_n: int = 5,
) -> str:
    """Build one concise DingTalk message covering every stored THS horizon."""
    if feature_df.empty:
        return "【行业资金流日报】暂无可用数据。"

    df = feature_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    latest_date = df["trade_date"].max()
    latest = df[df["trade_date"] == latest_date]
    periods = ["即时", "3日", "5日", "10日", "20日"]
    lines = [
        f"# 同花顺行业资金流日报 {latest_date:%Y-%m-%d}",
        "净额单位：亿元；涨跌幅单位：%。榜单按净额排序。",
        "",
        "| 周期 | 净流入前{n}（净额：亿元；涨跌幅：%） | 净流出前{n}（净额：亿元；涨跌幅：%） |".format(n=top_n),
        "|---|---|---|",
    ]

    for period in periods:
        today = latest[latest["period"] == period].copy()
        if today.empty:
            label = "今日" if period == "即时" else period
            lines.append(f"| {label} | 本次数据缺失 | 本次数据缺失 |")
            continue

        top_in = today.nlargest(top_n, "net_amount_yi")
        top_out = today.nsmallest(top_n, "net_amount_yi")
        stage_col = "period_change_pct" if period != "即时" else "change_pct"

        def render(row, rank: int) -> str:
            stage = getattr(row, stage_col, pd.NA)
            pct = f"{stage:+.2f}%" if pd.notna(stage) else "NA"
            return f"{rank}. {row.industry_name} {row.net_amount_yi:+.2f} / {pct}"

        label = "今日" if period == "即时" else period
        inflow_cell = "<br>".join(
            render(row, i)
            for i, row in enumerate(top_in.itertuples(index=False), 1)
        )
        outflow_cell = "<br>".join(
            render(row, i)
            for i, row in enumerate(top_out.itertuples(index=False), 1)
        )
        lines.append(f"| {label} | {inflow_cell} | {outflow_cell} |")

    lines += [
        "",
        "说明：资金流为同花顺统计口径；仅作描述性展示，不构成买卖信号。",
    ]
    return "\n".join(lines)
