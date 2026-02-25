from __future__ import annotations

import base64
import io
import os
import re
from pathlib import Path
from typing import Any, Dict, List
import xml.etree.ElementTree as ET

from loguru import logger
from PIL import Image, ImageDraw, ImageFont

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.terminal_theme import SVG_EXPORT_THEME

    _HAS_RICH = True
except Exception:
    _HAS_RICH = False

try:
    import cairosvg

    _HAS_CAIROSVG = True
except Exception:
    _HAS_CAIROSVG = False


_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

_MONO_FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/Library/Fonts/JetBrainsMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]

_CODE_FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_MAX_RENDER_TEXT_LINES = 360

_ASCII_MONO_FONT_STACK = (
    '"Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono", monospace'
)
_CJK_FONT_STACK = (
    '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI", "Microsoft YaHei", '
    '"DengXian", "Noto Sans CJK SC", "WenQuanYi Micro Hei", "Source Han Sans SC", '
    '"Arial Unicode MS", "DejaVu Sans", sans-serif'
)


if os.name == "nt":
    win_fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    _FONT_CANDIDATES.extend(
        [
            str(win_fonts_dir / "msyh.ttc"),  # Microsoft YaHei
            str(win_fonts_dir / "msyhl.ttc"),  # Microsoft YaHei Light
            str(win_fonts_dir / "simsun.ttc"),  # SimSun
            str(win_fonts_dir / "simhei.ttf"),  # SimHei
            str(win_fonts_dir / "Deng.ttf"),  # DengXian
            str(win_fonts_dir / "arialuni.ttf"),  # Arial Unicode MS
        ]
    )
    _MONO_FONT_CANDIDATES.extend(
        [
            str(win_fonts_dir / "CascadiaMono.ttf"),
            str(win_fonts_dir / "CascadiaCode.ttf"),
            str(win_fonts_dir / "consola.ttf"),  # Consolas
            str(win_fonts_dir / "cour.ttf"),  # Courier New
            str(win_fonts_dir / "lucon.ttf"),  # Lucida Console
        ]
    )


def _safe_color(color: str) -> str:
    raw = (color or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", raw):
        return raw
    if re.fullmatch(r"[0-9a-fA-F]{6}", raw):
        return f"#{raw}"
    return "#ef4444"


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _load_mono_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _MONO_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return _load_font(size)


def _strip_markdown(markdown: str) -> str:
    text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"```[^\n]*\n", "", text)
    text = text.replace("```", "")
    text = re.sub(r"!\[[^\]]*\]\([^\)]+\)", "[图片]", text)
    text = re.sub(r"\[([^\]]+)\]\(([^\)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)
    return text.strip()


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _patch_svg_text_fonts(svg: str) -> str:
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg

    ns = {"svg": "http://www.w3.org/2000/svg"}
    changed = False
    for node in root.findall(".//svg:text", ns):
        raw_text = node.text or ""
        if not raw_text:
            continue
        if not _contains_cjk(raw_text):
            continue

        style = (node.get("style") or "").strip()
        if style and not style.endswith(";"):
            style += ";"
        style += f"font-family: {_CJK_FONT_STACK};"
        node.set("style", style)

        # textLength assumes fixed cell width; for proportional CJK fonts this
        # causes spacing distortion, so remove it only on CJK-containing nodes.
        node.attrib.pop("textLength", None)

        # trim right-side padding fillers to avoid huge trailing gaps
        node.text = raw_text.rstrip("\u00a0 ")
        changed = True

    if not changed:
        return svg
    return ET.tostring(root, encoding="unicode")


def _estimate_console_width(markdown: str) -> int:
    text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.expandtabs(4) for ln in text.split("\n")]
    longest = max((len(ln) for ln in lines), default=0)
    return max(92, min(132, longest + 8))


def _split_markdown_sections(markdown: str) -> List[Dict[str, Any]]:
    text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    sections: List[Dict[str, Any]] = []
    cursor = 0
    for match in _CODE_FENCE_RE.finditer(text):
        if match.start() > cursor:
            sections.append({"type": "text", "content": text[cursor : match.start()]})
        lang = (match.group(1) or "").strip()
        code = (match.group(2) or "").rstrip("\n")
        sections.append({"type": "code", "lang": lang, "content": code})
        cursor = match.end()
    if cursor < len(text):
        sections.append({"type": "text", "content": text[cursor:]})
    if not sections:
        sections.append({"type": "text", "content": text})
    return sections


