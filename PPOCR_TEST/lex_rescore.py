#!/usr/bin/env python3
"""詞典受限的視窗重評分：在 CTC 的機率矩陣上，用成分詞庫救回多字錯讀。

## 為什麼需要這個

PP-OCR 的辨識頭在推論時**只有 CTC，沒有語言模型**——已從原始碼確認：
`MultiHead.forward()` 在 eval 模式只回傳 `ctc_out`，那個有語言模型的
`NRTRHead` 只在訓練時當輔助監督、推論時移除。CTC 的每個時間步**條件獨立**，
所以模型讀到「抗氧化」之後，**不會因此提高「劑」的機率**——架構上沒有那個機制。

這解釋了 2026-08-18 補詞彙語料的結果：字次涵蓋率 92.7%→97.9%、
「劑」字 0→973 次，但成分完全命中只從 583 升到 602。詞彙進到了視覺編碼器
（多看幾次字形），沒有進到推理（沒地方放）。

**所以詞彙要在解碼時外掛。** 這是 CTC 缺的那一半。

## 與既有兩種做法的差別

```
編輯距離對到最近字典詞     添加物層 70.0 → 53.0    在最終字串上動刀，
                                                 不知道模型對哪個字沒把握
dict_decode.py（逐位置）  15 筆修正，指標不動      有信心度保護，但
                                                 **一次只能改一個字**
本檔（視窗重評分）         ？                      一個視窗內可同時改多字
```

`碳酸氫二鈉 → 磷酸氫二鈉` 改一個字，`dict_decode` 救得到。
但兩個字同時讀錯、要一起改才成詞的，它的搜尋空間裡根本沒有那個候選。

## 演算法

對貪婪解碼的每個視窗 `text[a:b]`（長度 2..MAXLEN），在字典裡找**等長**的詞 w，
用該位置的完整機率分佈算 `logP(w)`。因為貪婪已是逐位置最大，`logP(w) - logP(greedy)`
必為負——**接受條件是「這個損失夠小」**：

    logP(w) - logP(greedy) >= -MAX_LOSS

MAX_LOSS 是唯一的旋鈕，物理意義明確：**願意為了拼出一個真實詞彙付出多少對數機率**。
0 等於不做任何替換；很大等於退化成「編輯距離對最近詞」那個已知會崩的做法。

四道保護沿用 `dict_decode.py` 並補一道：
  1. 視窗的貪婪文字**本來就是字典詞** → 不動（不去修正本來就對的）
  2. 每個被換掉的字，新字的機率必須 >= P_MIN（不拿雜訊來換）
  3. 鹽類字尾（酸＋金屬）不動——`琥珀酸鈉` 不在庫、`琥珀酸銅` 在庫，
     實測會把讀對的「鈉」改成「銅」
  4. 一個視窗最多改 MAX_SUBS 個字
  5. **視窗不重疊**：由左到右取最佳且不衝突者，避免連鎖改動互相污染

用法：
    import lex_rescore; lex_rescore.install()
    python run_baseline.py --preset=lexrescore
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MAXLEN = 8          # 視窗最長字數。字典裡 95% 的詞在 8 字以內，再長組合數爆炸
MINLEN = 2
MAX_LOSS = 4.0      # 允許的對數機率損失（唯一旋鈕，見檔頭）
P_MIN = 0.005       # 替代字的機率下限
MAX_SUBS = 3        # 一個視窗最多改幾個字
EPS = 1e-12

SALT_SUFFIX = set("鈉鉀鈣鎂銨鐵鋅銅錳鋁磷硫氫")

_lex_by_len = None


def lexicon_by_len():
    """字典依長度分組——重評分只比對等長的詞，分組後查找是 O(1)。"""
    global _lex_by_len
    if _lex_by_len is None:
        p = os.path.join(HERE, "data", "ingredient_dict.json")
        terms = json.load(open(p, encoding="utf-8"))
        d = {}
        for t in terms:
            if MINLEN <= len(t) <= MAXLEN:
                d.setdefault(len(t), set()).add(t)
        _lex_by_len = d
    return _lex_by_len


def _all_words():
    return set().union(*lexicon_by_len().values()) if lexicon_by_len() else set()


def _is_dict_word(s):
    return s in lexicon_by_len().get(len(s), ())


def rescore(text, probs, char2idx):
    """text: 貪婪解碼的字串；probs: [(char->p 的查詢函式)] 逐位置。

    probs[i](ch) 回傳位置 i 讀成 ch 的機率。回傳 (新字串, 替換明細)。
    """
    n = len(text)
    if n < MINLEN:
        return text, []
    lex = lexicon_by_len()
    lg = lambda x: math.log(max(x, EPS))                       # noqa: E731

    # 每個視窗的最佳候選：(損失, a, b, 新詞, 改了幾個字)
    cands = []
    for a in range(n):
        for L in range(MINLEN, min(MAXLEN, n - a) + 1):
            b = a + L
            cur = text[a:b]
            if _is_dict_word(cur):
                continue                                        # 保護 1
            words = lex.get(L)
            if not words:
                continue
            base = sum(lg(probs[a + k](cur[k])) for k in range(L))
            best = None
            for w in words:
                subs = [k for k in range(L) if w[k] != cur[k]]
                if not subs or len(subs) > MAX_SUBS:
                    continue                                    # 保護 4
                if any(text[a + k] in SALT_SUFFIX for k in subs):
                    continue                                    # 保護 3
                ps = [probs[a + k](w[k]) for k in subs]
                if any(p < P_MIN for p in ps):
                    continue                                    # 保護 2
                sc = sum(lg(probs[a + k](w[k])) for k in range(L))
                loss = base - sc                                # >= 0
                if loss <= MAX_LOSS and (best is None or loss < best[0]):
                    best = (loss, w, len(subs))
            if best:
                cands.append((best[0], a, b, best[1], best[2]))

    # 保護 5：由損失小到大取，視窗不重疊
    cands.sort()
    used = [False] * n
    out = list(text)
    fixes = []
    for loss, a, b, w, ns in cands:
        if any(used[a:b]):
            continue
        for k in range(a, b):
            used[k] = True
        fixes.append((a, text[a:b], w, round(loss, 2), ns))
        out[a:b] = list(w)
    return "".join(out), fixes


# ── 掛進 PaddleOCR 的 CTC 解碼 ────────────────────────────────────────────────
_installed = False


def install(with_traditional=True, verbose=True, log=None):
    global _installed
    if _installed:
        return
    import numpy as np
    from paddlex.inference.models.text_recognition import processors as PR

    orig = PR.CTCLabelDecode.__call__
    trad_mask = None
    if with_traditional:
        import trad_decode
        trad_mask = trad_decode._mask_indices

    def patched(self, pred, *a, **k):
        try:
            p = np.array(pred)
            charset = list(self.character)
            if trad_mask is not None:
                idx = trad_mask(charset)
                if idx:
                    p[..., idx] = 0.0
            texts, scores = orig(self, p, *a, **k)
            c2i = {c: i for i, c in enumerate(charset)}
            flat = p
            while flat.ndim > 3:
                flat = flat[0]
            if flat.ndim == 2:
                flat = flat[None, ...]
            out = []
            for bi, t in enumerate(texts):
                if bi >= len(flat):
                    out.append(t)
                    continue
                # CTC 的輸出字對應「argmax 非 blank 且與前一格不同」的時間步
                idx = flat[bi].argmax(-1)
                steps, prev = [], -1
                for tt in range(len(idx)):
                    c = int(idx[tt])
                    if c != 0 and c != prev:
                        steps.append(tt)
                    prev = c
                if len(steps) != len(t):
                    out.append(t)               # 對不齊就不動，安全優先
                    continue
                row = flat[bi]
                probs = [(lambda tt: (lambda ch: float(row[tt][c2i[ch]])
                                      if ch in c2i else 0.0))(tt)
                         for tt in steps]
                nt, fx = rescore(t, probs, c2i)
                if log is not None and fx:
                    log.append((t, nt, fx))
                out.append(nt)
            return out, scores
        except Exception as e:
            print("[lex_rescore] 失敗，退回原解碼：%s" % e, file=sys.stderr)
            return orig(self, pred, *a, **k)

    PR.CTCLabelDecode.__call__ = patched
    _installed = True
    if verbose:
        print("[lex_rescore] 已安裝：字典 %d 詞、MAX_LOSS=%.1f%s"
              % (len(_all_words()), MAX_LOSS,
                 "＋純繁體遮罩" if with_traditional else ""))
