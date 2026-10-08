from __future__ import annotations

import os
import platform
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont


def _font_candidates(bold: bool) -> list[Path]:
    bold_override = os.getenv("REPORT_FONT_BOLD", "").strip()
    regular_override = os.getenv("REPORT_FONT", "").strip()
    candidates: list[Path] = []

    if bold:
        if bold_override:
            candidates.append(Path(bold_override).expanduser())
    elif regular_override:
        candidates.append(Path(regular_override).expanduser())

    system = platform.system()
    if system == "Windows":
        names = (
            ["msyhbd.ttc", "simhei.ttf", "simsun.ttc", "msyh.ttc"]
            if bold
            else ["msyh.ttc", "simsun.ttc", "simhei.ttf", "msyhbd.ttc"]
        )
        candidates.extend(Path("C:/Windows/Fonts") / name for name in names)
    elif system == "Darwin":
        candidates.extend(
            [
                Path("/System/Library/Fonts/PingFang.ttc"),
                Path("/System/Library/Fonts/STHeiti Medium.ttc"),
                Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
                Path("/Library/Fonts/Arial Unicode.ttf"),
            ]
        )
    else:
        names = (
            [
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                "/usr/share/fonts/truetype/arphic/ukai.ttc",
            ]
            if bold
            else [
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                "/usr/share/fonts/truetype/arphic/ukai.ttc",
            ]
        )
        candidates.extend(Path(name) for name in names)
        user_fonts = Path.home() / ".local" / "share" / "fonts"
        candidates.extend(user_fonts.glob("NotoSansCJK*.ttc"))

    if bold and regular_override:
        candidates.append(Path(regular_override).expanduser())

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


@lru_cache(maxsize=64)
def load_report_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in _font_candidates(bold):
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue

    raise RuntimeError(
        "No usable Chinese TrueType font was found for PNG reports. "
        "Install a CJK font (for example fonts-noto-cjk on Linux) or set "
        "REPORT_FONT and optionally REPORT_FONT_BOLD in the project .env."
    )