def _normalize_text_line(raw_line: str) -> str:
    line = raw_line.rstrip()
    if not line:
        return ""
    line = re.sub(r"^\s*#{1,6}\s*", "", line)
    line = re.sub(r"^\s*[-*+]\s+", "• ", line)
    line = re.sub(r"^\s*\d+\.\s+", "• ", line)
    return line.strip()


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    wrapped: List[str] = []
    for raw in text.split("\n"):
        line = raw.rstrip()
        if not line:
            wrapped.append("")
            continue

        current = ""
        for ch in line:
            candidate = current + ch
            if not current or draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                wrapped.append(current)
                current = ch
        if current:
            wrapped.append(current)
    return wrapped if wrapped else [""]


def _wrap_code_lines(code: str, max_cols: int) -> List[str]:
    max_cols = max(int(max_cols), 20)
    out: List[str] = []
    for raw in (code or "").split("\n"):
        line = raw.replace("\t", "    ").rstrip()
        if not line:
            out.append("")
            continue
        while len(line) > max_cols:
            out.append(line[:max_cols])
            line = line[max_cols:]
        out.append(line)
    return out if out else [""]


def _render_with_rich_svg(markdown: str, title: str, width: int) -> Image.Image:
    if not (_HAS_RICH and _HAS_CAIROSVG):
        raise RuntimeError("rich/cairosvg is not available")

    content = markdown or "(空内容)"
    has_cjk = _contains_cjk(content)
    console_width = _estimate_console_width(content)
    font_aspect_ratio = 0.5 if has_cjk else 0.56

    console = Console(record=True, width=console_width, file=io.StringIO())
    console.print(
        Markdown(
            content,
            code_theme="monokai",
            inline_code_theme="monokai",
        )
    )
    svg = console.export_svg(
        title=title,
        theme=SVG_EXPORT_THEME,
        font_aspect_ratio=font_aspect_ratio,
        code_format="""
<svg class="rich-terminal" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
    <style>
    .{unique_id}-matrix {{
        font-family: """ + _ASCII_MONO_FONT_STACK + """;
        font-size: {char_height}px;
        line-height: {line_height}px;
        font-variant-east-asian: normal;
        font-variant-ligatures: none;
    }}
    .{unique_id}-title {{
        font-size: 18px;
        font-weight: bold;
        font-family: """ + _CJK_FONT_STACK + """;
    }}
    {styles}
    </style>
    <defs>
    <clipPath id="{unique_id}-clip-terminal">
      <rect x="0" y="0" width="{terminal_width}" height="{terminal_height}" />
    </clipPath>
    {lines}
    </defs>
    {chrome}
    <g transform="translate({terminal_x}, {terminal_y})" clip-path="url(#{unique_id}-clip-terminal)">
    {backgrounds}
    <g class="{unique_id}-matrix">
    {matrix}
    </g>
    </g>
</svg>
""".strip(),
    )
    # Keep Rich's monospace metrics for ASCII symbols, and only switch CJK
    # nodes to CJK fonts to avoid missing glyphs and placeholder distortion.
    svg = _patch_svg_text_fonts(svg)
    png_bytes = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=width)
    return Image.open(io.BytesIO(png_bytes)).convert("RGB")


