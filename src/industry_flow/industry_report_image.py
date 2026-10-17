from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from .fonts import load_report_font as _font
from .market_snapshot import build_market_report_summary


WIDTH = 1200
BACKGROUND = "#F4F7FB"
INK = "#172B4D"
MUTED = "#64748B"
INFLOW_RED = "#C44949"
OUTFLOW_GREEN = "#16845B"
BLUE = "#315E9B"
CARD = "#FFFFFF"
LINE = "#E6EBF2"
PERIODS = ("即时", "3日", "5日", "10日", "20日")
SW_PERIODS = ("1日", "3日", "5日", "10日", "20日")


def _present(value: Any) -> bool:
    if value is None:
        return False
    try:
        return not bool(pd.isna(value))
    except (TypeError, ValueError):
        return True


def _amount(value: Any) -> str:
    return f"{float(value):+.2f}亿元" if _present(value) else "—"


def _pct(value: Any) -> str:
    return f"{float(value):+.2f}%" if _present(value) else "—"


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    width: int,
) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _draw_rank_card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    rows: pd.DataFrame,
    accent: str,
    change_column: str,
    top_n: int,
) -> None:
    x, y, right, bottom = box
    width = right - x
    draw.rounded_rectangle(box, radius=20, fill=CARD, outline=LINE, width=2)
    draw.rounded_rectangle((x + 18, y + 17, x + 25, y + 51), radius=4, fill=accent)
    draw.text((x + 40, y + 15), title, font=_font(25, True), fill=INK)

    if rows.empty:
        draw.text((x + 28, y + 91), "暂无有效数据", font=_font(21), fill=MUTED)
        return

    row_top = y + 61
    row_height = 43
    for rank, row in enumerate(rows.head(top_n).itertuples(index=False), start=1):
        current_y = row_top + (rank - 1) * row_height
        name = _fit_text(
            draw,
            f"{rank}.  {row.industry_name}",
            _font(20, True),
            width - 56,
        )
        draw.text((x + 28, current_y), name, font=_font(20, True), fill=INK)
        amount = _amount(row.net_amount_yi)
        if "coverage_pct" in row._fields:
            coverage = getattr(row, "coverage_pct", None)
            stock_count = getattr(row, "flow_stock_count", None)
            coverage_text = (
                f"覆盖 {float(coverage):.0f}%"
                if _present(coverage)
                else "覆盖率未知"
            )
            if _present(stock_count):
                coverage_text += f" · {int(stock_count)} 条股票日记录"
            detail = f"净额 {amount}    {coverage_text}"
        else:
            change = _pct(getattr(row, change_column, None))
            detail = f"净额 {amount}    涨跌幅 {change}"
        draw.text(
            (x + 28, current_y + 22),
            detail,
            font=_font(15),
            fill=accent,
        )
        if rank < min(len(rows), top_n):
            draw.line(
                (x + 28, current_y + 41, right - 28, current_y + 41),
                fill=LINE,
                width=1,
            )


