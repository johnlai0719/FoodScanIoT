#!/usr/bin/env python3
# 把 synth_corpus.py 產生的語料渲染成 rec 訓練用的文字行小圖。
#
# 輸出即 PaddleOCR 的 rec 通用格式（simple_dataset）：
#   images/000001.jpg\t水、蔗糖、柳丁原汁
# 圖與 label.txt 一起放 FoodScanData/train_rec/synth/，與評估集實體隔離。
#
# 退化條件刻意對齊測試集 cases.json 的 difficulty 標籤（glare / curved /
# crease / blurry）——那四個標籤是看真實照片歸納出來的，不是憑空想的失真類型。
# 對齊之後，「合成資料補強了哪一類」與「哪一類的實測分數上升」可以互相印證；
# 用一組沒對應關係的失真去訓練，事後就無從歸因。
#
# 只做橫排。直排（c57/c58/c59 那種杯裝標示）是 det 層的問題——rec 拿到的是
# 已切好的行圖，垂直排列的字被旋轉後會變成側躺，需要另一套處理，不在此範圍。
#
# 用法：
#   python synth_render.py --n=6000
#   python synth_render.py --n=200 --out=_preview --seed=1   # 先看幾張再放大量
import argparse
import os
import random
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.abspath(
    os.path.join(HERE, '..', 'FoodScanData', 'train_rec', 'synth'))

# 包裝標示以黑體為主，明體與楷體較少但存在（多見於品名與法規文字）。
# 權重照這個比例，不是平均——訓練分布偏離真實分布會浪費樣本。
FONTS = [('C:/Windows/Fonts/msjh.ttc', 5), ('C:/Windows/Fonts/msjhbd.ttc', 3),
         ('C:/Windows/Fonts/msyh.ttc', 3), ('C:/Windows/Fonts/mingliu.ttc', 2),
         ('C:/Windows/Fonts/kaiu.ttf', 1)]


def pick_font(rng, size):
    paths = [p for p, _ in FONTS]
    weights = [w for _, w in FONTS]
    for _ in range(5):
        p = rng.choices(paths, weights=weights)[0]
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()



# ── 材質背景 ────────────────────────────────────────────────────────────────
# 2026-09-02 加。**由來是一次量測**：舊合成圖上未微調 rec 的信心中位 0.974、
# 只有 16% 低於 0.9；真實行裁圖是 0.863、58% 低於 0.9。把詞彙從 380 擴到 978
# 之後重量，新圖是 **0.993 / 16%**——比舊的還簡單。
#
# 也就是說 08-18 的診斷是對的：**剩下的落差在種類不在強度**。`--deg-scale`
# 加的是幾何與雜訊，加不出「材質」。純色底畫黑字，對 rec 而言太乾淨。
#
# 這裡不用真實照片當底圖，理由是洩漏：rec 拿到的是 48px 行圖，背景佔大部分
# 像素，用測試商品的包裝底色去訓練，等於讓模型先看過那件商品的材質。
# 改成**程序化模擬**——包裝材質的成因是物理的，模擬得出來：
#   halftone  印刷網點（CMYK 半色調，有角度、有頻率）——一般紙盒與軟袋
#   foil      鋁箔／金屬鍍膜的異向反光條紋
#   gloss     亮面塑膠的寬幅高光
#   fiber     紙纖維與牛皮紙的細噪
# 再疊上曲面亮度漸層（圓罐、瓶身）。
MATERIALS = ('halftone', 'foil', 'gloss', 'fiber', 'flat')
MATERIAL_W = (0.34, 0.16, 0.20, 0.18, 0.12)


