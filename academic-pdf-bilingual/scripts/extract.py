#!/usr/bin/env python3
"""
extract.py -- Turn a scholarly PDF into a structured, reading-order block model.

Outputs (into --outdir):
    blocks.json   structured document model, one entry per translatable unit
    figures/*.png raster crops of every detected figure region
    outline.txt   human readable dump for review / debugging

Design notes
------------
* Reading order for multi-column layouts is column-major inside a vertical
  "segment"; a full-width element (wide heading, figure zone, caption) closes
  the running segment and starts a new one.
* Figure regions are inferred from the layout, not from embedded image
  bounding boxes (those are unreliable in vector-heavy Elsevier proofs).
  A caption `Figure N. ...` implies a graphic occupying the free band above it.
* Text inside a figure region is part of the artwork and is dropped from the
  text flow, so panel labels never end up in the translation queue.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

# --------------------------------------------------------------------------
# text normalisation
# --------------------------------------------------------------------------

LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\u2019": "'", "\u2018": "'", "\u201c": '"',
    "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u00ad": "",
    "\u00a0": " ", "\u2212": "-", "\u02bc": "'",
}

# Compounds whose internal hyphen must survive a line break.
HYPHEN_KEEP = {
    "multi-user", "multi-agent", "multi-modal", "multi-agent-based",
    "ai-assisted", "ai-driven", "ai-based", "ai-powered", "llm-based",
    "human-centered", "human-in-the-loop", "human-oriented", "human-ai",
    "domain-specific", "domain-agnostic", "task-specific", "real-time",
    "end-to-end", "large-scale", "small-scale", "closed-loop", "in-the-loop",
    "state-of-the-art", "knowledge-augmented", "retrieval-augmented",
    "high-quality", "high-throughput", "well-defined", "well-suited",
    "user-friendly", "user-controlled", "cross-platform", "peer-to-peer",
    "open-source", "self-hosted", "self-attention", "fine-tuning",
    "long-term", "short-term", "follow-up", "decision-making", "trade-off",
    "trade-offs", "zero-shot", "few-shot", "chain-of-thought",
    "data-driven", "evidence-based", "whole-genome", "gene-expression",
    "deep-learning", "machine-learning", "up-to-date", "context-aware",
    "two-stage", "three-stage", "non-profit", "cost-effective",
    "bi-directional", "on-premises", "on-premise", "plug-and-play",
    "ready-to-use", "out-of-the-box", "state-of-the-art", "co-evolution",
    "agent-based", "record-based", "score-based", "feature-based",
}

CAPTION_RE = re.compile(r"^\s*(Figure|Fig\.|Table|Scheme|Box)\s*(\d+[A-Za-z]?)\s*[.:\u2014-]",
                        re.IGNORECASE)
REF_HEADING_RE = re.compile(r"^\s*(REFERENCES?|Literature\s+cited|Bibliography)\s*$", re.IGNORECASE)
SECTION_WORDS = {
    "abstract", "introduction", "background", "results", "result",
    "discussion", "conclusion", "conclusions", "methods", "method",
    "materials and methods", "acknowledgments", "acknowledgements",
    "author contributions", "funding", "competing interests",
    "data availability", "supplemental information", "supplementary",
    "references", "key words", "keywords", "highlights", "graphical abstract",
}


def clean_text(raw: str) -> str:
    """Normalise a raw block string: ligatures, de-hyphenation, whitespace."""
    for bad, good in LIGATURES.items():
        raw = raw.replace(bad, good)

    raw = raw.replace("\u00ad\n", "")
    lines = [ln.rstrip() for ln in raw.split("\n")]

    out: list[str] = []
    broken: list[bool] = []          # True when line i ended a hyphenated word
    for i, ln in enumerate(lines):
        if i < len(lines) - 1 and ln.endswith("-") and len(ln) > 1:
            nxt = lines[i + 1].lstrip()
            if nxt:
                head = ln[:-1].split()[-1] if ln[:-1].split() else ""
                tail = nxt.split()[0] if nxt.split() else ""
                joined = f"{head}-{tail}"
                keep = (
                    joined.lower() in HYPHEN_KEEP
                    or (head and head[-1].isupper())
                    or (len(tail) > 1 and tail[:1].isupper())
                )
                out.append(ln if keep else ln[:-1])
                broken.append(True)
                continue
        out.append(ln)
        broken.append(False)

    # join lines: a hyphen-broken line closes up with no space
    buf = out[0] if out else ""
    for i in range(1, len(out)):
        joiner = "" if broken[i - 1] else " "
        buf += joiner + out[i].lstrip()
    buf = re.sub(r"[ \t]{2,}", " ", buf)
    buf = re.sub(r"\s+([,.;:!?%)\]])", r"\1", buf)
    buf = re.sub(r"\(\s+", "(", buf)
    return buf.strip()


def is_hyphen_split(prev_text: str, next_text: str) -> bool:
    """True when prev ends a hyphenated word that next completes."""
    if not prev_text.endswith("-") or len(prev_text) < 2:
        return False
    nxt = next_text.lstrip()
    if not nxt:
        return False
    head = prev_text[:-1].split()[-1] if prev_text[:-1].split() else ""
    tail = nxt.split()[0] if nxt.split() else ""
    if not head or not tail:
        return False
    if not tail[:1].islower():
        return False
    joined = f"{head}-{tail}".lower()
    return joined not in HYPHEN_KEEP


TERMINAL = (".", "!", "?", ":", ";", '"', "'", ")", "]", "\u3002", "\uff01", "\uff1f")


def continues_paragraph(prev_text: str, next_text: str) -> bool:
    """True when a column/page break cut one paragraph into two blocks.

    Two signals: the previous block ends mid-word with a hyphen, or it ends
    without terminal punctuation while the next block resumes in lower case
    or with a number -- the classic signature of a flow cut at a column edge.
    """
    if not prev_text or not next_text:
        return False
    nxt = next_text.lstrip()
    if not nxt:
        return False
    if prev_text.endswith("-") and len(prev_text) > 1:
        return True
    if prev_text.rstrip().endswith(TERMINAL):
        return False
    return nxt[0].islower() or nxt[0].isdigit() or nxt[0] in "(["


def join_split(prev_text: str, next_text: str) -> str:
    """Glue two blocks back together, preserving compound hyphens."""
    nxt = next_text.lstrip()
    if prev_text.endswith("-") and len(prev_text) > 1:
        head = prev_text[:-1].split()[-1] if prev_text[:-1].split() else ""
        tail = nxt.split()[0] if nxt.split() else ""
        joined = f"{head}-{tail}"
        keep = (
            joined.lower() in HYPHEN_KEEP
            or (head and head[-1].isupper())
            or (len(tail) > 1 and tail[:1].isupper())
        )
        return (prev_text if keep else prev_text[:-1]) + nxt
    return prev_text + " " + nxt


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

def rect_of(bbox) -> fitz.Rect:
    return fitz.Rect(bbox)


def union(a: fitz.Rect, b: fitz.Rect) -> fitz.Rect:
    r = fitz.Rect(a)
    r.include_rect(b)
    return r


# --------------------------------------------------------------------------
# page model
# --------------------------------------------------------------------------

class RawBlock:
    __slots__ = ("page", "bbox", "text", "size", "bold", "color", "kind", "align")

    def __init__(self, page, bbox, text, size, bold, color):
        self.page = page
        self.bbox = rect_of(bbox)
        self.text = text
        self.size = size
        self.bold = bold
        self.color = color
        self.kind = "para"
        self.align = "left"

    @property
    def width(self) -> float:
        return self.bbox.width

    @property
    def height(self) -> float:
        return self.bbox.height

    def style(self) -> dict:
        return {
            "size": round(self.size, 2),
            "bold": bool(self.bold),
            "color": "#%06x" % (self.color & 0xFFFFFF),
            "align": self.align,
        }


def read_pages(doc: fitz.Document) -> list[list[RawBlock]]:
    pages: list[list[RawBlock]] = []
    for pno in range(doc.page_count):
        page = doc[pno]
        d = page.get_text("dict")
        blocks: list[RawBlock] = []
        for b in d["blocks"]:
            if b.get("type", 0) != 0:
                continue
            spans = [s for ln in b.get("lines", []) for s in ln.get("spans", [])]
            if not spans:
                continue
            text = "\n".join(
                "".join(s["text"] for s in ln["spans"]) for ln in b.get("lines", [])
            )
            if not text.strip():
                continue
            sizes = [s["size"] for s in spans if s["text"].strip()]
            biggest = max(sizes) if sizes else 0.0
            bold = any(s["flags"] & 16 for s in spans if s["text"].strip())
            colors = [s["color"] for s in spans if s["text"].strip()]
            color = max(set(colors), key=colors.count) if colors else 0
            blocks.append(RawBlock(pno + 1, b["bbox"], text, biggest, bold, color))
        pages.append(blocks)
    return pages


def body_font_size(pages: list[list[RawBlock]]) -> float:
    tally: dict[float, int] = {}
    for blocks in pages:
        for b in blocks:
            if len(b.text) >= 60:
                tally[round(b.size, 1)] = tally.get(round(b.size, 1), 0) + len(b.text)
    return max(tally, key=tally.get) if tally else 8.5


def content_box(pages: list[list[RawBlock]], page_rect: fitz.Rect,
                header_zone: float, footer_zone: float) -> fitz.Rect:
    xs0, xs1, ys0, ys1 = [], [], [], []
    for blocks in pages:
        for b in blocks:
            if b.bbox.y1 <= header_zone or b.bbox.y0 >= footer_zone:
                continue
            if len(b.text) < 40:
                continue
            xs0.append(b.bbox.x0)
            xs1.append(b.bbox.x1)
            ys0.append(b.bbox.y0)
            ys1.append(b.bbox.y1)
    if not xs0:
        return fitz.Rect(60, header_zone, page_rect.width - 60, footer_zone)
    xs0.sort(); xs1.sort(); ys0.sort(); ys1.sort()
    n = len(xs0)
    left = xs0[int(n * 0.05)]
    right = xs1[int(n * 0.95)]
    return fitz.Rect(left, min(ys0), right, max(ys1))


# --------------------------------------------------------------------------
# caption / figure-zone detection
# --------------------------------------------------------------------------

def find_captions(blocks: list[RawBlock], box: fitz.Rect,
                  header_zone: float, footer_zone: float) -> list[RawBlock]:
    caps = []
    for b in blocks:
        if b.bbox.y1 <= header_zone or b.bbox.y0 >= footer_zone:
            continue
        if CAPTION_RE.match(clean_text(b.text)):
            caps.append(b)
    caps.sort(key=lambda b: b.bbox.y0)
    return caps


def _paragraph_like(b: RawBlock, box: fitz.Rect) -> bool:
    """A real body paragraph: long and reasonably wide."""
    return len(b.text) >= 110 and b.bbox.width >= 0.30 * box.width


def find_figure_zones(page: fitz.Page, blocks: list[RawBlock], box: fitz.Rect,
                      header_zone: float, footer_zone: float):
    """Return list of dicts: {rect, captions:[RawBlock]}."""
    caps = find_captions(blocks, box, header_zone, footer_zone)
    if not caps:
        return []

    live = [b for b in blocks
            if header_zone < b.bbox.y0 and b.bbox.y1 < footer_zone]

    # ink extent of raster + vector artwork, used to trim the horizontal band
    ink = fitz.Rect()
    have_ink = False
    for d in page.get_drawings():
        r = fitz.Rect(d["rect"])
        if r.width < 3 or r.height < 3:
            continue
        r = r & page.rect
        ink = union(ink, r) if have_ink else r
        have_ink = True
    for im in page.get_image_info():
        r = fitz.Rect(im["bbox"]) & page.rect
        if r.width < 8 or r.height < 8:
            continue
        ink = union(ink, r) if have_ink else r
        have_ink = True

    def trim_horizontal(band: fitz.Rect) -> fitz.Rect:
        if not have_ink:
            return band
        clipped = ink & band
        if clipped.is_empty or clipped.width < 0.35 * box.width:
            return band
        return fitz.Rect(max(box.x0, clipped.x0 - 3), band.y0,
                         min(box.x1, clipped.x1 + 3), band.y1)

    zones = []
    for cap in caps:
        above = [b for b in live if b.bbox.y1 <= cap.bbox.y0 and b is not cap]
        para_above = [b for b in above if _paragraph_like(b, box)]
        top = max((b.bbox.y1 for b in para_above), default=header_zone + 2.0)
        region = fitz.Rect(box.x0, top + 2.0, box.x1, cap.bbox.y0 - 3.0)
        if region.height < 40:
            below = [b for b in live if b.bbox.y0 >= cap.bbox.y1 and b is not cap]
            para_below = [b for b in below if _paragraph_like(b, box)]
            bottom = min((b.bbox.y0 for b in para_below), default=box.y1)
            region = fitz.Rect(box.x0, cap.bbox.y1 + 3.0, box.x1, bottom - 2.0)
            if region.height < 40:
                continue
        zones.append({"rect": trim_horizontal(region), "captions": [cap]})

    # merge zones that overlap (multi-panel pages)
    zones.sort(key=lambda z: z["rect"].y0)
    merged = []
    for z in zones:
        if merged and z["rect"].y0 <= merged[-1]["rect"].y1 + 6:
            merged[-1]["rect"] = union(merged[-1]["rect"], z["rect"])
            merged[-1]["captions"].extend(z["captions"])
        else:
            merged.append(z)
    return merged


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def classify(b: RawBlock, page_no: int, is_last_page: bool,
             in_references: bool, body_size: float, box: fitz.Rect,
             first_text_block: bool, max_size_on_p1: float) -> str:
    text = clean_text(b.text)
    low = text.strip().lower()
    stripped = low.rstrip(".:")

    if CAPTION_RE.match(text):
        return "caption"
    if in_references:
        return "reference"
    if re.match(r"^\s*(key\s?words?|highlights|graphical abstract)\b", text, re.IGNORECASE):
        return "label"
    if page_no == 1 and first_text_block and b.size >= max_size_on_p1 - 0.01:
        return "title"
    if stripped in SECTION_WORDS or stripped.replace("  ", " ") in SECTION_WORDS:
        if stripped in {"abstract", "key words", "keywords"}:
            return "label"
        return "heading1"
    short = len(text) <= 120
    if short and b.bbox.width < 0.85 * box.width:
        if b.bold and b.size >= body_size + 1.0:
            return "heading1"
        if b.bold and b.size >= body_size + 0.2:
            return "heading2"
        if b.bold:
            return "heading3"
        if b.size >= body_size + 1.4 and len(text) <= 80:
            return "heading2"
    return "para"


# --------------------------------------------------------------------------
# reading order
# --------------------------------------------------------------------------

def order_page(blocks: list[RawBlock], zones: list[dict], box: fitz.Rect) -> list[dict]:
    """Return ordered list of items: {'kind':'text'|'figure', ...}"""
    items: list[dict] = []
    for b in blocks:
        items.append({"kind": "text", "block": b, "y0": b.bbox.y0,
                      "wide": b.bbox.width > 0.62 * box.width})
    for z in zones:
        items.append({"kind": "figure", "zone": z, "y0": z["rect"].y0, "wide": True})

    items.sort(key=lambda it: (round(it["y0"], 1), 0 if it["kind"] == "figure" else 1))

    mid = (box.x0 + box.x1) / 2.0
    ordered: list[dict] = []
    run: list[dict] = []

    def flush():
        if not run:
            return
        def col_of(it):
            b = it["block"]
            return 1 if (b.bbox.x0 + b.bbox.x1) / 2.0 < mid else 2
        run.sort(key=lambda it: (col_of(it), it["y0"]))
        ordered.extend(run)
        run.clear()

    for it in items:
        if it["wide"]:
            flush()
            ordered.append(it)
        else:
            run.append(it)
    flush()
    return ordered


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Structured extraction for bilingual scholarly PDFs")
    ap.add_argument("pdf")
    ap.add_argument("--outdir", default="work")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--max-pages", type=int, default=0)
    ap.add_argument("--profile", default="inplace", choices=["inplace", "reflow"],
                    help="inplace = natural blocks, keeps header/footer, for in-place "
                         "overlay composition. reflow = merged paragraphs, drops "
                         "header/footer, for the re-flowed side-by-side layout.")
    ap.add_argument("--no-figure-crops", action="store_true",
                    help="skip rasterising figure regions (in-place composition does "
                         "not need them, the artwork is copied with the page).")
    args = ap.parse_args()

    # In-place overlay composition replaces text inside the original block
    # rectangles, so a block must never span two columns. Re-flow composition
    # wants whole paragraphs instead, so it merges fragments across column
    # breaks. Same document, two different block segmentations.
    minimal_merge = args.profile == "inplace"

    src = Path(args.pdf).expanduser().resolve()
    if not src.exists():
        print(f"error: no such file: {src}", file=sys.stderr)
        return 2

    outdir = Path(args.outdir).expanduser().resolve()
    figdir = outdir / "figures"
    figdir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(src)
    page_rect = doc[0].rect
    header_zone = 60.0
    footer_zone = page_rect.height - 48.0

    pages = read_pages(doc)
    if args.max_pages:
        pages = pages[: args.max_pages]

    body_size = body_font_size(pages)
    box = content_box(pages, page_rect, header_zone, footer_zone)

    n_pages = len(pages)
    last_pno = n_pages

    blocks_out: list[dict] = []
    outline: list[str] = []
    figure_zones_by_page: dict[str, list[list[float]]] = {}
    seq = 0
    in_references = False
    fig_index = 0

    for pno in range(1, n_pages + 1):
        blocks = pages[pno - 1]
        live = [b for b in blocks if header_zone < b.bbox.y0 and b.bbox.y1 < footer_zone]

        zones = find_figure_zones(doc[pno - 1], blocks, box, header_zone, footer_zone)
        figure_zones_by_page[str(pno)] = [
            [round(v, 1) for v in z["rect"]] for z in zones
        ]
        zone_ids = set()
        for z in zones:
            for c in z["captions"]:
                zone_ids.add(id(c))

        # drop text that sits inside a figure graphic
        kept: list[RawBlock] = []
        for b in live:
            inside = any(z["rect"].contains(b.bbox.tl) and z["rect"].contains(b.bbox.br)
                         for z in zones)
            if inside and id(b) not in zone_ids:
                continue
            kept.append(b)

        text_blocks = [b for b in kept if id(b) not in zone_ids]
        max_size_p1 = max((b.size for b in text_blocks if len(b.text) > 40), default=0.0)

        ordered = order_page(kept, zones, box)
        outline.append(f"\n===== page {pno} =====")

        # ---- pass 1: collect items for this page -------------------------
        page_items: list[dict] = []
        first_text = True
        for it in ordered:
            if it["kind"] == "figure":
                page_items.append({"figure_zone": it["zone"], "y0": it["y0"]})
                continue
            b = it["block"]
            if id(b) in zone_ids:
                continue
            text = clean_text(b.text)
            if not text:
                continue
            kind = classify(b, pno, pno == last_pno, in_references, body_size,
                            box, first_text, max_size_p1)
            first_text = False

            ref_head = re.match(
                r"^\s*(REFERENCES?|Literature\s+cited|Bibliography)\s+(?=\S)(.{20,})$",
                text, re.IGNORECASE | re.DOTALL)
            if ref_head:
                in_references = True
                page_items.append({"text": ref_head.group(1).upper(),
                                   "bbox": b.bbox, "kind": "heading1",
                                   "y0": b.bbox.y0, "style": b.style()})
                text = ref_head.group(2).strip()
                kind = "reference"
            elif REF_HEADING_RE.match(text):
                in_references = True
                kind = "heading1"
            elif kind != "caption" and len(text) > 60:
                caps = re.match(r"^((?:[A-Z][A-Z0-9&/\-]{0,14}\s+){1,4})(?=[A-Z(])(.{25,})$",
                                text, re.DOTALL)
                if caps and 6 <= len(caps.group(1).strip()) <= 46:
                    head_txt = caps.group(1).strip()
                    page_items.append({"text": head_txt, "bbox": b.bbox,
                                       "kind": "heading1", "y0": b.bbox.y0,
                                       "style": b.style()})
                    text = caps.group(2).strip()

            page_items.append({"text": text, "bbox": b.bbox, "kind": kind,
                               "y0": b.bbox.y0, "style": b.style()})

        # ---- pass 2: rejoin words broken across block boundaries ---------
        # In-place composition replaces the text inside each block rectangle, so
        # a block must never span the column gutter. There, every natural block
        # is kept as-is; only the re-flow profile glues fragments back together.
        merged: list[dict] = []
        for item in page_items:
            if "figure_zone" in item or minimal_merge:
                merged.append(item)
                continue
            prev = merged[-1] if merged else None
            if (prev is not None and "figure_zone" not in prev
                    and prev["kind"] == item["kind"]
                    and prev["kind"] in ("para", "caption", "reference", "heading3")
                    and continues_paragraph(prev["text"], item["text"])):
                prev["text"] = join_split(prev["text"], item["text"])
                prev["bbox"] = union(prev["bbox"], item["bbox"])
                continue
            merged.append(item)

        # ---- pass 3: emit ------------------------------------------------
        for item in merged:
            if "figure_zone" in item:
                z = item["figure_zone"]
                fig_index += 1
                seq += 1
                fid = f"f{fig_index:02d}"
                png = figdir / f"{fid}_p{pno}.png"
                clip = fitz.Rect(z["rect"]) & page_rect
                if not args.no_figure_crops:
                    pix = doc[pno - 1].get_pixmap(
                        matrix=fitz.Matrix(args.dpi / 72.0, args.dpi / 72.0), clip=clip)
                    pix.save(str(png))
                cap_text = " ".join(clean_text(c.text) for c in z["captions"])
                label = ""
                m = CAPTION_RE.match(cap_text)
                if m:
                    label = f"{m.group(1).capitalize()} {m.group(2)}"
                blocks_out.append({
                    "id": f"b{seq:04d}",
                    "page": pno,
                    "seq": seq,
                    "kind": "figure",
                    "level": 0,
                    "bbox": [round(v, 1) for v in z["rect"]],
                    "text": "",
                    "label": label,
                    "figure": {
                        "image": f"figures/{png.name}",
                        "width_pt": round(clip.width, 1),
                        "height_pt": round(clip.height, 1),
                        "dpi": args.dpi,
                    },
                })
                outline.append(f"  F      p{pno} {label:12s} {clip.width:.0f}x{clip.height:.0f}pt -> {png.name}")
                for c in z["captions"]:
                    seq += 1
                    ctext = clean_text(c.text)
                    blocks_out.append({
                        "id": f"b{seq:04d}",
                        "page": pno,
                        "seq": seq,
                        "kind": "caption",
                        "level": 0,
                        "bbox": [round(v, 1) for v in c.bbox],
                        "text": ctext,
                        "label": label,
                        "figure": None,
                        "style": c.style(),
                    })
                    outline.append(f"  CAP    p{pno} {ctext[:78]}")
                continue

            seq += 1
            blocks_out.append({
                "id": f"b{seq:04d}",
                "page": pno,
                "seq": seq,
                "kind": item["kind"],
                "level": 0,
                "bbox": [round(v, 1) for v in item["bbox"]],
                "text": item["text"],
                "label": "",
                "figure": None,
                "style": item.get("style") or {"size": body_size, "bold": False,
                                               "color": "#000000", "align": "left"},
            })
            outline.append(f"  {item['kind']:6s} p{pno} len={len(item['text']):5d} {item['text'][:78]}")

    doc.close()

    model = {
        "source": str(src),
        "profile": args.profile,
        "page_count": n_pages,
        "page_size": [round(page_rect.width, 1), round(page_rect.height, 1)],
        "content_box": [round(v, 1) for v in box],
        "figure_zones": figure_zones_by_page,
        "header_zone": header_zone,
        "footer_zone": round(footer_zone, 1),
        "body_font_size": body_size,
        "blocks": blocks_out,
    }
    (outdir / "blocks.json").write_text(
        json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / "outline.txt").write_text("\n".join(outline), encoding="utf-8")

    kinds: dict[str, int] = {}
    for b in blocks_out:
        kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1
    chars = sum(len(b["text"]) for b in blocks_out)

    print(f"pages          : {n_pages}")
    print(f"page size      : {page_rect.width:.0f} x {page_rect.height:.0f} pt")
    print(f"content box    : {box.x0:.0f},{box.y0:.0f} -> {box.x1:.0f},{box.y1:.0f}")
    print(f"body font size : {body_size}")
    print(f"blocks         : {len(blocks_out)}   chars: {chars}")
    print(f"figures        : {fig_index}")
    print("kinds          : " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    print(f"written        : {outdir / 'blocks.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