def render_industry_report_png(
    feature_df: pd.DataFrame,
    trade_date: dt.date,
    output_path: Path,
    top_n: int = 5,
    market_snapshot: pd.DataFrame | None = None,
    market_unavailable_reason: str | None = None,
) -> Path:
    """Render market context and industry flow rankings as a DingTalk-ready image."""
    if feature_df.empty:
        raise ValueError("Cannot render an industry report from an empty feature table")
    required = {"trade_date", "period", "industry_name", "net_amount_yi"}
    missing = sorted(required - set(feature_df.columns))
    if missing:
        raise ValueError(f"Missing columns for industry image: {missing}")

    data = feature_df.copy()
    is_sw_l2 = (
        "source" in data.columns
        and data["source"].astype(str).eq("tushare_moneyflow_sw_l2").any()
    )
    periods = SW_PERIODS if is_sw_l2 else PERIODS
    data["trade_date"] = pd.to_datetime(data["trade_date"], errors="coerce").dt.date
    day = data[data["trade_date"] == trade_date]
    market = build_market_report_summary(
        market_snapshot,
        trade_date,
        unavailable_reason=market_unavailable_reason,
    )

    margin = 32
    gutter = 18
    sections_y = 314
    section_height = 342
    footer_height = 122
    panel_width = (WIDTH - margin * 2 - gutter) // 2
    height = sections_y + section_height * len(periods) + footer_height
    image = Image.new("RGB", (WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((margin, 28, WIDTH - margin, 184), radius=28, fill="#17365D")
    report_title = "申万二级行业 · 沪深A股资金流日报" if is_sw_l2 else "行业资金流日报"
    subtitle = (
        "沪深A股个股L2资金流按申万二级归集  ·  滚动交易日累计  ·  单位：亿元"
        if is_sw_l2
        else "同花顺行业资金流统计  ·  净额单位：亿元  /  涨跌幅单位：%"
    )
    if is_sw_l2 and not day.empty:
        market_cov = pd.to_numeric(day.get("market_coverage_pct"), errors="coerce").dropna()
        mapping_cov = pd.to_numeric(day.get("mapping_coverage_pct"), errors="coerce").dropna()
        if not market_cov.empty and not mapping_cov.empty:
            subtitle += (
                f"  ·  个股数据覆盖{market_cov.iloc[0]:.1f}%"
                f"  ·  行业映射覆盖{mapping_cov.iloc[0]:.1f}%"
            )
    draw.text((66, 48), report_title, font=_font(42 if is_sw_l2 else 46, True), fill="#FFFFFF")
    draw.text((68, 110), f"数据日期  {trade_date:%Y-%m-%d}", font=_font(25), fill="#D9E7F7")
    draw.text(
        (68, 151),
        subtitle,
        font=_font(18),
        fill="#BFD0E5",
    )

    market_box = (margin, 200, WIDTH - margin, 298)
    draw.rounded_rectangle(market_box, radius=18, fill=CARD, outline=LINE, width=2)
    market_width = WIDTH - margin * 2 - 48
    draw.text(
        (margin + 22, 207),
        "短线市场环境过滤（仅作规则观察）",
        font=_font(18, True),
        fill=BLUE,
    )
    draw.text(
        (margin + 22, 231),
        _fit_text(draw, market["status"], _font(17, True), market_width),
        font=_font(17, True),
        fill=INK,
    )
    draw.text(
        (margin + 22, 255),
        _fit_text(
            draw,
            f"{market['breadth']}  |  {market['trend']}",
            _font(15),
            market_width,
        ),
        font=_font(15),
        fill=INK,
    )
    draw.text(
        (margin + 22, 278),
        _fit_text(draw, market["note"], _font(12), market_width),
        font=_font(12),
        fill=MUTED,
    )

    for index, period in enumerate(periods):
        section_y = sections_y + index * section_height
        period_rows = day[day["period"].astype(str) == period].copy()
        if period_rows.empty:
            note = "历史窗口或行业覆盖不足" if is_sw_l2 else "本周期数据缺失"
        elif is_sw_l2:
            note = f"{period_rows['industry_name'].nunique()} 个申万二级行业 · 仅纳入覆盖达标项"
        else:
            note = f"{period_rows['industry_name'].nunique()} 个行业 · 按净额排序"
        draw.rounded_rectangle(
            (margin, section_y, WIDTH - margin, section_y + 48),
            radius=14,
            fill="#E8EEF7",
        )
        label = f"{period}累计" if is_sw_l2 else ("今日" if period == "即时" else period)
        draw.text((margin + 22, section_y + 7), label, font=_font(25, True), fill=BLUE)
        draw.text((margin + 125, section_y + 15), note, font=_font(16), fill="#98620F")

        panel_y = section_y + 58
        panel_height = section_height - 68
        if not period_rows.empty:
            period_rows["net_amount_yi"] = pd.to_numeric(
                period_rows["net_amount_yi"], errors="coerce"
            )
            change_column = None if is_sw_l2 else (
                "change_pct" if period == "即时" else "period_change_pct"
            )
            if change_column and change_column not in period_rows.columns:
                period_rows[change_column] = pd.NA
            inflow = period_rows.nlargest(top_n, "net_amount_yi")
            outflow = period_rows.nsmallest(top_n, "net_amount_yi")
        else:
            change_column = None if is_sw_l2 else (
                "change_pct" if period == "即时" else "period_change_pct"
            )
            inflow = period_rows
            outflow = period_rows

        _draw_rank_card(
            draw,
            (margin, panel_y, margin + panel_width, panel_y + panel_height),
            f"净流入 TOP {top_n}",
            inflow,
            INFLOW_RED,
            change_column,
            top_n,
        )
        _draw_rank_card(
            draw,
            (
                margin + panel_width + gutter,
                panel_y,
                WIDTH - margin,
                panel_y + panel_height,
            ),
            f"净流出 TOP {top_n}",
            outflow,
            OUTFLOW_GREEN,
            change_column,
            top_n,
        )

    footer_y = sections_y + section_height * len(periods) + 20
    draw.text(
        (48, footer_y),
        (
            "范围为Tushare接口提供的沪深A股，北交所未纳入；个股L2主动买卖净流入按申万二级成分有效日期归集。"
            if is_sw_l2
            else "资金净额及行业涨跌幅按同花顺接口口径展示。"
        ),
        font=_font(17),
        fill=MUTED,
    )
    draw.text(
        (48, footer_y + 31),
        (
            "成分覆盖不足项不参与排名；该口径不识别具体资金主体，也不代表行业指数涨跌。"
            if is_sw_l2
            else "仅作行业资金流描述性统计，不构成买卖信号。"
        ),
        font=_font(17),
        fill=MUTED,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)
    return output_path
