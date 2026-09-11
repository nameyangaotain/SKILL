#!/usr/bin/env python3
"""
compose.py -- Build the in-place bilingual PDF.

Layout produced
    One output page per source page, twice as wide as the original.
    LEFT  half: the original page, copied verbatim, pixel for pixel.
    RIGHT half: the same page, same layout, with every text block overwritten
                by its Chinese translation, fitted into the original rectangle.
    Figures are simply copied twice -- once inside each half -- and artwork
    labels stay in English on both sides, exactly like the source.

Why this instead of re-flowing
    Nothing moves. Columns, figure positions, captions, header and footer all
    keep their original coordinates, so the page count matches the source 1:1
    and the reader can compare line for line. The price is that a block whose
    translation is much shorter than its source leaves white space behind, and
    that a paragraph split by a column break is translated in two fragments.

Inputs
    blocks.json        from extract.py --profile inplace
    translations.json  {meta, verbatim, terms, blocks:{id: text}}

Output
    <name>_dual.pdf
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import fitz

# --------------------------------------------------------------------------
# fonts
# --------------------------------------------------------------------------

FONT_CANDIDATES = {
    "serif": [
        (r"C:\Windows\Fonts\simsun.ttc", 0),
        (r"C:\Windows\Fonts\simsun.ttf", 0),
        "/System/Library/Fonts/Supplemental/Songti.ttc", 0,
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 0,
        "/usr/share/fonts/truetype/arphic/uming.ttc", 0,
    ],
    "sans": [
        (r"C:\Windows\Fonts\simhei.ttf", 0),
        (r"C:\Windows\Fonts\msyh.ttc", 0),
        "/System/Library/Fonts/Supplemental/Songti.ttc", 0,
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0,
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0,
    ],
}
FALLBACK = {"serif": "china-s", "sans": "china-s"}


def load_font(kind: str):
    for path, index in FONT_CANDIDATES[kind]:
        if Path(path).exists():
            try:
                return fitz.Font(fontfile=path), path
            except Exception:
                continue
    try:
        return fitz.Font(FALLBACK[kind]), FALLBACK[kind]
    except Exception:
        return fitz.Font("cjk"), "cjk"


# --------------------------------------------------------------------------
# CJK-aware line breaking
# --------------------------------------------------------------------------

ATOM_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"       # CJK ideographs
    r"|[\u3000-\u303f\uff00-\uffef]"                     # CJK punctuation, fullwidth
    r"|[A-Za-z0-9][A-Za-z0-9'\u2019\-\./_@+]*"           # latin word / number
    r"|\s+"
    r"|."
)
NO_LINE_START = set("，。、；：？！）》】」』’”%,.;:?!)]}>")


def wrap_line(text: str, font, size: float, width: float) -> list[str]:
    tokens = ATOM_RE.findall(text)
    lines: list[str] = []
    cur = ""
    for tok in tokens:
        if tok.isspace():
            if cur and not cur.endswith(" "):
                cur += " "
            continue
        candidate = cur + tok
        if not cur.strip() or font.text_length(candidate, size) <= width:
            cur = candidate
        else:
            lines.append(cur.rstrip())
            cur = tok
    if cur.strip():
        lines.append(cur.rstrip())

    # never open a line with closing punctuation
    for i in range(1, len(lines)):
        while lines[i] and lines[i][0] in NO_LINE_START:
            lines[i - 1] += lines[i][0]
            lines[i] = lines[i][1:]
    return [ln for ln in lines if ln]


def fit_text(text: str, font, width: float, height: float, base_size: float,
             line_height: float, max_scale: float, min_scale: float):
    """Largest font size whose wrapped text still fits the source rectangle."""
    if width <= 1 or height <= 1:
        return None, []
    hi = base_size * max_scale
    lo = base_size * min_scale
    size = hi
    while size > lo:
        lines = wrap_line(text, font, size, width)
        if len(lines) * size * line_height <= height + 0.6:
            return size, lines
        size -= 0.2
    return lo, wrap_line(text, font, lo, width)


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

CLOSERS = ".,;:!?)]}\u2019\""


def clamp_erase(rect: fitz.Rect, zones: list[fitz.Rect], pad: float) -> fitz.Rect | None:
    """Erase rectangle for a block, shrunk so it never bites into a figure."""
    out = fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad)
    for z in zones:
        if out.x1 <= z.x0 or out.x0 >= z.x1:
            continue
        if out.y1 <= z.y0 or out.y0 >= z.y1:
            continue
        if out.y0 >= z.y0:               # block sits below the figure
            out.y0 = max(out.y0, z.y1 + 0.4)
        elif out.y1 <= z.y1:             # block sits above the figure
            out.y1 = min(out.y1, z.y0 - 0.4)
        else:                            # block is inside the figure: leave it
            return None
    if out.height <= 0.5 or out.width <= 0.5:
        return None
    return out


def sample_background(src_page: fitz.Page, rect: fitz.Rect) -> tuple[float, float, float]:
    """Most common pixel colour under a block, so erasing never leaves a white scar."""
    try:
        pix = src_page.get_pixmap(clip=rect, dpi=40)
    except Exception:
        return (1, 1, 1)
    if pix.width == 0 or pix.height == 0 or pix.n < 3:
        return (1, 1, 1)
    data = pix.samples
    step = pix.n
    counts: dict[tuple[int, int, int], int] = {}
    total = pix.width * pix.height
    stride = max(1, total // 400)
    for i in range(0, total, stride):
        o = i * step
        key = (data[o], data[o + 1], data[o + 2])
        counts[key] = counts.get(key, 0) + 1
    r, g, b = max(counts, key=counts.get)
    return (r / 255.0, g / 255.0, b / 255.0)


# --------------------------------------------------------------------------
# composition
# --------------------------------------------------------------------------

def compose(args) -> int:
    work = Path(args.workdir).expanduser().resolve()
    model = json.loads((work / "blocks.json").read_text(encoding="utf-8"))
    trdoc = json.loads((work / "translations.json").read_text(encoding="utf-8"))
    opts = trdoc.get("options", {}) or {}

    trans: dict[str, str] = trdoc.get("blocks", {}) or {}
    verbatim = set(trdoc.get("verbatim") or [])

    lh = float(opts.get("line_height", 1.42))
    max_scale = float(opts.get("max_scale", 1.10))
    min_scale = float(opts.get("min_scale", 0.72))
    gap = float(opts.get("gap", 0))
    divider = bool(opts.get("divider", True))
    keep_color = bool(opts.get("keep_heading_color", True))

    body_font, body_path = load_font("serif")
    head_font, head_path = load_font("sans")

    src = fitz.open(model["source"])
    out = fitz.open()

    by_page: dict[int, list[dict]] = {}
    for b in model["blocks"]:
        if b["kind"] == "figure":
            continue
        by_page.setdefault(b["page"], []).append(b)

    zones_by_page = model.get("figure_zones", {})

    stats = {"pages": 0, "replaced": 0, "skipped": 0, "shrunk": 0, "overflow": 0}

    for pno in range(src.page_count):
        page_no = pno + 1
        srect = src[pno].rect
        W, H = srect.width, srect.height
        pw = 2 * W + gap

        page = out.new_page(width=pw, height=H)
        page.show_pdf_page(fitz.Rect(0, 0, W, H), src, pno, overlay=True)
        page.show_pdf_page(fitz.Rect(W + gap, 0, pw, H), src, pno, overlay=True)

        if divider and gap == 0:
            page.draw_line(fitz.Point(W, 0), fitz.Point(W, H),
                           color=(0.86, 0.86, 0.86), width=0.45)

        zones = [fitz.Rect(z) for z in zones_by_page.get(str(page_no), [])]
        offset = W + gap

        blocks = by_page.get(page_no, [])

        # ---- pass 1: erase the English text on the right half ------------
        plan = []
        for b in blocks:
            bid = b["id"]
            if bid in verbatim:
                stats["skipped"] += 1
                continue
            zh = (trans.get(bid) or "").strip()
            if not zh:
                stats["skipped"] += 1
                continue
            r = fitz.Rect(b["bbox"])
            if r.is_empty:
                continue
            erase = clamp_erase(r, zones, pad=max(0.6, r.height * 0.03))
            if erase is None:
                stats["skipped"] += 1
                continue
            bg = sample_background(src[pno], r)
            page.draw_rect(fitz.Rect(erase.x0 + offset, erase.y0,
                                     erase.x1 + offset, erase.y1),
                           color=None, fill=bg, width=0, overlay=True)
            plan.append((b, r, zh))

        # ---- pass 2: draw the Chinese into the same rectangles -----------
        writers: dict[tuple, fitz.TextWriter] = {}

        def writer_for(color):
            key = tuple(round(c, 3) for c in color)
            if key not in writers:
                writers[key] = fitz.TextWriter(page.rect)
            return writers[key]

        for b, r, zh in plan:
            style = b.get("style") or {}
            base = float(style.get("size") or model.get("body_font_size", 8.5))
            kind = b["kind"]
            font = head_font if kind.startswith("heading") or kind in ("title", "label") else body_font

            size, lines = fit_text(zh, font, r.width, r.height, base, lh,
                                   max_scale, min_scale)
            if not lines:
                stats["overflow"] += 1
                continue
            if size < base * min_scale + 0.21:
                stats["shrunk"] += 1
            if len(lines) * size * lh > r.height + 1.0:
                stats["overflow"] += 1

            raw = style.get("color", "#000000").lstrip("#")
            try:
                rgb = (int(raw[0:2], 16) / 255, int(raw[2:4], 16) / 255, int(raw[4:6], 16) / 255)
            except Exception:
                rgb = (0, 0, 0)
            if not keep_color or sum(rgb) > 2.6 or sum(rgb) < 0.25:
                rgb = (0, 0, 0)

            tw = writer_for(rgb)
            x = offset + r.x0
            y = r.y0 + size * 0.84
            for ln in lines:
                tw.append((x, y), ln, font=font, fontsize=size)
                y += size * lh
            stats["replaced"] += 1

        for key, tw in writers.items():
            tw.write_text(page, color=key, overlay=True)

        stats["pages"] += 1

    stem = Path(model["source"]).stem
    pdf_path = Path(args.out).resolve() if args.out else work.parent / f"{stem}_dual.pdf"
    # A full SimSun/SimHei embed is 10 MB+; only the glyphs actually used matter.
    try:
        out.subset_fonts(verbose=False)
    except Exception:
        pass
    out.save(str(pdf_path), garbage=4, deflate=True, deflate_images=True,
             deflate_fonts=True, clean=True)
    out.close()
    src.close()

    size_mb = pdf_path.stat().st_size / 1_048_576
    print(f"font body : {body_path}")
    print(f"font head : {head_path}")
    print(f"pages     : {stats['pages']}  ({W:.0f}x{H:.0f}pt each half, "
          f"output {2 * W + gap:.0f}x{H:.0f}pt)")
    print(f"replaced  : {stats['replaced']}")
    print(f"skipped   : {stats['skipped']}  (verbatim / reference / figure labels)")
    print(f"shrunk    : {stats['shrunk']} blocks needed a font reduction")
    print(f"overflow  : {stats['overflow']} blocks did not fit")
    print(f"pdf       : {pdf_path}  ({size_mb:.1f} MB)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="In-place bilingual PDF composition")
    ap.add_argument("--workdir", default="work")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    return compose(args)


if __name__ == "__main__":
    raise SystemExit(main())
