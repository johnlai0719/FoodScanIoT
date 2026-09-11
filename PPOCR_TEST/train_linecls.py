#!/usr/bin/env python3
"""逐行分類器：判斷每一行 OCR 文字屬於成分／營養／保存／廠商／其他。

**目的是定位，不是辨識。** 本專案的天花板拆解是
    91.8% 成分文字有進到 OCR 全文 → 89.6% 完美定位下的切分上限 → 69.2% 實際
定位損失約 20 點、切分損失約 10 點。**定位是最大的單一槓桿。**

定位已經失敗過兩次，兩次都敗在同一件事：**分隔訊號是語意的，不是空間的**。
台灣標示把品名、內容量、成分、保存、注意事項、過敏原印在同一塊密集文字裡：
  - HalalBench 的 DBSCAN＋詞彙投票：−4.5 點，配對 bootstrap CI −9.7~−0.4，不跨 0
  - 視覺版面模型（PP-DocLayout 等）：類別是文件的（doc_title/text/table），
    食品包裝整個背面會被標成一塊 text
所以改在**文字**上做：OCR 已經把字讀出來了（det 實測 0 漏），
「原料：」這三個字讀得到——那正是分不開的那個訊號。

**訓練資料零人工標註**（`make_linecls.py`）：拿正解各欄位去對每一行 OCR 文字，
對得上 `ingredients_raw` 的就是成分行。這是弱標註，不是金標準。

**oracle 上限已量**：只留自動標成「成分」的行，正解成分原文的字元召回
從整頁的 74.6% 升到 83.0%（**+8.4 點**）。分類器一定比 oracle 差，
這 8.4 是天花板不是預期值。

## 兩個設計決定

**一、吃上下文，不只吃這一行。** 「脂肪」單獨一行分不出是成分還是營養表欄位名，
但前一行是「乳清蛋白粉、乳清粉」時就分得出。輸入是 `前一行 [SEP] 本行 [SEP] 後一行`。

**二、切分照商品分組，不是照行分組。** 同一案的行高度相關（同一份標示、
同一種版面），照行隨機切會讓驗證集裡有訓練集同一張圖的其他行，分數會虛高。
而且教授 08-29 明訂「同商品的所有影像必須在同一個 split」。

用法：
    .venv_torch/Scripts/python.exe train_linecls.py train
    .venv_torch/Scripts/python.exe train_linecls.py eval
"""
import argparse
import collections
import io
import json
import os
import random
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'out', 'linecls.jsonl')
OUTDIR = os.path.join(HERE, 'out', 'linecls_model')
MODEL = 'ckiplab/bert-base-chinese-ws'
LABELS = ['成分', '營養', '過敏原', '品名', '廠商', '保存', '日期', '份量',
          '注意', '認證', '其他']
L2I = {l: i for i, l in enumerate(LABELS)}
MAXLEN = 128


def brand_of(cid):
    """商品分組鍵。同品牌／同系列的案例必須同時落在同一邊——
    它們的版面幾乎相同，跨 split 就是洩漏。"""
    n = re.sub(r'^c\d+_', '', cid)
    for k in ('滿漢大餐', '拉麵道', '來一客', '阿Q', '雙響炮', '老媽拌麵', '一度讚',
              '麥香', '純喫茶', '飲冰室', '義美', '光泉', '統一', '品客', '多力多滋',
              '湖池屋', '優質蛋白奶', '冰鎮', '每日C', '樂事', '米香', '極饕',
              '乖乖', '飛壘', '茶裏王', 'Dr.Milker', '多多', '萬波'):
        if k in n:
            return k
    return n


def load():
    rows = [json.loads(l) for l in io.open(DATA, encoding='utf-8')]
    by = collections.defaultdict(list)
    for r in rows:
        by[r['case_id']].append(r)
    # 加上下文（同一案內、依原本的行序）
    out = []
    for cid, rs in by.items():
        for i, r in enumerate(rs):
            prev = rs[i - 1]['text'] if i > 0 else ''
            nxt = rs[i + 1]['text'] if i + 1 < len(rs) else ''
            out.append({'case_id': cid, 'brand': brand_of(cid),
                        'text': r['text'], 'prev': prev, 'next': nxt,
                        'label': r['label'], 'dist': r['dist']})
    return out


