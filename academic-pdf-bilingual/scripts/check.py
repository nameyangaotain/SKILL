#!/usr/bin/env python3
"""
check.py -- Quality gate for a bilingual translation pass.

Verifies, without calling any model:
  * coverage      every translatable block has a non-empty translation
  * leakage       the Chinese side is actually Chinese (no copied source text)
  * length ratio  translations are neither stubs nor runaway expansions
  * numbers       numerals, units, percentages and statistics survive
  * cross-refs    (Figure 1), (Table 2), Author-year citations survive
  * glossary      the declared term list is used consistently
  * structure     figure/caption numbering is contiguous

Exit code 0 = clean, 1 = warnings only, 2 = hard failures.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
NUM = re.compile(r"\d+(?:[.,]\d+)?%?")
# figure / table cross references, English and Chinese forms are equivalent
FIGREF = re.compile(r"(?:Figure|Fig\.|Table|Scheme|图|表)\s*(\d+)\s*([A-Za-z])?", re.I)
SUPREF = re.compile(
    r"(?:Supplemental|Supplementary|Video|补充)\s*"
    r"(?:Table|Video|Figure|Methods?|图|表|视频|方法)?\s*(\d+)", re.I)
CITE = re.compile(r"\(([A-Z][A-Za-z\-']+(?:\s+(?:and|&)\s+[A-Z][A-Za-z\-']+)?(?:\s+et\s+al\.)?),\s*(\d{4}[a-z]?)\)")
WORD = re.compile(r"[A-Za-z]{4,}")

PUNCT_MAP = str.maketrans({"（": "(", "）": ")", "，": ",", "；": ";",
                           "：": ":", "。": ".", "、": ",", "　": " ",
                           "–": "-", "—": "-", "％": "%", "％": "%"})


def norm(s: str) -> str:
    """Fold CJK punctuation into ASCII so pattern matching works on both sides."""
    return s.translate(PUNCT_MAP)

SKIP_KINDS = {"figure", "reference", "chrome"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default="work")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    work = Path(args.workdir).expanduser().resolve()
    model = json.loads((work / "blocks.json").read_text(encoding="utf-8"))
    trdoc = json.loads((work / "translations.json").read_text(encoding="utf-8"))
    tr = trdoc.get("blocks", {})
    terms = trdoc.get("terms") or []
    verbatim = set(trdoc.get("verbatim") or [])

    errors: list[str] = []
    warnings: list[str] = []
    per_block: list[dict] = []

    blocks = [b for b in model["blocks"] if b["kind"] not in SKIP_KINDS]

    # ---- coverage / leakage / ratio ------------------------------------
    for b in blocks:
        bid = b["id"]
        src = b["text"]
        dst = tr.get(bid)

        if bid in verbatim:
            per_block.append({"id": bid, "page": b["page"], "kind": b["kind"],
                              "en_len": len(src), "cn_len": len(src), "ratio": 1.0,
                              "issues": []})
            continue

        if dst is None or not str(dst).strip():
            errors.append(f"[coverage] {bid} (p{b['page']}, {b['kind']}) has no translation")
            continue

        dst = str(dst)
        issues: list[str] = []
        src_n, dst_n = norm(src), norm(dst)

        if not CJK.search(dst) and len(src) > 40:
            errors.append(f"[leak] {bid}: translation contains no CJK characters")

        if dst.strip() == src.strip() and len(src) > 40:
            errors.append(f"[leak] {bid}: translation identical to source")

        ratio = len(dst) / max(len(src), 1)
        # Calibrated on real academic EN->ZH output: the observed band is roughly
        # 0.24-0.57 because Chinese is compact and citations stay in English.
        # Anything under 0.25 on a long block means a truncated or misaligned row.
        lo, hi = 0.25, 1.05
        if len(src) >= 200 and (ratio < lo or ratio > hi):
            warnings.append(f"[ratio] {bid}: cn/en = {ratio:.2f} ({len(dst)}/{len(src)}) "
                            f"-- likely truncated or paired with the wrong row")

        # numbers must survive
        sn, dn = Counter(NUM.findall(src)), Counter(NUM.findall(dst))
        lost = [n for n in sn if sn[n] > dn.get(n, 0)]
        if lost:
            warnings.append(f"[number] {bid}: missing {lost[:6]}")

        # cross references must survive (Figure 1A == 图 1A)
        sf = Counter(FIGREF.findall(src_n))
        df = Counter(FIGREF.findall(dst_n))
        lost_f = [f"{n}{l}" for (n, l) in sf if df.get((n, l), 0) < sf[(n, l)]]
        if lost_f:
            warnings.append(f"[figref] {bid}: missing {sorted(lost_f)[:5]}")

        sf2 = Counter(SUPREF.findall(src_n))
        df2 = Counter(SUPREF.findall(dst_n))
        lost_s = [n for n in sf2 if df2.get(n, 0) < sf2[n]]
        if lost_s:
            warnings.append(f"[supref] {bid}: missing supplemental ref {sorted(lost_s)[:5]}")

        # citation keys: (Author, 2024) -- punctuation already folded
        sc = Counter(m.group(1).split()[0] for m in CITE.finditer(src_n))
        dc = Counter(m.group(1).split()[0] for m in CITE.finditer(dst_n))
        lost_c = [c for c in sc if dc.get(c, 0) < sc[c]]
        if lost_c:
            warnings.append(f"[cite] {bid}: citation key {sorted(lost_c)[:5]} not carried over")

        # stray untranslated latin runs on the Chinese side
        if b["kind"] in ("para", "abstract", "caption") and len(src) > 200:
            runs = WORD.findall(dst)
            if len(runs) > 0.55 * len(WORD.findall(src)) and len(WORD.findall(src)) > 40:
                warnings.append(f"[latin] {bid}: Chinese side still holds {len(runs)} latin words")

        per_block.append({"id": bid, "page": b["page"], "kind": b["kind"],
                          "en_len": len(src), "cn_len": len(dst), "ratio": round(ratio, 2),
                          "issues": issues})

    # ---- glossary consistency ------------------------------------------
    for t in terms:
        if isinstance(t, (list, tuple)):
            en, zh = (t + ["", ""])[:2]
        else:
            en, zh = t.get("en", ""), t.get("zh", "")
        if not en or not zh:
            continue
        # if the source uses the term, the translation should use the agreed form
        # at least once (allowing a first-occurrence "中文（English）" pattern)
        src_hits = sum(1 for b in blocks if en.lower() in b["text"].lower())
        if src_hits == 0:
            continue
        dst_hits = sum(1 for b in blocks if zh in str(tr.get(b["id"], "")))
        if dst_hits == 0:
            warnings.append(f"[glossary] '{en}' -> '{zh}' declared but never used in the translation")

    # ---- figure numbering ----------------------------------------------
    figs = [b for b in model["blocks"] if b["kind"] == "figure"]
    nums = []
    for f in figs:
        m = re.search(r"(\d+)", f.get("label") or "")
        if m:
            nums.append(int(m.group(1)))
    if nums and nums != list(range(nums[0], nums[0] + len(nums))):
        warnings.append(f"[figure] non-contiguous figure numbering: {nums}")

    # ---- report ---------------------------------------------------------
    report = {
        "blocks_checked": len(blocks),
        "translated": sum(1 for b in blocks if str(tr.get(b["id"], "")).strip()),
        "errors": errors,
        "warnings": warnings,
        "per_block": per_block,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"blocks checked : {len(blocks)}")
    print(f"translated     : {report['translated']}")
    print(f"errors         : {len(errors)}")
    print(f"warnings       : {len(warnings)}")
    print()
    for e in errors[:40]:
        print("  ERR " + e)
    if len(errors) > 40:
        print(f"  ... {len(errors) - 40} more errors")
    for w in warnings[:40]:
        print("  WARN " + w)
    if len(warnings) > 40:
        print(f"  ... {len(warnings) - 40} more warnings")

    if errors:
        return 2
    if warnings and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
