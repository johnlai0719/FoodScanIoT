#!/usr/bin/env python3
# 訓練成分切分模型：逐字二元邊界預測（B = 這個字是某個成分項目的開頭，I = 不是）。
#
# 規格與驗收標準見 SPEC_split_model.md。摘要：
#   目標    段內沒切開的那 13 個添加物（52 個漏失裡的 25%）
#   驗收    添加物層 F1 > 70.0%，精確度 >= 80%（約束，不是目標）
#   安全    正規化後輸出字元必須是輸入字元的子集
#
# 為什麼是 token classification 而不是 seq2seq：這個任務的輸出等於「輸入 ＋
# 邊界標記」，沒有一個字是新的。逐字分類**結構上不可能編造**，
# 而 seq2seq 可以（要靠事後檢查擋）。既有結論已經踩過「格式良好的錯誤比
# 隨機碎片更危險」這個坑（見 README 的 PaddleOCR-VL 段）。
#
# 為什麼從 ckiplab/bert-base-chinese-ws 起步：中文斷詞與本任務結構完全相同——
# 都是逐字二元邊界、都是繁體中文。從已經會找中文邊界的 checkpoint 開始，
# 比從 base 收斂快，8000 筆也更夠用。
#
# 長序列：BERT 上限 512，而成分段可能超過 600 字（c58 的 GT 是 596 字）。
# 用滑動視窗加重疊，合併時取**視窗中央**的預測——邊緣的上下文不完整。
# 不處理的話最難的 c57/c58/c59 會壞掉。
#
# 用法：
#   python train_split.py train --epochs=3
#   python train_split.py predict --preset=bertsplit
#   python sim_match_preset.py v6_hires__boxth0.4 bertsplit
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = "ckiplab/bert-base-chinese-ws"
OUTDIR = os.path.join(HERE, "output", "split_model")
MAXLEN = 512
STRIDE = 384          # 視窗前進量；重疊 128 字，合併時取中央


def load(path, limit=None):
    """讀合成資料。邊界位置是生成時就記下來的，不需要事後對齊。

    第一版是「用項目文字回去 input 裡找位置」，55% 的樣本對不上——
    字元混淆改了 input（多磷酸鈉→多碳酸鈉），項目文字自然找不到。
    改成 synth_split.py 生成時直接輸出 boundaries，零丟棄且完全精確。
    """
    rows = []
    with open(path, encoding="utf-8") as f:
        for i, ln in enumerate(f):
            if limit and i >= limit:
                break
            d = json.loads(ln)
            text = d["input"]
            lab = [0] * len(text)
            for b in d["boundaries"]:
                if 0 <= b < len(text):
                    lab[b] = 1
            rows.append((text, lab))
    return rows


def encode(tok, text, labels, maxlen=MAXLEN, stride=STRIDE):
    """切窗並把字元標籤對到 token。回傳多個視窗。"""
    out = []
    for start in range(0, max(1, len(text)), stride):
        chunk = text[start:start + maxlen - 2]
        if not chunk:
            break
        enc = tok(chunk, truncation=True, max_length=maxlen,
                  return_offsets_mapping=True)
        lab = []
        for k, (s, e) in enumerate(enc["offset_mapping"]):
            if e == s:                       # [CLS] / [SEP] / padding
                lab.append(-100)
            else:
                lab.append(labels[start + s] if start + s < len(labels) else 0)
        enc.pop("offset_mapping")
        enc["labels"] = lab
        out.append(dict(enc))
        if start + maxlen - 2 >= len(text):
            break
    return out


def cmd_train(a):
    import numpy as np
    import torch
    from torch.utils.data import Dataset
    from transformers import (AutoTokenizer, AutoModelForTokenClassification,
                              DataCollatorForTokenClassification,
                              TrainingArguments, Trainer)

    tok = AutoTokenizer.from_pretrained(MODEL)
    tr = load(os.path.join(HERE, "corpus", "split_train.jsonl"), a.limit)
    va = load(os.path.join(HERE, "corpus", "split_val.jsonl"), 400)
    print("訓練 %d 筆、驗證 %d 筆（對不上標籤的已丟棄）" % (len(tr), len(va)))

    class DS(Dataset):
        def __init__(self, rows):
            self.x = []
            for t, l in rows:
                self.x.extend(encode(tok, t, l))

        def __len__(self):
            return len(self.x)

        def __getitem__(self, i):
            return self.x[i]

    dtr, dva = DS(tr), DS(va)
    print("切窗後：訓練 %d 視窗、驗證 %d 視窗" % (len(dtr), len(dva)))

    model = AutoModelForTokenClassification.from_pretrained(
        MODEL, num_labels=2, ignore_mismatched_sizes=True)

    def metrics(p):
        pr = np.argmax(p.predictions, -1)
        lb = p.label_ids
        m = lb != -100
        tp = int(((pr == 1) & (lb == 1) & m).sum())
        fp = int(((pr == 1) & (lb == 0) & m).sum())
        fn = int(((pr == 0) & (lb == 1) & m).sum())
        rc = tp / (tp + fn) if tp + fn else 0
        pe = tp / (tp + fp) if tp + fp else 0
        return {"boundary_recall": rc, "boundary_precision": pe,
                "boundary_f1": 2 * rc * pe / (rc + pe) if rc + pe else 0}

    args = TrainingArguments(
        output_dir=OUTDIR, num_train_epochs=a.epochs,
        per_device_train_batch_size=a.bs, per_device_eval_batch_size=a.bs,
        # transformers 5.x 拿掉了 warmup_ratio，改用 warmup_steps
        learning_rate=a.lr, warmup_steps=100, weight_decay=0.01,
        eval_strategy="epoch", save_strategy="epoch",
        load_best_model_at_end=True, metric_for_best_model="boundary_f1",
        logging_steps=50, report_to=[], fp16=torch.cuda.is_available(),
        save_total_limit=2)
    trainer = Trainer(model=model, args=args, train_dataset=dtr,
                      eval_dataset=dva, compute_metrics=metrics,
                      data_collator=DataCollatorForTokenClassification(tok))
    trainer.train()
    trainer.save_model(OUTDIR)
    tok.save_pretrained(OUTDIR)
    print("模型存到 %s" % OUTDIR)
    print("注意：這裡的 boundary_f1 是**合成驗證集**上的字元級指標，"
          "不是驗收指標。驗收要跑 predict 再用 sim_match_preset.py。")


