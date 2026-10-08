from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from industry_flow.fonts import load_report_font as _font


WIDTH = 1200
BACKGROUND = "#F4F7FB"
INK = "#172B4D"
MUTED = "#64748B"
GREEN = "#16845B"
RED = "#C44949"
BLUE = "#315E9B"
CARD = "#FFFFFF"
LINE = "#E6EBF2"


def _format_pct(value: Any) -> str:
    return f"{float(value):+.2f}%" if value is not None else "—"


def _format_amount(value: Any) -> str:
    return f"{float(value):+.2f} 亿" if value is not None else "—"


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _summary_card(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    heading: str,
    rows: list[dict[str, Any]],
    color: str,
    status: str | None,
) -> None:
    draw.rounded_rectangle((x, y, x + w, y + h), radius=20, fill=CARD, outline=LINE, width=2)
    draw.rounded_rectangle((x + 18, y + 17, x + 25, y + 51), radius=4, fill=color)
    draw.text((x + 40, y + 18), heading, font=_font(25, True), fill=INK)
    if status:
        draw.text((x + 28, y + 95), status, font=_font(24), fill=MUTED)
        return
    if not rows:
        draw.text((x + 28, y + 95), "暂无有效数据", font=_font(23), fill=MUTED)
        return

    row_top = y + 60
    row_height = 47
    for index, item in enumerate(rows[:5], start=1):
        current_y = row_top + (index - 1) * row_height
        name = f"{item.get('code', '')}  {item.get('name', '')}"
        name = _fit_text(draw, name, _font(20, True), w - 56)
        draw.text((x + 28, current_y), name, font=_font(20, True), fill=INK)
        detail = (
            f"净额 {_format_amount(item.get('flow_yi'))}   "
            f"涨跌 {_format_pct(item.get('change_pct'))}   "
            f"强度 {_format_pct(item.get('strength_pct'))}"
        )
        draw.text((x + 28, current_y + 21), detail, font=_font(15), fill=color)
        if index < min(len(rows), 5):
            draw.line((x + 28, current_y + 43, x + w - 28, current_y + 43), fill=LINE, width=1)


def render_etf_report_png(
    summaries: list[dict[str, Any]],
    trade_date: dt.date,
    output_path: Path,
) -> Path:
    section_height = 372
    header_height = 245
    footer_height = 132
    height = header_height + section_height * max(len(summaries), 1) + footer_height
    image = Image.new("RGB", (WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((32, 28, WIDTH - 32, 207), radius=28, fill="#17365D")
    draw.text((66, 57), "ETF 资金流日报", font=_font(48, True), fill="#FFFFFF")
    draw.text((68, 125), f"数据日期  {trade_date:%Y-%m-%d}", font=_font(25), fill="#D9E7F7")
    draw.text(
        (68, 165),
        "已剔除债券类 ETF 与上交所货币 ETF  ·  单位：亿元 / %",
        font=_font(19),
        fill="#BFD0E5",
    )

    margin = 32
    gutter = 18
    panel_w = (WIDTH - margin * 2 - gutter) // 2
    for index, summary in enumerate(summaries):
        top = header_height + index * section_height
        label = str(summary.get("period", ""))
        draw.rounded_rectangle((margin, top, WIDTH - margin, top + 54), radius=15, fill="#E8EEF7")
        draw.text((margin + 22, top + 10), label, font=_font(25, True), fill=BLUE)
        note = summary.get("note")
        if note:
            note_text = _fit_text(draw, str(note), _font(16), WIDTH - margin * 2 - 145)
            draw.text((margin + 125, top + 17), note_text, font=_font(16), fill="#98620F")
        panel_y = top + 64
        panel_h = section_height - 78
        status = summary.get("status")
        _summary_card(
            draw,
            margin,
            panel_y,
            panel_w,
            panel_h,
            "净流入 TOP 5",
            summary.get("inflow", []),
            GREEN,
            status,
        )
        _summary_card(
            draw,
            margin + panel_w + gutter,
            panel_y,
            panel_w,
            panel_h,
            "净流出 TOP 5",
            summary.get("outflow", []),
            RED,
            status,
        )

    footer_y = header_height + section_height * max(len(summaries), 1) + 18
    draw.text((48, footer_y), "申购强度 = 区间基金份额净增率；涨跌幅 = 区间价格涨跌幅。", font=_font(17), fill=MUTED)
    draw.text((48, footer_y + 29), "缺少部分 ETF 价格时，该周期净额按有价格的日期累计，可能影响排名。", font=_font(17), fill=MUTED)
    draw.text((48, footer_y + 67), "数据来源：基金份额与行情公开数据，净额为估算值；缺少数据以 — 表示。", font=_font(16), fill=MUTED)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)
    return output_path