def material_bg(rng, w, h, base):
    """回傳 (h, w, 3) 的 float32 背景。base 是該材質的主色。"""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = np.zeros((h, w, 3), np.float32) + np.array(base, np.float32)
    kind = rng.choices(MATERIALS, weights=MATERIAL_W)[0]

    if kind == 'halftone':
        # 半色調網點：兩層不同角度的正弦相乘，模擬 CMYK 疊印的莫列波紋。
        # 頻率設在 0.25~0.9 週期/像素——太低看不出來，太高會被 JPEG 抹平。
        for _ in range(rng.randint(1, 2)):
            th = rng.uniform(0, 3.14)
            f = rng.uniform(0.25, 0.9)
            u = xx * np.cos(th) + yy * np.sin(th)
            v = -xx * np.sin(th) + yy * np.cos(th)
            dots = np.sin(u * f) * np.sin(v * f)
            a += (dots * rng.uniform(4, 14))[..., None]
    elif kind == 'foil':
        # 鋁箔：沿某個方向的細長高光條紋（異向反射），加上粗粒度亮度起伏
        th = rng.uniform(-0.5, 0.5)
        u = xx * np.cos(th) + yy * np.sin(th)
        streak = np.sin(u * rng.uniform(0.04, 0.16) + rng.uniform(0, 6.3))
        a += (np.clip(streak, 0, 1) ** 2 * rng.uniform(25, 60))[..., None]
        a += (np.sin(u * 0.9) * rng.uniform(3, 9))[..., None]
    elif kind == 'gloss':
        # 亮面塑膠：一道寬幅高光帶，位置與寬度隨機
        c = rng.uniform(0, w)
        wd = rng.uniform(w * 0.15, w * 0.5)
        band = np.exp(-((xx - c) ** 2) / (2 * wd ** 2))
        a += (band * rng.uniform(20, 55))[..., None]
    elif kind == 'fiber':
        # 紙纖維：各向同性細噪 ＋ 少量長纖維
        a += rng.uniform(3, 9) * np.random.randn(h, w, 1)
        for _ in range(rng.randint(2, 6)):
            y0 = rng.randint(0, h - 1)
            a[y0:y0 + 1, :, :] += rng.uniform(-8, 8)

    # 曲面亮度漸層：圓罐／瓶身中央亮、邊緣暗（或反過來）
    if rng.random() < 0.55:
        cx = rng.uniform(0.2, 0.8) * w
        g = np.exp(-((xx - cx) ** 2) / (2 * (w * rng.uniform(0.3, 0.8)) ** 2))
        a += ((g - 0.5) * rng.uniform(-30, 30))[..., None]

    return np.clip(a, 0, 255)


def base_image(rng, text, height):
    """先畫出乾淨的一行，四周留白讓後續的形變不會把字切掉。"""
    size = int(height * rng.uniform(0.62, 0.80))
    font = pick_font(rng, size)
    pad = int(height * 0.35)
    l, t, r, b = font.getbbox(text)
    w, h = max(r - l, 8) + pad * 2, max(b - t, 8) + pad * 2

    # 包裝底色五花八門：白紙、牛皮、深色印刷。對比度也跟著變。
    if rng.random() < 0.22:                       # 深底淺字
        bg = tuple(rng.randint(10, 80) for _ in range(3))
        fg = tuple(rng.randint(200, 255) for _ in range(3))
    else:
        bg = tuple(rng.randint(200, 255) for _ in range(3))
        fg = tuple(rng.randint(0, 90) for _ in range(3))

    # 背景改成程序化材質（見 MATERIALS 上方的說明）。
    # 對比度也一併降低：真實標示的字與底常常只差 60~120 灰階，
    # 純色底配 0~90 對 200~255 那種對比在真實照片上很少見。
    img = Image.fromarray(material_bg(rng, w, h, bg).astype(np.uint8))
    d = ImageDraw.Draw(img)
    d.text((pad - l, pad - t), text, font=font, fill=fg)
    return img, font, fg, bg



