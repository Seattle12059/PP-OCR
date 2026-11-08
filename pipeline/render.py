"""阶段 ③：把带坐标的 OCR 结果还原成白底 HTML。

思路：每页一个 SVG，画布坐标 = 原图像素坐标（靠 viewBox 自适应缩放）。
每个文字行一个 <text>，行内每个字/词一个 <tspan>，用 textLength 把字宽
强制锁回原图检测框的宽度，避免浏览器字体宽度差异造成的错位。
"""

from __future__ import annotations

import statistics
from html import escape
from pathlib import Path
from typing import Any

FONT_STACK = '"PingFang SC", "Hiragino Sans GB", "Songti SC", sans-serif'
LOW_SCORE = 0.5
LOW_COLOR = "#c62828"


# ---------------------------------------------------------------- 排序


def _center_y(line: dict) -> float:
    box = line["box"]
    return (box[1] + box[3]) / 2


def _box_height(line: dict) -> float:
    box = line["box"]
    return max(1.0, box[3] - box[1])


def order_lines(lines: list[dict]) -> list[dict]:
    """按阅读顺序排：先按竖直中心聚成行，行内按 x，行间按 y。"""
    usable = [ln for ln in lines if ln.get("box")]
    if not usable:
        return []

    med_h = statistics.median(_box_height(ln) for ln in usable)
    tol = max(3.0, med_h * 0.55)

    rows: list[dict] = []
    for line in sorted(usable, key=_center_y):
        cy = _center_y(line)
        target = None
        for row in rows:
            if abs(cy - row["cy"]) <= tol:
                target = row
                break
        if target is None:
            rows.append({"cy": cy, "items": [line]})
        else:
            target["items"].append(line)
            target["cy"] = statistics.mean(_center_y(x) for x in target["items"])

    ordered: list[dict] = []
    for row in sorted(rows, key=lambda r: r["cy"]):
        ordered.extend(sorted(row["items"], key=lambda ln: ln["box"][0]))
    return ordered


# ---------------------------------------------------------------- 颜色采样


class ColorSampler:
    """从原图对应区域采样文字颜色，保留彩色标题。"""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def _image(self, path: str):
        if path not in self._cache:
            from PIL import Image

            img = Image.open(path).convert("RGB")
            self._cache[path] = img
        return self._cache[path]

    def sample(self, page: dict, box: list[float]) -> str:
        import numpy as np

        try:
            img = self._image(page["image"])
        except Exception:
            return "#000000"
        x1, y1, x2, y2 = (max(0, int(v)) for v in box)
        if x2 <= x1 or y2 <= y1:
            return "#000000"
        crop = np.asarray(img.crop((x1, y1, x2, y2)))
        if crop.size == 0:
            return "#000000"
        flat = crop.reshape(-1, 3).astype(np.int16)
        lum = flat @ np.array([0.299, 0.587, 0.114])
        if lum.min() > 200:  # 整块都是浅色，没有文字像素
            return "#000000"
        thresh = np.percentile(lum, 10)
        dark = flat[lum <= max(thresh, lum.min() + 1)]
        if dark.size == 0:
            return "#000000"
        r, g, b = (int(v) for v in np.median(dark, axis=0))
        return f"#{r:02x}{g:02x}{b:02x}"

    def close(self) -> None:
        for img in self._cache.values():
            try:
                img.close()
            except Exception:
                pass
        self._cache.clear()


# ---------------------------------------------------------------- SVG 生成


def _is_ascii(text: str) -> bool:
    return bool(text) and all(ord(ch) < 128 for ch in text)


def _natural_width(token: str, font_size: float) -> float:
    """粗估 ASCII 文本在给定字号下的自然宽度，用于判断会不会溢出检测框。"""
    ratio = 0.55
    if all(ch.isdigit() for ch in token):
        ratio = 0.56
    elif token.isupper():
        ratio = 0.66
    return len(token) * ratio * font_size


def estimate_font_size(line: dict, k: float) -> float:
    """估该行的字号。

    检测框经过 unclip 外扩，框高通常比真实字高大 40%~50%，直接拿框高当
    字号会明显偏大。中文是全宽方块字，"相邻两字的起点差" 等于字前进宽度
    （= 字号），比框宽更准；没有中文时退回 框高 × k。
    """
    box = line["box"]
    height = max(1.0, box[3] - box[1])
    segments = line.get("segments") or []

    cjk = [s for s in segments if s.get("box") and not _is_ascii(str(s.get("t", "")))]
    if len(cjk) >= 2:
        starts = sorted(s["box"][0] for s in cjk)
        advances = [b - a for a, b in zip(starts, starts[1:]) if b - a > 0]
        if advances:
            adv = statistics.median(advances)
            return min(adv, height)
    return height * k


