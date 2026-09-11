"""Generate the README layout schematics. Abstract mock-ups only, no real content."""
from PIL import Image, ImageDraw, ImageFont

CN_FONT = "C:/Windows/Fonts/msyh.ttc"
CN_BOLD = "C:/Windows/Fonts/msyhbd.ttc"

BG = (255, 255, 255)
INK = (34, 34, 34)
MUT = (150, 150, 150)
BORD = (190, 190, 190)
EN = (178, 178, 178)
ZH = (127, 119, 221)
FIG = (226, 240, 250)
FIGB = (120, 170, 215)
PAG = (250, 250, 250)


def f(size, bold=False):
    return ImageFont.truetype(CN_BOLD if bold else CN_FONT, size)


def lines(d, x, y, w, n, gap=9, h=5, color=EN):
    for _ in range(n):
        d.rounded_rectangle([x, y, x + w, y + h], 2, fill=color)
        y += h + gap
    return y


def mock(d, x, y, w, h, zh=False):
    d.rounded_rectangle([x, y, x + w, y + h], 4, fill=PAG, outline=BORD, width=2)
    px, py = x + 26, y + 20
    d.rounded_rectangle([px, py, px + w - 52, py + 7], 2, fill=MUT)
    py += 22
    col = ZH if zh else EN
    d.rounded_rectangle([px, py, px + int((w - 56) * 0.72), py + 13], 3,
                        fill=ZH if zh else INK)
    py += 26
    lines(d, px, py, w - 52, 2, gap=7, h=4, color=col)
    py += 46
    cw = (w - 70) // 2
    lines(d, px, py, cw, 7, gap=7, h=4, color=col)
    lines(d, px + cw + 18, py, cw, 7, gap=7, h=4, color=col)
    py += 74
    d.rounded_rectangle([px, py, px + w - 52, py + 92], 4, fill=FIG, outline=FIGB, width=2)
    d.text((px + (w - 52) // 2, py + 38), "Figure", font=f(15), fill=FIGB, anchor="mm")
    py += 104
    lines(d, px, py, w - 52, 7, gap=7, h=4, color=col)


def dual():
    img = Image.new("RGB", (1420, 620), BG)
    d = ImageDraw.Draw(img)
    d.text((40, 26), "原位对照  ·  in-place dual", font=f(25, True), fill=INK)
    d.text((40, 62), "一个输出页 = 一个原始页；宽度翻倍，页数与原文 1:1",
           font=f(15), fill=MUT)
    mock(d, 40, 104, 620, 470, zh=False)
    mock(d, 760, 104, 620, 470, zh=True)
    d.line([710, 104, 710, 574], fill=BORD, width=2)
    d.text((350, 592), "左半：原页原样复制，分栏 / 插图 / 页眉全不动",
           font=f(15), fill=MUT, anchor="mm")
    d.text((1070, 592), "右半：同一版式，仅文字原地换成中文",
           font=f(15), fill=MUT, anchor="mm")
    img.save("docs/dual-layout.png")
    print("docs/dual-layout.png", img.size)


def reflow():
    img = Image.new("RGB", (1420, 620), BG)
    d = ImageDraw.Draw(img)
    d.text((40, 26), "重排对照  ·  re-flowed side-by-side", font=f(25, True), fill=INK)
    d.text((40, 62), "A3 横向（两个 A4 并排），逐段对齐，页数随内容浮动",
           font=f(15), fill=MUT)
    X, Y, W, H = 40, 104, 1340, 470
    d.rounded_rectangle([X, Y, X + W, Y + H], 4, fill=PAG, outline=BORD, width=2)
    half = (W - 24) // 2
    for k, cx in enumerate([X + 16, X + 16 + half + 8]):
        zh = k == 1
        col = ZH if zh else EN
        px, py = cx + 22, Y + 22
        d.rounded_rectangle([px, py, px + 180, py + 7], 2, fill=MUT)
        py += 20
        d.rounded_rectangle([px, py, px + int(half * 0.62), py + 13], 3,
                            fill=ZH if zh else INK)
        py += 30
        for _ in range(4):
            lines(d, px, py, half - 44, 2, gap=6, h=4, color=col)
            py += 24
            lines(d, px, py, half - 44, 3, gap=6, h=4, color=col)
            py += 38
            lines(d, px, py, half - 44, 2, gap=6, h=4, color=col)
            py += 22
    d.rounded_rectangle([X + 16, Y + 326, X + W - 16, Y + 396], 4,
                        fill=FIG, outline=FIGB, width=2)
    d.text((X + W // 2, Y + 361), "Figure  ·  通栏居中，图注中英对照",
           font=f(15), fill=FIGB, anchor="mm")
    d.text((X + W // 4, Y + 448), "英文原文", font=f(15), fill=MUT, anchor="mm")
    d.text((X + W * 3 // 4, Y + 448), "中文译文", font=f(15), fill=MUT, anchor="mm")
    img.save("docs/reflow-layout.png")
    print("docs/reflow-layout.png", img.size)


if __name__ == "__main__":
    dual()
    reflow()
