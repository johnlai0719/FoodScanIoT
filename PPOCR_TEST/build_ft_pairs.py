#!/usr/bin/env python3
"""把既有正解做成 VLM 微調用的（裁切圖 → 文字）配對。

⚠⚠ **這批資料來自評估集，任何用它訓練出來的權重都是汙染的。**
產出目錄叫 `_contaminated_` 開頭就是為了讓人一眼看見。

**唯一正當的用途是「過擬合測試」**——回答「我的訓練管線跑不跑得起來」，
那是一個關於程式的是非題，不是關於方法好壞的數字。判準是「它能不能背下
訓練的那幾案」，與測試集的成績無關，所以不會把資訊洩漏到驗收數字裡。

**規矩（違反其中任何一條，今天量到的所有數字就作廢）：**

1. 訓練出來的權重**永遠不拿去跑測試集**，也不產生任何要寫進報告的數字
2. 不用它的結果去決定任何方法選擇（換不換讀取器、用不用某個解析器…）
3. checkpoint 目錄與正常產出分開，並在檔名標明汙染

為什麼 1:1 才收：`ingredients_raw` 是**整個成分欄**的逐字轉錄，一案有多張圖
或多個成分區時，無法確定哪一段對應哪一張裁切圖，硬配會產生錯的標籤。
"""
import argparse, io, json, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
EVAL = os.path.abspath(os.path.join(HERE, '..', '測試', '量化測試'))
sys.path.insert(0, EVAL)
import casetool                       # noqa: E402
import run_vlcrop as RV               # noqa: E402
from PIL import Image                 # noqa: E402

OUT = os.path.join(HERE, 'out', '_contaminated_ft_pairs')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--boxes', default='vlcrop_hy_v4', help='拿哪一組的裁切框')
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    rows, skip = [], {'multi': 0, 'noraw': 0}
    for c in casetool.load_cases()['cases']:
        cid = c['case_id']
        gp = casetool.gt_path(cid, c['category'])
        vp = os.path.join(HERE, 'out', a.boxes, cid + '.json')
        if not (os.path.exists(gp) and os.path.exists(vp)):
            continue
        G = json.load(io.open(gp, encoding='utf-8'))
        raw = (G.get('ingredients_raw') or '').strip()
        if not G.get('is_food_label') or not raw:
            skip['noraw'] += 1
            continue
        V = json.load(io.open(vp, encoding='utf-8'))
        imgs = [im for im in V['images']
                if ((im.get('regions') or {}).get('ingredients') or {}).get('found')]
        if len(imgs) != 1:
            skip['multi'] += 1
            continue
        im = imgs[0]
        r = im['regions']['ingredients']
        src = os.path.join(EVAL, im['path'].replace('/', os.sep))
        if not os.path.exists(src):
            continue
        with Image.open(src) as pic:
            crop = RV.crop(pic.convert('RGB'), r['box'], r.get('rot') or 0)
        dst = os.path.join(OUT, cid + '.jpg')
        crop.save(dst, 'JPEG', quality=92)
        rows.append({'case_id': cid, 'image': os.path.basename(dst),
                     'text': raw, 'n_char': len(raw),
                     'set_version': c.get('set_version'),
                     'category': c.get('category')})
        if a.limit and len(rows) >= a.limit:
            break
    with io.open(os.path.join(OUT, 'pairs.jsonl'), 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print('寫出 %d 對 → %s' % (len(rows), OUT))
    print('  排除：多圖／多區 %d、無 raw %d' % (skip['multi'], skip['noraw']))
    print('  總字數 %d，中位 %d 字' % (sum(r['n_char'] for r in rows),
          sorted(r['n_char'] for r in rows)[len(rows)//2] if rows else 0))
    print('\n⚠ 這批是評估集資料。訓練出來的權重只能用於過擬合測試，'
          '不得產生任何要寫進報告的數字。')


if __name__ == '__main__':
    main()
