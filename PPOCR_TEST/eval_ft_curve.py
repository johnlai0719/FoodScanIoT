#!/usr/bin/env python3
"""學習曲線評估：各個訓練規模的權重，在**同一組固定的評估集**上比。

⚠ 全程在汙染區內（`out/_contaminated_*`）。訓練與測試都取自那 134 對，
不碰評分管線、不產生任何要寫進報告的數字。**輸出是一條斜率，不是成績。**

問的問題只有一個：**加資料會不會有用。**
  還在陡升  → 有用，斜率可外推出大概要多少
  已經走平  → 沒用，問題不在資料量
  沒有起色  → 這個任務不可學，省下拍照的力氣

## 三條指標，最後一條是安全閥

**相似度**（字元序列比對）：泛化初期不會逐字背對，先看有沒有靠近。
⚠ 它跟報告裡的任何數字都**不可比**——`0.775` 不是 `F1 0.775`。

**長度比**（輸出字數 ÷ 正解字數）：原始模型的主要毛病是把整塊倒出來
（`c103` 181 字對正解 15 字、`c106` 590 字對 228 字），這個比值最直接
反映有沒有學會「在哪結束」。

**佐證率**（輸出的字有多少出現在 PP-OCR 讀到的全文裡）：**這條是安全閥。**
模型可以靠兩種方式讓相似度上升——真的讀得更準，或**學會這類商品通常
含什麼然後補上去**。後者在這個評估裡也會加分（補的東西常常剛好對），
但那是猜對不是讀對，換一個沒見過的商品就垮。

    相似度↑ 佐證率不變   真的讀得更準
    相似度↑ 佐證率↓     開始編了，資料再多也是負債

這正是 Gemini 出事的地方：它知道某個牌子通常誰做的，`name` 有 47 案填了
照片上不存在的字。判準見 [[凍結前決策單#0]]（輸出的每個字都要有影像來源）。
"""
import argparse, io, json, os, sys, difflib, statistics, re, unicodedata, torch
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import train_ft_overfit as T
from PIL import Image
from transformers import AutoProcessor, AutoModelForImageTextToText
from peft import PeftModel

EVAL = os.path.abspath(os.path.join(HERE, '..', '測試', '量化測試'))
READERS = ('v6_best', 'vlcrop_hy_v4')


def norm(s):
    s = unicodedata.normalize('NFKC', str(s or ''))
    return re.sub(r'[\s　·、,，.。:：;；()（）\[\]「」【】/／\-_®™©]', '', s).lower()


def ocr_text(cid):
    """兩個讀取器讀到的全文，用來判斷輸出有沒有影像佐證。"""
    t = ''
    for pre in READERS:
        p = os.path.join(HERE, 'out', pre, cid + '.json')
        if os.path.exists(p):
            d = json.load(io.open(p, encoding='utf-8'))
            t += '\n'.join(l.get('text') or '' for im in d.get('images', [])
                           for l in im.get('lines', []))
    return norm(t)


def grounded_ratio(out, txt, k=4):
    """輸出切成 k 字視窗，有多少比例出現在 OCR 全文裡。

    用視窗而不是整串：整串幾乎不可能完全命中，逐字又太寬鬆
    （單一個「水」到處都有）。4 字視窗夠長到有辨識力、短到容忍讀錯一兩字。
    """
    n = norm(out)
    if len(n) < k:
        return 1.0 if n and n in txt else 0.0
    win = [n[i:i + k] for i in range(len(n) - k + 1)]
    return sum(1 for w in win if w in txt) / len(win)


def gen(pr, m, row, maxpx, maxnew):
    img = Image.open(os.path.join(T.PAIRS, row['image'])).convert('RGB')
    img.thumbnail((maxpx, maxpx))
    head = T.BOS + T.IMG_TOK + T.PROMPT + T.USER
    inp = pr(text=[head], images=[img], return_tensors='pt').to(m.device)
    with torch.no_grad():
        g = m.generate(**inp, max_new_tokens=maxnew, do_sample=False)
    return pr.batch_decode(g[:, inp['input_ids'].shape[1]:],
                           skip_special_tokens=True)[0].strip()


