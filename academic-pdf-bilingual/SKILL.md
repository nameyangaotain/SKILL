---
name: academic-pdf-bilingual
description: Translate a scholarly PDF into a bilingual English/Chinese edition that keeps the original layout intact. Produces one output page per source page at double width, with the untouched original on the left and the same page with Chinese text substituted in place on the right, figures copied into both halves. Also supports a re-flowed variant where English and Chinese sit in aligned side-by-side rows. Use this skill when the user asks to 翻译文献 / 翻译论文 / 中英对照 / 双语对照 / 对照排版 / 保留原排版翻译 / 出一份左右对照的 PDF, or wants a bilingual version of a journal article, preprint, report, or thesis chapter.
agent_created: true
---

# 学术文献中英对照翻译

把一篇学术 PDF 做成左右对照的双语版：**左半是原页原样复制，右半是同一页的版式保留版，只把文字原地换成了中文**。页面宽度翻倍，页数与原文严格 1:1，图片在左右两半各出现一次，图内标注保持英文。

另有一种"重排版"模式：把原文和译文排成对齐的左右两列，正文按段对照、页数放开。两种模式共用同一套解析与翻译产物，只是最后一步不同。

## 何时使用

- 用户给出论文 PDF，要求翻译成中文并**保留原排版**。
- 用户要求「中英对照」「双语对照」「左右对照」「一份原文一份译文」。
- 用户希望页面变成 A4 的两倍宽（= 两个原始页面并排）。

不适用：纯文本/Word 翻译、只需摘要翻译、扫描件 OCR（本流程依赖 PDF 内嵌文字层）。

## 两种版式，怎么选

| | 原位对照（默认） | 重排对照 |
| --- | --- | --- |
| 左半 | 原页 1:1 复制，不动 | 英文重排为单栏 |
| 右半 | 同一页，文字原地换成中文 | 中文重排为单栏 |
| 分栏/图表位置 | **与原页完全一致** | 保留顺序，位置重算 |
| 页数 | 与原文 1:1 | 约等于原文，随内容浮动 |
| 图片 | 左右各一份 | 通栏一份 |
| 适合 | 要"看起来还是这篇论文" | 要逐段精读对照 |
| 脚本 | `compose.py` | `render.py` |

用户没有明说时，默认**原位对照**——它才是"保持文献排版"的字面意思。

## 前置检查

```bash
python scripts/extract.py <pdf> --outdir work --max-pages 2
```

若 `blocks` 数量极少而页面明显有内容，说明是扫描件，需先 OCR。

依赖：`PyMuPDF`。原位模式用系统中文 TTF（Windows 的 SimSun/SimHei，macOS 的 Songti，Linux 的 Noto CJK），找不到时回退到 PyMuPDF 内置 CJK 字体。重排模式另外需要 Chrome 或 Edge。

## 工作流

### 第 1 步 · 结构化解析

```bash
python scripts/extract.py <paper.pdf> --outdir work --profile inplace --no-figure-crops
```

`--profile` 决定块的切分方式，这一步**决定了后面能不能做原位替换**：

- `inplace`（默认）：保持 PDF 的**自然块**，不做任何跨块合并。这是硬性要求——原位替换要往每个块的矩形里回填中文，块一旦跨栏，矩形就会横跨整页，无法使用。代价是段落被分栏切断时会拆成两块，翻译时也要拆成两段。
- `reflow`：跨栏的片段重新合并成完整段落，适合重排模式。

产出：

| 文件 | 内容 |
| --- | --- |
| `work/blocks.json` | 块模型：ID、页码、类型、坐标、原文、字号/加粗/颜色 |
| `work/figure_zones`（在 blocks.json 内） | 每页的插图区域矩形，原位替换时用来避免擦到图形 |
| `work/outline.txt` | 人类可读的结构大纲 |

**必做核对**：读 `work/outline.txt`，确认标题层级合理、阅读顺序连贯、图题编号连续。同时确认**没有块的矩形横跨分栏中线**（除首页单栏区和通栏图注外）。结构错了，后面全部白做。

### 第 2 步 · 术语与角色准备

1. 从 `title`、`abstract` 判断学科，按 `references/translator-personas.md` 选定译员角色。
2. 抽取 15–40 个高频专业词，结合 `references/translation-rules.md` 的术语表种子，建立项目术语表。
3. 先落盘 `work/translations.json` 的 `meta`、`terms`、`verbatim` 三段，再开始翻译。

```json
{
  "meta": { "title_zh": "……", "persona": "……", "layout": "inplace" },
  "verbatim": ["b0002", "b0010", "b0014"],
  "terms": [["HAIant", "HAIant", "系统名保留原文"]],
  "blocks": {}
}
```

`verbatim` 放不需要翻译、但要在右半保留英文原样的块：**作者姓名、DOI、自引文格式**。作者姓名保留拉丁原文是刻意的——中文名用字无法从拼音反推，猜错等于伪造。

### 第 3 步 · 分批翻译

**开始前必须从 `blocks.json` 重新导出待译清单**，并以这份清单为唯一依据：

```bash
python -c "
import json
m=json.load(open('work/blocks.json',encoding='utf-8'))
for b in m['blocks']:
    if b['kind'] in ('figure','reference'): continue
    print(f'<<<{b[\"id\"]}|{b[\"kind\"]}>>> {b[\"text\"]}')
" > work/to_translate.txt
```

> **最容易犯的错误**：拿上一次解析（或改脚本之前）留下的清单去翻译。块 ID 会随切分规则变化而整体移位，结果是覆盖率检查全绿、但译文内容整体错位一格。