def load_reader(preset):
    """從**任一讀取器**的輸出建出同格式的列，弱標註沿用 `make_linecls.label`。

    為什麼要有這個：分類器是在 PP-OCR 的行上訓練的，但管線真正拿去抽成分的
    是 HunyuanOCR 的文字。2026-09-11 實測，**同一個模型套到 Hunyuan 的行上
    召回反而更高**（0.843 vs 0.696 @0.5）——因為它讀的是裁切區，沒有整頁的
    食譜／廣告／地址當干擾，而且不會把長行切碎。

    ⚠ 預測 Hunyuan 的行必須**照同一折**做：模型若在該案的 PP-OCR 行上訓練過，
       再去預測同一案的 Hunyuan 行就是洩漏（同樣的標示內容換個讀取器而已）。
    """
    import make_linecls as ML
    import score_ocr as _S
    cases = json.load(io.open(os.path.join(_S.EVAL_ROOT, 'cases.json'),
                              encoding='utf-8'))['cases']
    out = []
    for c in cases:
        cid = c['case_id']
        gt = _S.load_gt(cid, c.get('category') or '')
        if not gt or not gt.get('is_food_label', True):
            continue
        fp = os.path.join(HERE, 'out', preset, cid + '.json')
        if not os.path.exists(fp):
            continue
        d = json.load(io.open(fp, encoding='utf-8'))
        texts = [t for im in (d.get('images') or [])
                 for t in ((ln.get('text') or '').strip()
                           for ln in (im.get('lines') or []))
                 if len(_S.normalize(t)) >= 2]
        for i, t in enumerate(texts):
            tag, r = ML.label(t, gt)
            out.append({'case_id': cid, 'brand': brand_of(cid), 'text': t,
                        'prev': texts[i - 1] if i else '',
                        'next': texts[i + 1] if i + 1 < len(texts) else '',
                        'label': tag, 'dist': r})
    return out


def split(rows, frac=0.25, seed=42):
    """照品牌分組切分。回傳 (train, val)。"""
    brands = sorted({r['brand'] for r in rows})
    rnd = random.Random(seed)
    rnd.shuffle(brands)
    n = max(1, int(len(brands) * frac))
    va = set(brands[:n])
    return ([r for r in rows if r['brand'] not in va],
            [r for r in rows if r['brand'] in va])


