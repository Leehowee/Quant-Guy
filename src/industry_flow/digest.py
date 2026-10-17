from __future__ import annotations

import pandas as pd

from .market_snapshot import build_market_report_summary


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
    market_snapshot: pd.DataFrame | None = None,
    trade_date=None,
    market_unavailable_reason: str | None = None,
) -> str:
    """Build one concise DingTalk message covering every stored THS horizon."""
    if feature_df.empty:
        return "【行业资金流日报】暂无可用数据。"

    df = feature_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    latest_date = df["trade_date"].max()
    report_date = trade_date or latest_date.date()
    latest = df[df["trade_date"] == latest_date]
    market = build_market_report_summary(
        market_snapshot,
        report_date,
        unavailable_reason=market_unavailable_reason,
    )
    periods = ["即时", "3日", "5日", "10日", "20日"]
    lines = [
        f"# 同花顺行业资金流日报 {latest_date:%Y-%m-%d}",
        "净额单位：亿元；涨跌幅单位：%。榜单按净额排序。",
        "",
        "### 短线市场环境过滤（仅作规则观察）",
        market["status"],
        market["breadth"],
        market["trend"],
        f"数据说明：{market['note']}",
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


def build_sw_industry_flow_digest(
    feature_df: pd.DataFrame,
    top_n: int = 5,
    market_snapshot: pd.DataFrame | None = None,
    trade_date=None,
    market_unavailable_reason: str | None = None,
) -> str:
    """Render rolling full-market stock-flow sums grouped by SW Level 2."""
    if feature_df.empty:
        return "【申万二级行业资金流日报】暂无满足覆盖率要求的数据。"

    df = feature_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    latest_date = df["trade_date"].max()
    report_date = trade_date or latest_date.date()
    latest = df.loc[df["trade_date"].eq(latest_date)].copy()
    market = build_market_report_summary(
        market_snapshot,
        report_date,
        unavailable_reason=market_unavailable_reason,
    )
    periods = ["1日", "3日", "5日", "10日", "20日"]
    market_coverage = pd.to_numeric(
        latest.get("market_coverage_pct", pd.Series(dtype=float)), errors="coerce"
    ).dropna()
    mapping_coverage = pd.to_numeric(
        latest.get("mapping_coverage_pct", pd.Series(dtype=float)), errors="coerce"
    ).dropna()
    coverage_note = ""
    if not market_coverage.empty and not mapping_coverage.empty:
        coverage_note = (
            f"当日全市场个股资金流覆盖 {market_coverage.iloc[0]:.1f}%；"
            f"申万二级映射覆盖 {mapping_coverage.iloc[0]:.1f}%。"
        )

    lines = [
        f"# 申万二级行业 · 沪深A股资金流日报 {latest_date:%Y-%m-%d}",
        "净额单位：亿元；1/3/5/10/20日均为最近相应交易日的净流入累计值。",
        "",
        "### 短线市场环境过滤（仅作规则观察）",
        market["status"],
        market["breadth"],
        market["trend"],
        f"数据说明：{market['note']}",
        "",
        "| 周期 | 净流入前{n} | 净流出前{n} |".format(n=top_n),
        "|---|---|---|",
    ]

    for period in periods:
        rows = latest.loc[latest["period"].astype(str).eq(period)].copy()
        if rows.empty:
            lines.append(f"| {period}累计 | 历史窗口或行业覆盖不足 | 历史窗口或行业覆盖不足 |")
            continue
        rows["net_amount_yi"] = pd.to_numeric(rows["net_amount_yi"], errors="coerce")
        inflow = rows.nlargest(top_n, "net_amount_yi")
        outflow = rows.nsmallest(top_n, "net_amount_yi")

        def render(row, rank: int) -> str:
            amount = float(row.net_amount_yi)
            coverage = getattr(row, "coverage_pct", pd.NA)
            stocks = getattr(row, "flow_stock_count", pd.NA)
            suffix = ""
            if pd.notna(coverage):
                suffix = f"（覆盖{float(coverage):.0f}%"
                if pd.notna(stocks):
                    suffix += f"，{int(stocks)}条股票日记录"
                suffix += "）"
            return f"{rank}. {row.industry_name} {amount:+.2f}亿{suffix}"

        inflow_cell = "<br>".join(
            render(row, rank)
            for rank, row in enumerate(inflow.itertuples(index=False), 1)
        )
        outflow_cell = "<br>".join(
            render(row, rank)
            for rank, row in enumerate(outflow.itertuples(index=False), 1)
        )
        lines.append(f"| {period}累计 | {inflow_cell} | {outflow_cell} |")

    lines += [
        "",
        coverage_note,
        "覆盖范围：Tushare接口文档中的沪深A股；北交所未纳入。个股L2主动买卖净流入按"
        "申万二级成分有效日期归集，不代表机构或特定账户持仓，也不等于行业指数表现。",
        "数据不足的行业或周期不参与排名；以上为描述性统计，不构成买卖信号。",
    ]
    return "\n".join(line for line in lines if line is not None)