- 每批 12–20 块或原文 ≤ 6000 字符。
- **逐批写回 `work/translations.json`**，不要等全部翻完再落盘。
- 遵守 `references/translation-rules.md`：术语一致、数字保真、交叉引用保真、专名保留。
- `figure` 块没有正文，`reference` 块整体不翻译，都不放进批次。
- `verbatim` 里的块不翻译。
- **原位模式下，被分栏切断的段落片段要分开译**：每段译文只填它自己的框，读者按左栏末→右栏首的顺序连起来读。若原文在栏末断在半个词上（`…by integrating ma-` / `chine learning…`），中文也照此在词中断开（`…通过整合机` / `器学习…`），这样接起来仍然通顺。

英文到中文的正常字符比约 **0.25–0.60**。低于 0.25 基本可断定译文被截断或与错位的行配对，必须回原文核对。

### 第 4 步 · 质检

```bash
python scripts/check.py --workdir work --json-out work/check.json
```

覆盖度、数字、交叉引用、图表编号、术语一致性逐项校验。**先清空所有 `ERR`**，`WARN` 逐条判断。

### 第 5 步 · 合成

**原位对照（默认）：**

```bash
python scripts/compose.py --workdir work
```

对每一页：新建一张宽度为原文两倍的页面 → 左半 `show_pdf_page` 原页 → 右半再 `show_pdf_page` 一次 → 在右半把每个可译块的矩形填白 → 把中文按原矩形自适应缩放回填。插图因为整页被复制，自然在左右各出现一次；图内标注不译，两侧都是英文。

会打印 `replaced / skipped / shrunk / overflow` 四项计数。`overflow` 必须为 0——不为 0 说明有块的译文挤不进原框，需要缩短译文或调整 `min_scale`。

**重排对照：**

```bash
python scripts/render.py --workdir work
```

产出 A3 横向的 `bilingual.html`（可预览）与 PDF，另附 `terms.md` 术语表。

用 `present_files` 把成品 PDF 交付用户。

### 可调参数

写进 `translations.json` 的 `options`，改完重跑第 5 步即可，无需重新解析或翻译。

| 选项 | 默认 | 适用 | 说明 |
| --- | --- | --- | --- |
| `line_height` | `1.42` | 原位 | 中文行距倍数 |
| `max_scale` | `1.10` | 原位 | 中文相对原字号的最大放大倍数 |
| `min_scale` | `0.72` | 原位 | 缩到这个比例仍放不下就会溢出，需改写译文 |
| `gap` | `0` | 原位 | 左右两半之间的间隙（pt），0 = 紧贴 |
| `divider` | `true` | 原位 | 中线是否画一条淡分隔线 |
| `keep_heading_color` | `true` | 原位 | 标题沿用原文颜色（蓝/绿/红），正文强制黑 |
| `page_width` / `page_height` | `420mm` / `297mm` | 重排 | A3 横向 |
| `en_size` / `cn_size` | `8.6pt` / `9.0pt` | 重排 | 正文字号 |
| `figure_max_height` | `218mm` | 重排 | 单张插图高度上限 |

## 输出规格

**原位对照**

- 页面 = 原文两倍宽 × 原文高（如 612×783pt → 1224×783pt = 432×276mm），页数与原文 1:1。
- 左半原页逐像素保留：分栏、页眉页脚、水印、插图、公式、参考文献全部原样。
- 右半同版式，正文/标题/图注为中文，参考文献、作者姓名、DOI、以及所有图内标注保持英文。
- 中文按每个原文本框自适应缩放，不会溢到相邻块。
- PDF 文字可复制可检索，中文用系统 TTF 子集嵌入。

**重排对照**

- A3 横向 420×297mm，左英文右中文，逐段对齐；插图通栏居中、图注中英对照；参考文献通栏保留原文。

## 硬约束

- **图内文字不译。** 热图色标、坐标轴、流程框内的英文一律保留。重绘科研图表极易错位，且破坏数据可追溯性。
- **参考文献不译。** 只在正文引用处保持标号一致。
- **作者姓名保留拉丁原文**，不从拼音反推中文用字。
- **不做 OCR。** 扫描件请先转成带文字层的 PDF。
- **译文仅供阅读参考**，学术引用应以原刊英文版为准。

## 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 右半某段中文缩得很小 | 译文比原文长，触到 `min_scale` | 压缩译文，或调低 `min_scale`（不低于 0.6，否则难读） |
| 中文盖住了插图 | 块的擦除矩形切进了图区 | `compose.py` 已按 `figure_zones` 收缩；若仍发生，检查该页 `figure_zones` 是否漏了区域 |
| 右半留白很多 | 译文比原文短 | 正常现象，原位模式不改版面；如需紧凑改用重排模式 |
| 某块根本没被替换 | 该块在 `verbatim` 里，或本来就无译文 | 预期行为；参考文献与作者名就是如此 |
| 中文显示为方块 | 没找到中文 TTF | 装一个中文字体，或确认 `compose.py` 的 `FONT_CANDIDATES` 覆盖了本机路径 |
| 段落在分栏处读不通 | 片段没按同一处断开 | 回第 3 步，让两个片段的译文在原文断开处也断开 |
| 输出体积过大 | 字体全量嵌入 | `compose.py` 已做子集化；若仍大，检查 `subset_fonts()` 是否报错 |
| 重排模式下插图撑爆一页 | 缺高度上限 | 调小 `figure_max_height` |

## 参考文件

- `references/translator-personas.md` —— 学科到译员角色的映射、角色提示词模板、批量节奏。
- `references/translation-rules.md` —— 术语处理、数字保真、交叉引用、语体、分栏片段处理、禁止事项、术语表种子。