def cmd_train(a):
    import numpy as np
    import torch
    from torch.utils.data import Dataset
    from transformers import (AutoTokenizer, AutoModelForSequenceClassification,
                              TrainingArguments, Trainer)

    rows = load()
    tr, va = split(rows)
    print('訓練 %d 行（%d 案）｜驗證 %d 行（%d 案）'
          % (len(tr), len({r['case_id'] for r in tr}),
             len(va), len({r['case_id'] for r in va})))
    c = collections.Counter(r['label'] for r in tr)
    print('訓練集類別：', '　'.join('%s %d' % (k, v) for k, v in c.most_common()))

    tok = AutoTokenizer.from_pretrained(MODEL)

    class DS(Dataset):
        def __init__(self, rs):
            self.rs = rs

        def __len__(self):
            return len(self.rs)

        def __getitem__(self, i):
            r = self.rs[i]
            e = tok(r['prev'] + ' [SEP] ' + r['text'] + ' [SEP] ' + r['next'],
                    truncation=True, max_length=MAXLEN, padding='max_length')
            e['labels'] = L2I[r['label']]
            return {k: torch.tensor(v) for k, v in e.items()}

    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL, num_labels=len(LABELS), ignore_mismatched_sizes=True)

    def metrics(p):
        pr = np.argmax(p.predictions, -1)
        lb = p.label_ids
        out = {'acc': float((pr == lb).mean())}
        # 成分那一類單獨報——那是這個模型存在的理由，其餘類別只是幫它排除
        i = L2I['成分']
        tp = int(((pr == i) & (lb == i)).sum())
        fp = int(((pr == i) & (lb != i)).sum())
        fn = int(((pr != i) & (lb == i)).sum())
        rc = tp / (tp + fn) if tp + fn else 0.0
        pe = tp / (tp + fp) if tp + fp else 0.0
        out.update({'ing_recall': rc, 'ing_precision': pe,
                    'ing_f1': 2 * rc * pe / (rc + pe) if rc + pe else 0.0})
        return out

    args = TrainingArguments(
        output_dir=OUTDIR, num_train_epochs=a.epochs,
        per_device_train_batch_size=32, per_device_eval_batch_size=64,
        # transformers 5.x 拿掉了 warmup_ratio，改用 warmup_steps
        learning_rate=3e-5, warmup_steps=50, weight_decay=0.01,
        eval_strategy='epoch', save_strategy='epoch',
        load_best_model_at_end=True, metric_for_best_model='ing_f1',
        logging_steps=25, report_to=[], seed=42, fp16=torch.cuda.is_available())
    t = Trainer(model=model, args=args, train_dataset=DS(tr),
                eval_dataset=DS(va), compute_metrics=metrics)
    t.train()
    t.save_model(OUTDIR)
    tok.save_pretrained(OUTDIR)
    print('\n模型存到', OUTDIR)


def cmd_eval(a):
    """驗證集上的兩件事：逐行分類的分數，以及**下游真正在意的字元召回**。

    逐行 F1 高不代表定位有用——關鍵是「只留預測為成分的行」之後，
    正解成分原文還找得回多少。那個數字才跟 +8.4 點的 oracle 上限可比。
    """
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    sys.path.insert(0, HERE)
    import score_ocr as S
    import glob

    tok = AutoTokenizer.from_pretrained(OUTDIR)
    model = AutoModelForSequenceClassification.from_pretrained(OUTDIR)
    model.eval()
    if torch.cuda.is_available():
        model.cuda()

    rows = load()
    _, va = split(rows)
    preds = []
    with torch.no_grad():
        for i in range(0, len(va), 64):
            b = va[i:i + 64]
            e = tok([r['prev'] + ' [SEP] ' + r['text'] + ' [SEP] ' + r['next']
                     for r in b], truncation=True, max_length=MAXLEN,
                    padding=True, return_tensors='pt')
            if torch.cuda.is_available():
                e = {k: v.cuda() for k, v in e.items()}
            preds.extend(model(**e).logits.argmax(-1).tolist())

    gts = {os.path.basename(f)[:-5]: json.load(io.open(f, encoding='utf-8'))
           for f in glob.glob(os.path.join(S.EVAL_ROOT, 'ground_truth', '*', '*.json'))}
    by = collections.defaultdict(lambda: {'all': [], 'gold': [], 'pred': []})
    for r, p in zip(va, preds):
        b = by[r['case_id']]
        b['all'].append(r['text'])
        if r['label'] == '成分':
            b['gold'].append(r['text'])
        if LABELS[p] == '成分':
            b['pred'].append(r['text'])

    def rec(gt, txt):
        a_, b_ = S.normalize(gt), S.normalize(''.join(txt))
        if not b_:
            return 0.0
        return 1 - S.substring_edit(a_, b_)[0] / len(a_)

    res = []
    for cid, b in by.items():
        gt = S.normalize((gts.get(cid) or {}).get('ingredients_raw') or '')
        if len(gt) < 20:
            continue
        res.append((cid, rec(gt, b['all']), rec(gt, b['gold']), rec(gt, b['pred'])))

    import statistics as st
    n = len(res)
    print('驗證集 %d 案（品牌與訓練集不重疊）' % n)
    print('-' * 52)
    print('正解成分原文的字元召回')
    print('  整頁 OCR 全文            %.1f%%   基準' % (100 * st.mean(r[1] for r in res)))
    print('  只留 oracle 標的成分行    %.1f%%   上限' % (100 * st.mean(r[2] for r in res)))
    print('  只留**模型預測**的成分行   %.1f%%   ← 這個才是實際能拿到的'
          % (100 * st.mean(r[3] for r in res)))
    base = st.mean(r[1] for r in res)
    print('-' * 52)
    print('相對整頁：oracle %+.1f 點｜模型 %+.1f 點'
          % (100 * (st.mean(r[2] for r in res) - base),
             100 * (st.mean(r[3] for r in res) - base)))
    print('\n模型比整頁差的案例：')
    for cid, al, go, pr in sorted(res, key=lambda x: x[3] - x[1])[:6]:
        print('  %-30s 整頁 %.2f → 模型 %.2f （oracle %.2f）' % (cid[:28], al, pr, go))