def deg_badcrop(img, rng, text, height, font, fg, bg):
    """壞框：det 在小字密集區框不準，rec 拿到的本來就不是乾淨的單行。

    **由來是把真實裁圖攤開來看**（`out/_real_crops.png`）。合成圖每一張都是
    完美置中、單行、四周留白；真實的則是——上下被切掉字頭字尾、一個框裡擠了
    三行、左右邊界帶進隔壁字的碎片。材質補了之後未微調 rec 的信心只從
    0.993 動到 0.987，離真實的 0.863 還很遠，**差距在框不在底色**。

    三種一起做，因為真實照片上它們也常常同時發生：
      tight   上下切掉一部分（負留白）
      bleed   上或下滲進相鄰行的一截
      side    左或右邊界切在字中間，留下半個字
    """
    a = np.asarray(img)
    h, w = a.shape[:2]
    out = Image.fromarray(a)

    # ① 上下切：真實的 det 框常常貼著字甚至切進去
    if rng.random() < 0.55:
        top = int(h * rng.uniform(0.0, 0.22))
        bot = int(h * rng.uniform(0.0, 0.22))
        if h - top - bot > 12:
            out = out.crop((0, top, w, h - bot))

    # ② 相鄰行滲入：在上方或下方貼一截別的文字
    if rng.random() < 0.40:
        nb = ImageDraw.Draw(Image.new('RGB', (1, 1)))
        other = ''.join(rng.choice(text) for _ in range(max(2, len(text) // 2)))             if text else '公克'
        strip_h = int(out.height * rng.uniform(0.18, 0.42))
        strip = Image.new('RGB', (out.width, strip_h), bg)
        d2 = ImageDraw.Draw(strip)
        # 只露出一截，所以往上／往下畫出界
        off = -strip_h if rng.random() < 0.5 else int(strip_h * 0.55)
        d2.text((rng.randint(-8, 8), off), other, font=font, fill=fg)
        canvas = Image.new('RGB', (out.width, out.height + strip_h), bg)
        if rng.random() < 0.5:
            canvas.paste(strip, (0, 0)); canvas.paste(out, (0, strip_h))
        else:
            canvas.paste(out, (0, 0)); canvas.paste(strip, (0, out.height))
        out = canvas

    # ③ 左右切在字中間
    if rng.random() < 0.35:
        cw = out.width
        l = int(cw * rng.uniform(0, 0.06))
        r = int(cw * rng.uniform(0, 0.06))
        if cw - l - r > 16:
            out = out.crop((l, 0, cw - r, out.height))
    return out


def deg_curved(img, rng):
    """圓柱面：水平方向愈往邊緣壓縮愈多。泡麵杯、瓶身、罐身都是這樣。

    正向關係是「紋理上等距的一欄，投影到螢幕是 sin(θ)」，所以取樣時要用它的
    反函數。第一版誤用 sin 當取樣映射（等於用了反函數的反函數），結果是邊緣
    被**拉伸**而非壓縮，「脫」會被撕成「月兌」——圖上顯示的字與 label 不符，
    這種訓練對比不加退化更糟。

    θmax 上限刻意壓在 1.1 弧度（約 63°）：真實照片不會拍到杯子的整個半圓，
    而 x→±1 時 du/dx→∞ 會把邊緣字元壓成糊。有界的曲率才是有用的樣本。
    """
    a = np.asarray(img).astype(np.float32)
    w = a.shape[1]
    tmax = rng.uniform(0.45, 1.1)
    theta = np.linspace(-tmax, tmax, w)
    screen_of_tex = np.sin(theta) / np.sin(tmax)        # 紋理欄 → 螢幕位置
    screen = np.linspace(-1, 1, w)
    xi = np.interp(screen, screen_of_tex, np.arange(w)) # 反查：螢幕欄 → 紋理欄
    xi = np.clip(np.rint(xi).astype(np.int32), 0, w - 1)
    return Image.fromarray(a[:, xi].astype(np.uint8))


def deg_crease(img, rng):
    """收縮膜的皺褶：局部垂直位移 ＋ 一條亮邊。"""
    a = np.asarray(img).astype(np.float32)
    h, w = a.shape[:2]
    amp = rng.uniform(1.2, max(1.3, h * 0.09))
    period = rng.uniform(w / 5, w / 1.6)
    phase = rng.uniform(0, 6.28)
    shift = (amp * np.sin(2 * np.pi * np.arange(w) / period + phase)).astype(np.int32)
    out = np.empty_like(a)
    for col in range(w):
        out[:, col] = np.roll(a[:, col], shift[col], axis=0)
    # 褶痕本身的反光線
    c = rng.randint(0, w - 1)
    half = max(1, int(w * 0.012))
    lo, hi = max(0, c - half), min(w, c + half)
    out[:, lo:hi] = np.clip(out[:, lo:hi] * rng.uniform(1.25, 1.7), 0, 255)
    return Image.fromarray(out.astype(np.uint8))


def deg_glare(img, rng):
    """膜或印刷面的反光斑：一塊高斯亮區疊上去。"""
    a = np.asarray(img).astype(np.float32)
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = rng.uniform(0, w), rng.uniform(0, h)
    sx, sy = w * rng.uniform(0.08, 0.3), h * rng.uniform(0.3, 1.2)
    blob = np.exp(-(((xx - cx) / sx) ** 2 + ((yy - cy) / sy) ** 2))
    a = a + (blob * rng.uniform(60, 165))[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def deg_blurry(img, rng):
    return img.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.7)))


def deg_lowres(img, rng):
    """解析度損失——**這是最重要的一項**。

    第一版沒有它，300 張合成圖的未微調 CER 只有 0.23%（5576 字錯 13 字），
    訓練起來學不到東西。原因是直接用 48px 渲染，正好等於 rec 模型的輸入高度，
    全程沒有任何資訊損失；而真實照片裡讀錯的那些字，行高只有 15-25px
    （從 3024×4032 裁出小字行，再被放大到 48px），糊掉的是那一段路。

    所以這裡刻意走一趟「縮小再放大」，讓合成樣本經歷同一種資訊損失。
    """
    w, h = img.size
    # 下限 15px 是實測定出來的：11px 時長行會被壓到連未微調模型都吐空字串，
    # 那種樣本沒有監督訊號可言，只是雜訊。真實照片裡讀錯的字行高約 15-25px，
    # 對齊那個區間即可，再低就不是「難」而是「壞」。
    small_h = rng.randint(15, 28)
    small_w = max(4, int(w * small_h / h))
    down = img.resize((small_w, small_h), rng.choice(
        [Image.BILINEAR, Image.BICUBIC, Image.LANCZOS, Image.NEAREST]))
    return down.resize((w, h), rng.choice([Image.BICUBIC, Image.BILINEAR]))


def deg_jpeg(img, rng):
    """低品質 JPEG 的區塊瑕疵。App 會壓縮上傳，這條路徑真的存在。"""
    import io
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=rng.randint(18, 55))
    buf.seek(0)
    return Image.open(buf).convert('RGB')