def predict_text(model, tok, text, device, thresh=0.5):
    """滑動視窗預測，重疊處取視窗中央的那一份。"""
    import numpy as np
    import torch
    score = np.zeros(len(text))
    weight = np.zeros(len(text))
    for start in range(0, max(1, len(text)), STRIDE):
        chunk = text[start:start + MAXLEN - 2]
        if not chunk:
            break
        enc = tok(chunk, truncation=True, max_length=MAXLEN,
                  return_offsets_mapping=True, return_tensors="pt")
        off = enc.pop("offset_mapping")[0].tolist()
        with torch.no_grad():
            logits = model(**{k: v.to(device) for k, v in enc.items()}).logits[0]
        prob = torch.softmax(logits, -1)[:, 1].cpu().numpy()
        n = len(chunk)
        for k, (s, e) in enumerate(off):
            if e == s or start + s >= len(text):
                continue
            # 靠近視窗中央的權重高——邊緣的上下文不完整
            w = 1.0 - abs((s - n / 2) / (n / 2 + 1e-9)) * 0.9
            score[start + s] += prob[k] * w
            weight[start + s] += w
        if start + MAXLEN - 2 >= len(text):
            break
    p = np.divide(score, np.maximum(weight, 1e-9))
    cuts = [i for i in range(len(text)) if p[i] >= thresh]
    if not cuts or cuts[0] != 0:
        cuts = [0] + cuts
    items = []
    for i, c in enumerate(cuts):
        end = cuts[i + 1] if i + 1 < len(cuts) else len(text)
        s = text[c:end].strip(" 、,，;；·\n")
        if s:
            items.append(s)
    return items


def cmd_predict(a):
    import torch
    from transformers import AutoTokenizer, AutoModelForTokenClassification
    import bench_ingredients as BI
    import score_ocr as S

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(OUTDIR)
    model = AutoModelForTokenClassification.from_pretrained(OUTDIR).to(device)
    model.eval()

    BI.BOXES = a.src
    cases = BI.load_cases()
    outdir = os.path.join(HERE, "out", a.preset)
    os.makedirs(outdir, exist_ok=True)
    ndrop = 0
    for i, (cid, d, gt) in enumerate(cases, 1):
        # 與現有抽取器同樣的輸入：先用規則定位成分段（範圍 A1），再交給模型切
        seg = BI.p_union(cid, d, gt) or []
        text = "、".join(seg) if seg else BI.full_text(d)[:2000]
        items = predict_text(model, tok, text, device, a.thresh)
        full = S.normalize(text, fold_variants=True)
        kept = []
        for x in items:
            k = S.normalize(x, fold_variants=True)
            if k and k in full:
                kept.append(x)
            else:
                ndrop += 1
        rec = {"case_id": cid, "preset": a.preset,
               "set_version": gt.get("set_version"),
               "category": gt.get("category"),
               "images": [{"path": "", "elapsed_s": 0, "n_lines": len(kept),
                           "lines": [{"text": x, "score": None, "box": None}
                                     for x in kept]}]}
        json.dump(rec, open(os.path.join(outdir, cid + ".json"), "w",
                            encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[%d/%d] %-28s %3d 項" % (i, len(cases), cid, len(kept)))
    print("\n完成，丟掉 %d 項無依據輸出 -> out/%s/" % (ndrop, a.preset))
    print("驗收： python sim_match_preset.py %s %s" % (a.src, a.preset))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--epochs", type=float, default=3)
    t.add_argument("--bs", type=int, default=16)
    t.add_argument("--lr", type=float, default=3e-5)
    t.add_argument("--limit", type=int, default=None)
    p = sub.add_parser("predict")
    p.add_argument("--preset", default="bertsplit")
    p.add_argument("--src", default="v6_hires__boxth0.4")
    p.add_argument("--thresh", type=float, default=0.5)
    a = ap.parse_args()
    (cmd_train if a.cmd == "train" else cmd_predict)(a)


if __name__ == "__main__":
    main()
