#!/usr/bin/env python3
# 把案例照片切成重疊的高解析磚塊、銳化後排成一頁，供人工轉錄正解時閱讀。
#
# 為何要有這支：泡麵杯、鋁袋這類商品的成分欄字小又密，直接看原圖得不斷縮放
# 平移，抄到一半就找不到剛才那一行。但原圖本身其實有足夠像素——4284×5712 的
# 照片縮到螢幕寬度後每個字只剩幾像素，讀不出來是顯示問題，不是照片問題。
# 切成磚塊以原生解析度呈現並銳化，多數「看不出來」的欄位就讀得出來了。
#
# **這只影響「人怎麼看圖」，不影響評分**：模型讀的仍是 images/ 的原圖，
# 產物放在 _zoom/（不進版控、不被 harness 讀取）。正解仍由人逐字轉錄——
# 放大是為了讓人讀得到，不是找別人代讀（成分正解曾採「Gemini 起草＋人工
# 校正」，因與受測對象同源有循環評估之虞而廢止，見 score_eval.py 的說明）。
#
# 用法：
#   python zoom_label.py c57_來一客(京燉肉骨風味)      # 單一案例
#   python zoom_label.py --new                          # 所有正解仍空白的案例
#   python zoom_label.py c57_... --cols=3 --rows=4       # 切更細（字更大）
import glob
import html
import json
import os
import sys

from PIL import Image, ImageEnhance, ImageFilter

import casetool

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「塩」「菓」…）。
# 沒有這兩行的後果不是印出亂碼，而是**整批任務在中途拋 UnicodeEncodeError 死掉**
# ——2026-09-07 run_eval 炸在第 10 案的「塩」、intake 炸在第 11 案的「菓」，
# 後者還留下半套用狀態（照片已搬、cases.json 沒存）。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
ZOOM = os.path.join(HERE, '_zoom')
OVERLAP = 0.12  # 磚塊重疊比例：切在字上時，相鄰磚塊仍能讀到完整那一行


def tiles_for(im, cols, rows):
    w, h = im.size
    tw, th = w / cols, h / rows
    ox, oy = tw * OVERLAP, th * OVERLAP
    for r in range(rows):
        for c in range(cols):
            box = (max(0, int(c * tw - ox)), max(0, int(r * th - oy)),
                   min(w, int((c + 1) * tw + ox)), min(h, int((r + 1) * th + oy)))
            yield r, c, im.crop(box)


def process(case, cols, rows):
    cid, cat, images = case
    out = os.path.join(ZOOM, cid)
    os.makedirs(out, exist_ok=True)
    blocks = []
    for src in images:
        sp = os.path.join(HERE, src)
        if not os.path.exists(sp):
            print(f"  [略過] 找不到 {src}")
            continue
        stem = os.path.splitext(os.path.basename(src))[0]
        with Image.open(sp) as im:
            im = im.convert('RGB')
            items = []
            for r, c, t in tiles_for(im, cols, rows):
                t = ImageEnhance.Contrast(t).enhance(1.45)
                t = t.filter(ImageFilter.UnsharpMask(radius=2.2, percent=170, threshold=2))
                fn = f'{stem}_r{r}c{c}.jpg'
                t.save(os.path.join(out, fn), quality=92)
                items.append(fn)
        blocks.append((os.path.basename(src), items))

    gt_rel = f'ground_truth/{cat}/{cid}.json'
    secs = ''.join(
        f'<h2>{html.escape(name)}</h2>'
        + ''.join(f'<img src="{html.escape(fn)}" loading="lazy">' for fn in items)
        for name, items in blocks)
    page = f"""<!doctype html><meta charset="utf-8">
<title>{html.escape(cid)} 放大</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0 auto; max-width: 1400px;
         padding: 0 12px 40px; }}
  h1 {{ font-size: 1.15em; }}
  h2 {{ font-size: .9em; color: #666; border-bottom: 1px solid #ddd;
        padding-bottom: 4px; margin-top: 28px; }}
  img {{ width: 100%; display: block; margin: 8px 0; border: 1px solid #eee; }}
  .hint {{ background: #fff6d6; border: 1px solid #e0c96a; padding: 8px 12px;
           border-radius: 8px; line-height: 1.6; font-size: .92em; }}
  code {{ background: #f2f2f2; padding: 1px 5px; border-radius: 3px; }}
</style>
<h1>{html.escape(cid)}</h1>
<div class="hint">
  磚塊有重疊，切在字上時看相鄰那張。讀不出來的欄位<b>留 null，不要猜</b>——
  null 代表「標示上沒有」，填錯的數字比空著更糟。<br>
  正解檔：<code>{html.escape(gt_rel)}</code>
</div>
{secs}"""
    idx = os.path.join(out, 'index.html')
    with open(idx, 'w', encoding='utf-8') as f:
        f.write(page)
    n = sum(len(i) for _, i in blocks)
    print(f"{cid}：{len(blocks)} 張照片 → {n} 塊  {os.path.relpath(idx, HERE)}")
    return idx


def load_cases(which):
    cases = json.load(open(casetool.CASES, encoding='utf-8'))['cases']
    by_id = {c['case_id']: c for c in cases}
    if which == '--new':
        # 正解仍空白（name 未填）者＝還沒轉錄的案例
        out = []
        for c in cases:
            gp = casetool.gt_path(c['case_id'], c['category'])
            if not os.path.exists(gp):
                continue
            gt = json.load(open(gp, encoding='utf-8'))
            if gt.get('is_food_label') and not gt.get('name'):
                out.append(c)
        return out
    if which not in by_id:
        sys.exit(f"cases.json 沒有 {which}")
    return [by_id[which]]


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit("用法：zoom_label.py <case_id> | --new [--cols=N] [--rows=N]")
    cols, rows, target = 2, 3, None
    for a in args:
        if a.startswith('--cols='):
            cols = int(a.split('=', 1)[1])
        elif a.startswith('--rows='):
            rows = int(a.split('=', 1)[1])
        elif a == '--new' or not a.startswith('--'):
            target = a
        else:
            sys.exit(f"看不懂的參數：{a}")
    if not target:
        sys.exit("請指定 case_id 或 --new")

    picked = load_cases(target)
    if not picked:
        sys.exit("沒有符合的案例（--new 找的是正解仍空白的食品案例）。")
    last = None
    for c in picked:
        last = process((c['case_id'], c['category'], c['images']), cols, rows)
    print(f"\n共 {len(picked)} 案。用瀏覽器開啟上面的 index.html 邊看邊抄。")
    if len(picked) == 1:
        print(f"  {last}")


if __name__ == '__main__':
    main()
