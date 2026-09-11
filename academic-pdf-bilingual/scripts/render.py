#!/usr/bin/env python3
"""
render.py -- Compose the bilingual HTML document and print it to PDF.

Inputs
    blocks.json         produced by extract.py
    translations.json   { "meta": {...}, "terms": [...], "blocks": {"b0001": "..."} }

Outputs
    bilingual.html      A3 landscape, English left / Chinese right
    <name>.pdf          printed by headless Chrome (vector text, selectable)
    terms.md            glossary table (optional)

Why a <table> instead of flex/grid
    Each translation unit is one row: original in the left cell, translation in
    the right cell. Table rows fragment across printed pages far more reliably
    than flex or grid in headless Chrome, and cells of a row stay vertically
    aligned by construction, which is exactly the "anchor alignment" the
    bilingual layout needs.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]

HEADING_TAGS = {"heading1": "h1", "heading2": "h2", "heading3": "h3"}
FULL_WIDTH_KINDS = {"figure", "reference"}


# --------------------------------------------------------------------------
# css
# --------------------------------------------------------------------------

def build_css(opts: dict) -> str:
    en = opts.get("en_font", '"Times New Roman", "Nimbus Roman", Georgia, serif')
    cn = opts.get("cn_font", '"SimSun", "Songti SC", "Noto Serif CJK SC", serif')
    cn_head = opts.get("cn_heading_font", '"SimHei", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif')
    accent = opts.get("accent", "#1f4e79")
    pw = opts.get("page_width", "420mm")
    ph = opts.get("page_height", "297mm")
    fig_max_h = opts.get("figure_max_height", "218mm")
    en_size = opts.get("en_size", "8.6pt")
    cn_size = opts.get("cn_size", "9.0pt")
    cn_lh = opts.get("cn_line_height", "1.62")
    en_lh = opts.get("en_line_height", "1.40")

    return f"""