def score(pr, m, held, texts, maxpx, maxnew, tag):
    sims, ratios, grd, exact, outs = [], [], [], 0, []
    for i, r in enumerate(held, 1):
        out = gen(pr, m, r, maxpx, maxnew)
        outs.append(out)
        sims.append(difflib.SequenceMatcher(None, out, r['text']).ratio())
        ratios.append(len(out) / max(1, len(r['text'])))
        grd.append(grounded_ratio(out, texts[r['case_id']]))
        exact += (out == r['text'])
        if i % 20 == 0:
            print('   %-10s %3d/%d  相似度 %.3f  佐證 %.3f'
                  % (tag, i, len(held), statistics.mean(sims), statistics.mean(grd)),
                  flush=True)
    return {'sim': statistics.mean(sims), 'ratio': statistics.median(ratios),
            'grounded': statistics.mean(grd), 'exact': exact, 'outputs': outs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ckpts', nargs='*', default=None,
                    help='要評的 checkpoint 目錄；預設自動找 _contaminated_lora_ckpt_n*')
    ap.add_argument('--limit', type=int, default=0, help='只測前幾案（除錯用）')
    ap.add_argument('--maxpx', type=int, default=896)
    ap.add_argument('--maxnew', type=int, default=900)
    ap.add_argument('--out', default=os.path.join(HERE, 'out', '_contaminated_curve.json'))
    a = ap.parse_args()

    rows = [json.loads(l) for l in io.open(os.path.join(T.PAIRS, 'pairs.jsonl'),
                                           encoding='utf-8')]
    sp = json.load(io.open(os.path.join(T.PAIRS, 'split.json'), encoding='utf-8'))
    held = [r for r in rows if r['case_id'] in set(sp['held'])]
    if a.limit:
        held = held[:a.limit]
    texts = {r['case_id']: ocr_text(r['case_id']) for r in held}

    cks = a.ckpts
    if not cks:
        import glob
        cks = sorted(glob.glob(os.path.join(HERE, 'out',
                                            '_contaminated_lora_ckpt_n*')),
                     key=lambda p: int(re.search(r'_n(\d+)$', p).group(1)))
    print('⚠ 汙染區實驗。固定評估集 %d 案，評 base ＋ %d 個 checkpoint\n'
          % (len(held), len(cks)), flush=True)

    pr = AutoProcessor.from_pretrained(T.MODEL)
    base = AutoModelForImageTextToText.from_pretrained(
        T.MODEL, dtype=torch.bfloat16, device_map='cuda').eval()

    res = [{'n': 0, **score(pr, base, held, texts, a.maxpx, a.maxnew, 'base')}]
    for ck in cks:
        n = int(re.search(r'_n(\d+)$', ck).group(1))
        meta = os.path.join(ck, 'curve_meta.json')
        ep = json.load(io.open(meta, encoding='utf-8'))['best_epoch'] \
            if os.path.exists(meta) else None
        m = PeftModel.from_pretrained(base, ck).eval()
        r = score(pr, m, held, texts, a.maxpx, a.maxnew, 'n=%d' % n)
        res.append({'n': n, 'best_epoch': ep, **r})
        m.unload()

    json.dump({'held': [r['case_id'] for r in held], 'points': res},
              io.open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n══ 學習曲線（固定 %d 案，全部沒訓練過）══' % len(held))
    print('%8s %8s %10s %8s %10s %10s'
          % ('訓練案數', '最佳ep', '相似度', '長度比', '佐證率', '完全一致'))
    for p in res:
        print('%8d %8s %10.3f %8.2f %10.3f %8d'
              % (p['n'], p.get('best_epoch') or '-', p['sim'], p['ratio'],
                 p['grounded'], p['exact']))
    print('\n判讀：相似度↑ 而佐證率不變 → 真的讀得更準；'
          '相似度↑ 但佐證率↓ → 開始編了，資料再多也是負債。')


if __name__ == '__main__':
    main()