def _render_with_pillow_markdown(markdown: str, title: str, width: int, theme_color: str) -> Image.Image:
    outer_bg = "#f3f4f6"
    card_bg = "#ffffff"
    title_color = "#111827"
    body_color = "#1f2937"
    code_bg = "#111827"
    code_border = "#374151"
    code_text = "#e5e7eb"
    accent = _safe_color(theme_color)

    title_font = _load_font(40)
    body_font = _load_font(30)
    code_font = _load_mono_font(28)
    code_label_font = _load_font(22)

    card_margin = 24
    inner_padding = 36
    max_text_width = width - 2 * (card_margin + inner_padding)

    scratch = Image.new("RGB", (width, 2000), outer_bg)
    draw = ImageDraw.Draw(scratch)

    rendered_sections: List[Dict[str, Any]] = []
    rendered_line_count = 0
    truncated = False

    sections = _split_markdown_sections(markdown or "(空内容)")
    mono_char_w = max(int(draw.textlength("M", font=code_font)), 10)
    max_code_cols = max_text_width // mono_char_w

    for section in sections:
        if rendered_line_count >= _MAX_RENDER_TEXT_LINES:
            truncated = True
            break

        if section.get("type") == "code":
            code_lines = _wrap_code_lines(str(section.get("content", "")), max_code_cols)
            remain = _MAX_RENDER_TEXT_LINES - rendered_line_count
            if len(code_lines) > remain:
                code_lines = code_lines[:remain]
                truncated = True
            rendered_sections.append(
                {
                    "type": "code",
                    "lang": str(section.get("lang", "")).strip(),
                    "lines": code_lines if code_lines else [""],
                }
            )
            rendered_line_count += len(code_lines)
            continue

        text_content = str(section.get("content", ""))
        lines: List[str] = []
        for raw_line in text_content.split("\n"):
            line = _normalize_text_line(raw_line)
            wrapped = _wrap_lines(draw, line, body_font, max_text_width) if line else [""]
            for w in wrapped:
                if rendered_line_count >= _MAX_RENDER_TEXT_LINES:
                    truncated = True
                    break
                lines.append(w)
                rendered_line_count += 1
            if truncated:
                break
        if lines:
            rendered_sections.append({"type": "text", "lines": lines})

    if truncated:
        rendered_sections.append({"type": "text", "lines": ["", "… 内容过长，已截断显示"]})

    title_box = draw.textbbox((0, 0), title, font=title_font)
    title_h = title_box[3] - title_box[1]
    body_line_h = max(int(body_font.size * 1.45), 34)
    code_line_h = max(int(code_font.size * 1.4), 34)

    content_h = 0
    for section in rendered_sections:
        if section["type"] == "text":
            content_h += len(section["lines"]) * body_line_h
        else:
            code_lines = section["lines"]
            block_h = 24 + 20 + len(code_lines) * code_line_h + 24
            if section.get("lang"):
                block_h += 30
            content_h += block_h
        content_h += 16

    height = 2 * card_margin + inner_padding + title_h + 20 + max(content_h, body_line_h) + inner_padding
    image = Image.new("RGB", (width, height), outer_bg)
    draw = ImageDraw.Draw(image)

    card_left = card_margin
    card_top = card_margin
    card_right = width - card_margin
    card_bottom = height - card_margin
    draw.rounded_rectangle((card_left, card_top, card_right, card_bottom), radius=18, fill=card_bg)
    draw.rectangle((card_left, card_top, card_right, card_top + 10), fill=accent)

    x = card_left + inner_padding
    y = card_top + inner_padding
    draw.text((x, y), title, fill=title_color, font=title_font)
    y += title_h + 20

    for section in rendered_sections:
        if section["type"] == "text":
            for line in section["lines"]:
                draw.text((x, y), line if line else " ", fill=body_color, font=body_font)
                y += body_line_h
            y += 16
            continue

        code_lines: List[str] = section["lines"]
        lang = str(section.get("lang", "")).strip()
        block_x1 = x
        block_x2 = x + max_text_width
        block_y1 = y
        block_h = 24 + len(code_lines) * code_line_h + 24 + 20
        if lang:
            block_h += 30
        block_y2 = block_y1 + block_h

        draw.rounded_rectangle((block_x1, block_y1, block_x2, block_y2), radius=12, fill=code_bg, outline=code_border, width=2)
        code_x = block_x1 + 18
        code_y = block_y1 + 18
        if lang:
            draw.text((code_x, code_y), f"[{lang}]", fill="#9ca3af", font=code_label_font)
            code_y += 30
        for line in code_lines:
            draw.text((code_x, code_y), line if line else " ", fill=code_text, font=code_font)
            code_y += code_line_h
        y = block_y2 + 16

    return image


def render_markdown_to_base64(
    markdown: str,
    title: str = "Codex",
    theme_color: str = "#ef4444",
    model_name: str | None = None,
    cwd_hint: str | None = None,
) -> str:
    _ = model_name, cwd_hint
    width = 1800
    content = (markdown or "(空内容)").rstrip("\n") + "\n\n"

    try:
        image = _render_with_rich_svg(content, title=title, width=width)
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=97, subsampling=0, optimize=True, progressive=True)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as exc:
        logger.debug(f"rich svg render unavailable, fallback to pillow: {exc}")

    image = _render_with_pillow_markdown(content, title=title, width=width, theme_color=theme_color)

    buf = io.BytesIO()
    try:
        image.save(buf, format="JPEG", quality=97, subsampling=0, optimize=True, progressive=True)
    except Exception as exc:
        logger.warning(f"simple render JPEG optimize failed, fallback default save: {exc}")
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=95, subsampling=0)

    return base64.b64encode(buf.getvalue()).decode("utf-8")
