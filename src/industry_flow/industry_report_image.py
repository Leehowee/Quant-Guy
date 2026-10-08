from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


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


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        [Path("C:/Windows/Fonts/msyhbd.ttc"), Path("C:/Windows/Fonts/msyh.ttc")]
        if bold
        else [Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/msyhbd.ttc")]
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


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
        change = _pct(getattr(row, change_column, None))
        draw.text(
            (x + 28, current_y + 22),
            f"净额 {amount}    涨跌幅 {change}",
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
) -> Path:
    """Render THS industry fund-flow rankings as a DingTalk-ready long image."""
    if feature_df.empty:
        raise ValueError("Cannot render an industry report from an empty feature table")
    required = {"trade_date", "period", "industry_name", "net_amount_yi"}
    missing = sorted(required - set(feature_df.columns))
    if missing:
        raise ValueError(f"Missing columns for industry image: {missing}")

    data = feature_df.copy()
    data["trade_date"] = pd.to_datetime(data["trade_date"], errors="coerce").dt.date
    day = data[data["trade_date"] == trade_date]

    margin = 32
    gutter = 18
    header_height = 208
    section_height = 342
    footer_height = 122
    panel_width = (WIDTH - margin * 2 - gutter) // 2
    height = header_height + section_height * len(PERIODS) + footer_height
    image = Image.new("RGB", (WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((margin, 28, WIDTH - margin, 184), radius=28, fill="#17365D")
    draw.text((66, 48), "行业资金流日报", font=_font(46, True), fill="#FFFFFF")
    draw.text((68, 110), f"数据日期  {trade_date:%Y-%m-%d}", font=_font(25), fill="#D9E7F7")
    draw.text(
        (68, 151),
        "同花顺行业资金流统计  ·  净额单位：亿元  /  涨跌幅单位：%",
        font=_font(18),
        fill="#BFD0E5",
    )

    for index, period in enumerate(PERIODS):
        section_y = header_height + index * section_height
        period_rows = day[day["period"].astype(str) == period].copy()
        note = f"{period_rows['industry_name'].nunique()} 个行业 · 按净额排序" if not period_rows.empty else "本周期数据缺失"
        draw.rounded_rectangle(
            (margin, section_y, WIDTH - margin, section_y + 48),
            radius=14,
            fill="#E8EEF7",
        )
        label = "今日" if period == "即时" else period
        draw.text((margin + 22, section_y + 7), label, font=_font(25, True), fill=BLUE)
        draw.text((margin + 125, section_y + 15), note, font=_font(16), fill="#98620F")

        panel_y = section_y + 58
        panel_height = section_height - 68
        if not period_rows.empty:
            period_rows["net_amount_yi"] = pd.to_numeric(
                period_rows["net_amount_yi"], errors="coerce"
            )
            change_column = "change_pct" if period == "即时" else "period_change_pct"
            if change_column not in period_rows.columns:
                period_rows[change_column] = pd.NA
            inflow = period_rows.nlargest(top_n, "net_amount_yi")
            outflow = period_rows.nsmallest(top_n, "net_amount_yi")
        else:
            change_column = "change_pct" if period == "即时" else "period_change_pct"
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

    footer_y = header_height + section_height * len(PERIODS) + 20
    draw.text(
        (48, footer_y),
        "资金净额及行业涨跌幅按同花顺接口口径展示。",
        font=_font(17),
        fill=MUTED,
    )
    draw.text(
        (48, footer_y + 31),
        "仅作行业资金流描述性统计，不构成买卖信号。",
        font=_font(17),
        fill=MUTED,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)
    return output_path