@page {{ size: {pw} {ph}; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
  color: #111; background: #fff;
}}
table.doc {{
  width: 100%; border-collapse: separate; border-spacing: 0;
  table-layout: fixed;
}}
table.doc col.c-en {{ width: 50%; }}
table.doc col.c-zh {{ width: 50%; }}
tr.r {{ break-inside: avoid; page-break-inside: avoid; }}
tr.r > td {{ vertical-align: top; }}
td.en {{ padding: 0.9mm 6mm 0.9mm 8mm; }}
td.zh {{ padding: 0.9mm 8mm 0.9mm 6mm; border-left: 0.4pt solid #dcdcdc; }}
table.doc thead th {{
  padding: 3mm 8mm 2mm 8mm; text-align: left; font-weight: 500;
  font-family: {cn_head}; font-size: 7.2pt; color: #7a7a7a;
  border-bottom: 0.4pt solid #dcdcdc;
}}
table.doc thead th span.rh-right {{ float: right; }}

.blk {{ font-family: {en}; font-size: {en_size}; line-height: {en_lh};
        text-align: justify; hyphens: auto; -webkit-hyphens: auto; }}
td.zh .blk {{ font-family: {cn}; font-size: {cn_size}; line-height: {cn_lh};
              letter-spacing: 0.01em; }}

h1, h2, h3 {{ margin: 0; font-family: {cn_head}; font-weight: 700; }}
td.en h1, td.en h2, td.en h3 {{ font-family: {en}; }}
h1 {{ font-size: 11.4pt; color: {accent}; }}
h2 {{ font-size: 10.2pt; color: #2f6b46; }}
h3 {{ font-size: 9.4pt; color: #9c4023; }}
td.zh h1 {{ font-size: 12.2pt; }}
td.zh h2 {{ font-size: 10.8pt; }}
td.zh h3 {{ font-size: 9.9pt; }}

.blk.k-title {{ font-size: 15pt; line-height: 1.32; font-weight: 700; color: {accent};
                 text-align: left; hyphens: none; }}
.zh .blk.k-title {{ font-size: 16pt; font-family: {cn_head}; }}
.blk.k-authors {{ font-size: 9.6pt; line-height: 1.5; text-align: left; }}
.zh .blk.k-authors {{ font-size: 9.6pt; line-height: 1.6; font-family: {cn_head}; }}
.blk.k-affiliation, .blk.k-label, .blk.k-note {{
   font-size: 8.1pt; line-height: 1.5; text-align: left; color: #333; }}
.zh .blk.k-affiliation, .zh .blk.k-label {{ font-size: 8.4pt; }}
.blk.k-label {{ font-weight: 700; letter-spacing: 0.06em; color: {accent};
                 font-family: {cn_head}; text-transform: uppercase; }}
.blk.k-abstract {{ font-size: 8.7pt; }}
.zh .blk.k-abstract {{ font-size: 9.3pt; }}
.blk.k-citation {{ font-size: 7.8pt; color: #444; line-height: 1.42; }}
.blk.k-reference {{ font-size: 7.6pt; line-height: 1.36; text-align: left;
                     text-indent: -3.2mm; padding-left: 3.2mm; }}
.blk.k-caption {{ font-size: 7.9pt; line-height: 1.42; }}
.zh .blk.k-caption {{ font-size: 8.3pt; }}
.blk.k-caption b {{ color: #444; }}

tr.full > td {{ padding: 2mm 8mm; }}
tr.figrow > td {{ padding: 3mm 8mm 1mm 8mm; text-align: center; }}
tr.figrow img {{ max-width: 100%; max-height: {fig_max_h}; width: auto; height: auto; }}
.figwork {{ font-size: 7.4pt; color: #a0a0a0; text-align: center; margin-top: 1mm; }}
.missing {{ background: #fdecec; color: #a33; padding: 0 2px; border-radius: 2px; }}
.zh .blk.verbatim-tag {{ font-family: {en}; font-size: 8.2pt; line-height: 1.45;
                         color: #4a4a4a; text-align: left; }}
.zh .blk.verbatim-tag .vnote {{ display: block; font-family: {cn_head}; font-size: 7.4pt;
                                color: #a0a0a0; margin-top: 0.8mm; }}
.zh-empty {{ color: #c8c8c8; }}

.footnote-zone {{ font-family: {cn}; }}

.runningfoot {{
  position: fixed; bottom: 3.5mm; left: 0; right: 0; text-align: center;
  font-family: {cn_head}; font-size: 7pt; color: #9a9a9a;
}}
.cover-note {{
  font-family: {cn}; font-size: 8pt; color: #666; line-height: 1.5;
  padding: 2mm 0 0 0;
}}
"""


# --------------------------------------------------------------------------
# block rendering
# --------------------------------------------------------------------------

def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def render_en_fragment(block: dict) -> str:
    text = esc(block.get("text", ""))
    if block["kind"] == "caption":
        m = re.match(r"^(Figure|Table|Scheme|Box)\s*(\d+[A-Za-z]?)\.\s*", text, re.IGNORECASE)
        if m:
            text = f"<b>{m.group(0).strip()}</b> " + text[m.end():]
    return text


def render_block_rows(block: dict, tr: dict, verbatim: set | None = None, *,
                      show_english: bool = True) -> str:
    kind = block["kind"]
    bid = block["id"]
    verbatim = verbatim or set()

    if bid in verbatim:
        body = render_en_fragment(block)
        note = "原样保留（作者姓名 / DOI / 引文格式不作翻译）"
        return (
            f'<tr class="r" id="{bid}">'
            f'<td class="en"><div class="blk k-{kind}">{body}</div></td>'
            f'<td class="zh"><div class="blk k-{kind} verbatim-tag">'
            f'{esc(block.get("text", ""))}<span class="vnote">{note}</span></div></td>'
            f'</tr>'
        )

    zh = tr.get(bid)
    if zh is None:
        zh = '<span class="missing">[未翻译]</span>'
    else:
        zh = esc(zh)

    if kind == "figure":
        fig = block.get("figure") or {}
        img = fig.get("image", "")
        label = esc(block.get("label") or "")
        wpt = fig.get("width_pt", 0)
        hpt = fig.get("height_pt", 0)
        return (
            f'<tr class="r figrow" id="{bid}"><td colspan="2" class="en">'
            f'<img src="{esc(img)}" alt="{label}">'
            f'<div class="figwork">{label} &nbsp;·&nbsp; 原图按 {fig.get("dpi", 300)} dpi 无损裁取'
            f'（{wpt:.0f}×{hpt:.0f}pt），图内标注保留英文</div>'
            f'</td></tr>'
        )

    if kind == "reference":
        body = render_en_fragment(block)
        return (
            f'<tr class="r full" id="{bid}"><td colspan="2" class="en">'
            f'<div class="blk k-reference">{body}</div></td></tr>'
        )

    tag = HEADING_TAGS.get(kind, "div")
    en_html = render_en_fragment(block)
    en_inner = f"<{tag}>{en_html}</{tag}>" if kind in HEADING_TAGS else en_html
    zh_inner = f"<{tag}>{zh}</{tag}>" if kind in HEADING_TAGS else zh

    en_cls = f"blk k-{kind}"
    zh_cls = f"blk k-{kind}"
    en_cell = (f'<td class="en {kind if kind in ("title", "authors", "affiliation", "label", "abstract", "citation") else ""}">'
               f'<div class="{en_cls}">{en_inner}</div></td>'
               if show_english else "")
    zh_cell = f'<td class="zh"><div class="{zh_cls}">{zh_inner}</div></td>'

    if not show_english:
        return f'<tr class="r full" id="{bid}">' + zh_cell.replace('class="zh"', 'class="zh" colspan="2"') + "</tr>"
    return f'<tr class="r" id="{bid}">{en_cell}{zh_cell}</tr>'


# --------------------------------------------------------------------------
# document assembly
# --------------------------------------------------------------------------

def build_document(model: dict, trdoc: dict, opts: dict) -> tuple[str, list[str]]:
    blocks = model["blocks"]
    tr = trdoc.get("blocks", {})
    meta = trdoc.get("meta", {})
    missing: list[str] = []

    trans_blocks = [b for b in blocks if b["kind"] != "figure"]
    verbatim = set(trdoc.get("verbatim") or [])
    for b in trans_blocks:
        if b["kind"] == "reference" or b["id"] in verbatim:
            continue
        if not tr.get(b["id"]):
            missing.append(b["id"])

    title_en = next((b["text"] for b in blocks if b["kind"] == "title"), "")
    title_zh = meta.get("title_zh") or tr.get(
        next((b["id"] for b in blocks if b["kind"] == "title"), ""), "")

    journal = meta.get("journal", "")
    year = meta.get("year", "")
    persona = meta.get("persona", "")
    src_name = Path(model.get("source", "")).name

    rh_left = meta.get("running_head", "")
    rh_right = journal

    rows: list[str] = []

    # -- masthead row ------------------------------------------------------
    rows.append(
        '<tr class="r full"><td colspan="2">'
        '<div class="cover-note">'
        f'<b>中英对照译本</b>　·　原文：<i>{esc(src_name)}</i>'
        f'{"　·　" + esc(journal) if journal else ""}{" " + esc(str(year)) if year else ""}'
        f'{"　·　译员角色：" + esc(persona) if persona else ""}<br>'
        '本译本由 AI 按学术翻译规范生成，专业术语保留原文以便回查；'
        '插图沿用原文位图，图内英文标注未作改动。引用与引文请以原刊英文版为准。'
        '</div></td></tr>'
    )

    for b in blocks:
        if b["kind"] == "chrome":
            continue
        rows.append(render_block_rows(b, tr, verbatim))

    thead = (
        "<thead><tr><th colspan='2'>"
        f"{esc(rh_left)}<span class='rh-right'>{esc(rh_right)}</span>"
        "</th></tr></thead>"
    )

    body = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>{esc(title_zh or title_en)}</title>"
        f"<style>{build_css(opts)}</style></head><body>"
        '<table class="doc"><colgroup><col class="c-en"><col class="c-zh"></colgroup>'
        f"{thead}<tbody>{''.join(rows)}</tbody></table>"
        f'<div class="runningfoot">{esc(meta.get("footer", "中英对照译本 · AI 辅助翻译，仅供阅读参考"))}</div>'
        "</body></html>"
    )
    return body, missing


def build_terms_md(trdoc: dict, meta_fallback: str = "") -> str:
    terms = trdoc.get("terms") or []
    if not terms:
        return ""
    lines = ["| 英文 | 中文 | 备注 |", "| --- | --- | --- |"]
    for t in terms:
        if isinstance(t, (list, tuple)):
            en = t[0] if len(t) > 0 else ""
            zh = t[1] if len(t) > 1 else ""
            note = t[2] if len(t) > 2 else ""
        else:
            en, zh, note = t.get("en", ""), t.get("zh", ""), t.get("note", "")
        lines.append(f"| {en} | {zh} | {note} |")
    head = f"# 术语对照表\n\n{meta_fallback}\n" if meta_fallback else "# 术语对照表\n\n"
    return head + "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# pdf
# --------------------------------------------------------------------------

def find_chrome() -> str | None:
    env = os.environ.get("CHROME_PATH")
    if env and Path(env).exists():
        return env
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    for name in ("google-chrome", "chromium", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def html_to_pdf(chrome: str, html_path: Path, pdf_path: Path, timeout: int = 180) -> tuple[bool, str]:
    cmd = [
        chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--disable-dev-shm-usage", "--no-pdf-header-footer",
        "--print-to-pdf-no-header", "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=20000", "--allow-file-access-from-files",
        f"--print-to-pdf={pdf_path}", html_path.as_uri(),
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "chrome timed out"
    if pdf_path.exists() and pdf_path.stat().st_size > 1000:
        return True, p.stderr[-800:]
    return False, (p.stderr or p.stdout)[-1500:]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Render the bilingual HTML and print to PDF")
    ap.add_argument("--workdir", default="work")
    ap.add_argument("--out", default=None, help="output pdf path")
    ap.add_argument("--html", default=None, help="output html path")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    work = Path(args.workdir).expanduser().resolve()
    blocks_path = work / "blocks.json"
    tr_path = work / "translations.json"
    if not blocks_path.exists():
        print(f"error: {blocks_path} not found -- run extract.py first", file=sys.stderr)
        return 2
    if not tr_path.exists():
        print(f"error: {tr_path} not found -- translate the blocks first", file=sys.stderr)
        return 2

    model = json.loads(blocks_path.read_text(encoding="utf-8"))
    trdoc = json.loads(tr_path.read_text(encoding="utf-8"))
    opts = trdoc.get("options", {}) or {}

    html_doc, missing = build_document(model, trdoc, opts)

    html_path = Path(args.html).resolve() if args.html else work / "bilingual.html"
    html_path.write_text(html_doc, encoding="utf-8")

    terms_md = build_terms_md(trdoc)
    if terms_md:
        (work / "terms.md").write_text(terms_md, encoding="utf-8")

    stem = Path(model.get("source", "document.pdf")).stem
    pdf_path = Path(args.out).resolve() if args.out else work.parent / f"{stem}_zh-bilingual.pdf"

    print(f"html      : {html_path}")
    if terms_md:
        print(f"terms     : {work / 'terms.md'}  ({len(trdoc.get('terms') or [])} entries)")
    print(f"blocks    : {len(model['blocks'])}   missing translations: {len(missing)}")
    if missing:
        preview = ", ".join(missing[:20]) + (" ..." if len(missing) > 20 else "")
        print(f"missing   : {preview}")

    if args.no_pdf:
        return 0 if not missing else 1

    chrome = find_chrome()
    if not chrome:
        print("error: no Chrome/Edge binary found; set CHROME_PATH", file=sys.stderr)
        return 2
    ok, log = html_to_pdf(chrome, html_path, pdf_path)
    if not ok:
        print(f"error: printing failed\n{log}", file=sys.stderr)
        return 3

    size_mb = pdf_path.stat().st_size / 1_048_576
    print(f"pdf       : {pdf_path}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