DEGRADATIONS = {'curved': deg_curved, 'crease': deg_crease,
                'glare': deg_glare, 'blurry': deg_blurry,
                'lowres': deg_lowres, 'jpeg': deg_jpeg}
# 出現機率。lowres 給最高權重——它是把合成樣本的難度拉到接近真實照片的主因，
# 其餘四項對齊 cases.json 的 difficulty 標籤。
# 仍保留約三成完全乾淨的樣本：訓練集若全是壞圖，模型在清晰照片上會退步。
DEG_PROB = {'lowres': 0.55, 'curved': 0.22, 'crease': 0.16,
            'glare': 0.20, 'blurry': 0.18, 'jpeg': 0.25,
            # 真實裁圖幾乎每一張都有某種框的問題，機率設高
            'badcrop': 0.70}
# lowres 必須在幾何形變之後、模糊之前套用，否則「先糊再縮」會抵銷掉它的效果
# badcrop 夾在中間：曲面／皺褶／反光是**包裝上本來就有的**，
# 壞框是 det 在那之後切的，而 lowres／blur／jpeg 是拍攝與存檔造成的。
DEG_ORDER = ['curved', 'crease', 'glare', 'badcrop', 'lowres', 'blurry', 'jpeg']


def render(text, rng, height):
    img, font, fg, bg = base_image(rng, text, height)
    applied = []
    for name in DEG_ORDER:
        if rng.random() < DEG_PROB[name]:
            if name == 'badcrop':
                img = deg_badcrop(img, rng, text, height, font, fg, bg)
            else:
                img = DEGRADATIONS[name](img, rng)
            applied.append(name)
    if rng.random() < 0.5:                                    # 輕微旋轉
        img = img.rotate(rng.uniform(-1.8, 1.8), resample=Image.BICUBIC,
                         expand=False, fillcolor=(255, 255, 255))
    if rng.random() < 0.35:                                   # 對比不足
        img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.55, 0.9))
    if rng.random() < 0.4:                                    # 感測器雜訊
        a = np.asarray(img).astype(np.float32)
        a += np.random.normal(0, rng.uniform(2, 9), a.shape)
        img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    # 統一高度：PP-OCR rec 的輸入是 3×48×W，先縮到目標高度可避免二次重採樣
    w, h = img.size
    img = img.resize((max(8, int(w * height / h)), height), Image.LANCZOS)
    return img, applied


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', default=os.path.join(HERE, 'corpus', 'synth_lines.txt'))
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--n', type=int, default=0, help='0 = 全部語料')
    ap.add_argument('--height', type=int, default=48)
    ap.add_argument('--quality', type=int, default=88)
    ap.add_argument('--val-frac', type=float, default=0.08)
    ap.add_argument('--seed', type=int, default=20260815)
    # 劣化強度的全域倍率。加這個是因為量到合成資料**明顯比真實簡單**：
    # 未微調的 rec 在合成圖上信心中位 0.974、只有 16% 低於 0.9；
    # 真實行裁圖是 0.863 / 58%。訓練資料比真實簡單，模型就會過擬合到合成模式
    # （上一輪實測真實成分淨損 16 項）。用這個倍率掃到分布對齊再訓練。
    ap.add_argument('--deg-scale', type=float, default=1.0)
    a = ap.parse_args()

    if a.deg_scale != 1.0:
        for k in DEG_PROB:
            DEG_PROB[k] = min(0.95, DEG_PROB[k] * a.deg_scale)
        print(f'劣化機率 ×{a.deg_scale}：' +
              '、'.join(f'{k} {v:.2f}' for k, v in DEG_PROB.items()))
    if not os.path.exists(a.corpus):
        sys.exit(f'找不到語料 {a.corpus}，先跑 synth_corpus.py build')
    lines = [l.rstrip('\n') for l in open(a.corpus, encoding='utf-8') if l.strip()]
    if a.n:
        lines = lines[:a.n]
    out = os.path.abspath(a.out)
    imgdir = os.path.join(out, 'images')
    os.makedirs(imgdir, exist_ok=True)

    rng = random.Random(a.seed)
    np.random.seed(a.seed)
    stats, rows = {}, []
    for i, text in enumerate(lines):
        img, applied = render(text, rng, a.height)
        fn = f'{i:06d}.jpg'
        img.save(os.path.join(imgdir, fn), quality=a.quality)
        # label 用相對路徑，訓練 config 的 data_dir 指到 out 即可
        rows.append(f'images/{fn}\t{text}')
        for k in applied or ['(clean)']:
            stats[k] = stats.get(k, 0) + 1

    lp = os.path.join(out, 'label.txt')
    with open(lp, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(rows) + '\n')

    # 另外切 train/val。val 只用來在訓練途中選 checkpoint——**真正的驗收是
    # score_ocr.py 在 58 案真實照片上的數字**，合成資料上的 acc 再高也不代表
    # 真實表現，兩者的分布本來就不同。
    shuf = list(rows)
    random.Random(a.seed + 1).shuffle(shuf)
    nval = max(1, int(len(shuf) * a.val_frac))
    for name, part in (('val_list.txt', shuf[:nval]), ('train_list.txt', shuf[nval:])):
        with open(os.path.join(out, name), 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(part) + '\n')

    print(f'已產生 {len(rows)} 張 → {imgdir}')
    print(f'標註檔 → {lp}')
    print(f'切分 → train_list.txt {len(shuf) - nval} 筆 / val_list.txt {nval} 筆')
    print('退化分布：' + '、'.join(f'{k} {v}' for k, v in sorted(stats.items())))
    chars = sorted({c for l in lines for c in l})
    print(f'字元集：{len(chars)} 種')
    print('\n訓練 config 片段：')
    print(f"  Train.dataset.data_dir: {out}")
    print(f"  Train.dataset.label_file_list: [{lp}]")


if __name__ == '__main__':
    main()