def cmd_cv(a):
    """K 折交叉驗證，寫出**折外預測**（out-of-fold）。

    為什麼非做不可：分類器在 43 案上訓練過，拿全 57 案去跟 `p_boxsep` 比
    等於作弊——那 43 案的預測是「看過答案」的。而只用 14 案的驗證集，
    n 太小，配對 bootstrap 的 CI 會寬到什麼都判不了。

    K 折讓每一案的預測都來自**沒看過它的模型**，於是全 57 案都可以誠實地比。
    折的切分依商品分組（同品牌不跨折），與教授 08-29 的防洩漏要求一致。

    輸出：`out/linecls_pred/{case_id}.json`，每行一個 P(成分)。
    存成檔案是因為 torch 與 paddle 不能共存於同一個環境
    （見 ocr-layer-exhausted 的環境衝突）——bench_ingredients 走 paddle，
    讀預存結果即可，逐行分類是純文字到純文字，預存不失去任何東西。
    """
    import numpy as np
    import torch
    from torch.utils.data import Dataset
    from transformers import (AutoTokenizer, AutoModelForSequenceClassification,
                              TrainingArguments, Trainer)

    rows = load()
    brands = sorted({r['brand'] for r in rows})
    rnd = random.Random(42); rnd.shuffle(brands)
    folds = [set(brands[i::a.k]) for i in range(a.k)]
    outdir = os.path.join(HERE, 'out', 'linecls_pred')
    os.makedirs(outdir, exist_ok=True)
    tok = AutoTokenizer.from_pretrained(MODEL)

    class DS(Dataset):
        def __init__(self, rs): self.rs = rs
        def __len__(self): return len(self.rs)
        def __getitem__(self, i):
            r = self.rs[i]
            e = tok(r['prev'] + ' [SEP] ' + r['text'] + ' [SEP] ' + r['next'],
                    truncation=True, max_length=MAXLEN, padding='max_length')
            e['labels'] = L2I[r['label']]
            return {k: torch.tensor(v) for k, v in e.items()}

    # 同一折也對其他讀取器的行做預測，寫到 linecls_pred_<preset>/
    extra = {}
    for pre in (a.also or []):
        extra[pre] = load_reader(pre)
        os.makedirs(os.path.join(HERE, 'out', 'linecls_pred_' + pre), exist_ok=True)
        print('另外預測 %s：%d 行' % (pre, len(extra[pre])))
    prob = {}
    prob_extra = {pre: {} for pre in extra}
    for f, va_brands in enumerate(folds, 1):
        tr = [r for r in rows if r['brand'] not in va_brands]
        va = [r for r in rows if r['brand'] in va_brands]
        print('\n── 第 %d/%d 折：訓練 %d 行、預測 %d 行 ──'
              % (f, a.k, len(tr), len(va)), flush=True)
        model = AutoModelForSequenceClassification.from_pretrained(
            MODEL, num_labels=len(LABELS), ignore_mismatched_sizes=True)
        args = TrainingArguments(
            output_dir=os.path.join(HERE, 'out', '_cv_tmp'),
            num_train_epochs=a.epochs, per_device_train_batch_size=32,
            learning_rate=3e-5, warmup_steps=50, weight_decay=0.01,
            eval_strategy='no', save_strategy='no', logging_steps=1000,
            report_to=[], seed=42, fp16=torch.cuda.is_available(),
            disable_tqdm=True)
        Trainer(model=model, args=args, train_dataset=DS(tr)).train()
        model.eval()
        if torch.cuda.is_available():
            model.cuda()
        with torch.no_grad():
            for i in range(0, len(va), 64):
                b = va[i:i + 64]
                e = tok([r['prev'] + ' [SEP] ' + r['text'] + ' [SEP] ' + r['next']
                         for r in b], truncation=True, max_length=MAXLEN,
                        padding=True, return_tensors='pt')
                if torch.cuda.is_available():
                    e = {k: v.cuda() for k, v in e.items()}
                # ⚠ **十一類的機率全部存下來，不是只存 P(成分)。**
                #    2026-09-11 之前只存 `p`，於是品名／廠商／過敏原那幾類
                #    的機率被丟掉——想用它們挑行時得整個重跑 CV（25 分鐘）。
                #    `p` 保留原名以相容既有讀取端（`bench_ingredients._linecls_text`、
                #    `run_vlcrop._linecls_probs`、`crop_linecls.py`）。
                all_p = torch.softmax(model(**e).logits, -1).tolist()
                for r, row in zip(b, all_p):
                    prob.setdefault(r['case_id'], []).append(
                        {'text': r['text'], 'p': round(row[L2I['成分']], 4),
                         'ps': {lb: round(row[L2I[lb]], 4) for lb in LABELS}})
            # 同一折的保留品牌，其他讀取器的行也一起預測（維持折外）
            for pre, rs in extra.items():
                vb = [r for r in rs if r['brand'] in va_brands]
                for i in range(0, len(vb), 64):
                    b = vb[i:i + 64]
                    e = tok([r['prev'] + ' [SEP] ' + r['text'] + ' [SEP] ' + r['next']
                             for r in b], truncation=True, max_length=MAXLEN,
                            padding=True, return_tensors='pt')
                    if torch.cuda.is_available():
                        e = {k: v.cuda() for k, v in e.items()}
                    ap = torch.softmax(model(**e).logits, -1).tolist()
                    for r, row in zip(b, ap):
                        prob_extra[pre].setdefault(r['case_id'], []).append(
                            {'text': r['text'], 'p': round(row[L2I['成分']], 4),
                             'ps': {lb: round(row[L2I[lb]], 4) for lb in LABELS}})
        del model
        torch.cuda.empty_cache()

    for cid, v in prob.items():
        json.dump(v, io.open(os.path.join(outdir, cid + '.json'), 'w',
                             encoding='utf-8'), ensure_ascii=False)
    print('\n折外預測寫出 %d 案 → %s' % (len(prob), outdir))
    for pre, pv in prob_extra.items():
        od = os.path.join(HERE, 'out', 'linecls_pred_' + pre)
        for cid, v in pv.items():
            json.dump(v, io.open(os.path.join(od, cid + '.json'), 'w',
                                 encoding='utf-8'), ensure_ascii=False)
        print('折外預測寫出 %d 案 %s %s' % (len(pv), chr(0x2192), od))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('train'); t.add_argument('--epochs', type=int, default=6)
    sub.add_parser('eval')
    c = sub.add_parser('cv')
    c.add_argument('--k', type=int, default=4)
    c.add_argument('--epochs', type=int, default=6)
    c.add_argument('--also', nargs='*', default=[],
                   help='同一折另外預測哪些讀取器的行，例：--also vlcrop_hy_v4')
    a = ap.parse_args()
    {'train': cmd_train, 'eval': cmd_eval, 'cv': cmd_cv}[a.cmd](a)