def _rotation(line: dict) -> tuple[float, float, float] | None:
    poly = line.get("poly")
    if not poly or len(poly) < 2:
        return None
    import math

    dx = poly[1][0] - poly[0][0]
    dy = poly[1][1] - poly[0][1]
    angle = math.degrees(math.atan2(dy, dx))
    if abs(angle) < 1.0:
        return None
    box = line["box"]
    return angle, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _line_svg(
    line: dict,
    opts: dict,
    sampler: ColorSampler | None,
    page: dict,
) -> str:
    box = line["box"]
    font_size = max(1.0, estimate_font_size(line, opts["font_k"]))
    fill = "#000000"
    if sampler is not None:
        fill = sampler.sample(page, box)

    score = line.get("score")
    if opts["mark_low"] and isinstance(score, (int, float)) and score < LOW_SCORE:
        fill = LOW_COLOR

    width = box[2] - box[0]
    segments = [s for s in line.get("segments") or [] if s.get("box")]
    text = line.get("text") or ""

    attrs = [
        f'font-size="{font_size:.2f}"',
        f'fill="{fill}"',
        'font-family=\'%s\'' % FONT_STACK,
    ]

    if segments:
        parts = []
        for seg in segments:
            sb = seg["box"]
            sw = max(0.5, sb[2] - sb[0])
            token = str(seg["t"])
            if _is_ascii(token) and _natural_width(token, font_size) <= sw * 1.02:
                # 拉丁字母/数字的自然字宽和检测框吻合时不要设 textLength，
                # 否则 2、A 这类窄字形会被压扁。
                parts.append(
                    f'<tspan x="{sb[0]:.1f}" y="{sb[3]:.1f}">{escape(token)}</tspan>'
                )
            else:
                # 中文是全宽方块字，浏览器算出的字宽常与检测框差几像素，
                # 逐字累积会明显错位。spacingAndGlyphs 把字形横向缩放到框宽。
                parts.append(
                    f'<tspan x="{sb[0]:.1f}" y="{sb[3]:.1f}" '
                    f'textLength="{sw:.1f}" lengthAdjust="spacingAndGlyphs">'
                    f"{escape(token)}</tspan>"
                )
        body = "".join(parts)
        anchor = f'x="{box[0]:.1f}" y="{box[3]:.1f}"'
    else:
        body = escape(text)
        anchor = (
            f'x="{box[0]:.1f}" y="{box[3]:.1f}" textLength="{max(0.5, width):.1f}" '
            'lengthAdjust="spacingAndGlyphs"'
        )

    rot = _rotation(line)
    if rot:
        angle, cx, cy = rot
        return (
            f'<g transform="rotate({angle:.2f} {cx:.1f} {cy:.1f})">'
            f"<text {anchor} {' '.join(attrs)}>{body}</text></g>"
        )
    return f"<text {anchor} {' '.join(attrs)}>{body}</text>"


def _page_svg(page: dict, opts: dict, sampler: ColorSampler | None) -> str:
    width, height = page["width"], page["height"]
    lines = order_lines(page.get("lines") or [])
    body = "\n".join(_line_svg(ln, opts, sampler, page) for ln in lines)
    return (
        f'<svg class="page-svg" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'preserveAspectRatio="xMidYMid meet">\n'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>\n'
        f"{body}\n</svg>"
    )


# ---------------------------------------------------------------- 文档组装


def render_html(
    pages: list[dict],
    title: str,
    *,
    font_k: float = 0.78,
    mark_low: bool = False,
    color: bool = False,
) -> str:
    opts = {"font_k": font_k, "mark_low": mark_low}
    sampler = ColorSampler() if color else None
    try:
        sections = []
        for page in pages:
            svg = _page_svg(page, opts, sampler)
            sections.append(
                f'<section class="page" data-page="{page["page"]}">\n{svg}\n</section>'
            )
    finally:
        if sampler is not None:
            sampler.close()

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
  html, body {{
    margin: 0;
    padding: 0;
    background: #e9ecef;
    font-family: {FONT_STACK};
  }}
  .page {{
    background: #fff;
    margin: 16px auto;
    max-width: 1100px;
    box-shadow: 0 1px 6px rgba(0, 0, 0, 0.18);
    page-break-after: always;
    break-after: page;
  }}
  .page:last-child {{ page-break-after: auto; break-after: auto; }}
  .page-svg {{ display: block; width: 100%; height: auto; }}
  @media print {{
    html, body {{ background: #fff; }}
    .page {{ margin: 0; max-width: none; box-shadow: none; }}
    @page {{ size: auto; margin: 0; }}
  }}
</style>
</head>
<body>
{chr(10).join(sections)}
</body>
</html>
"""


def page_text(page: dict) -> str:
    return "\n".join(ln["text"] for ln in order_lines(page.get("lines") or []))


def write_outputs(
    pages: list[dict],
    out_dir: Path,
    base: str,
    *,
    font_k: float = 0.78,
    mark_low: bool = False,
    color: bool = False,
) -> dict[str, Path]:
    """写出 HTML / JSON / TXT。base 是原文件名（含扩展名），如 `报告.pdf`。"""
    import json

    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{base}.html"
    json_path = out_dir / f"{base}.json"
    txt_path = out_dir / f"{base}.txt"

    html_path.write_text(
        render_html(pages, base, font_k=font_k, mark_low=mark_low, color=color),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(pages, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    txt_path.write_text(
        "\n\n".join(page_text(p) for p in pages) + "\n", encoding="utf-8"
    )
    return {"html": html_path, "json": json_path, "txt": txt_path}
