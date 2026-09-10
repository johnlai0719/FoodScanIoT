#!/usr/bin/env python3
# 字典約束解碼：在 rec 的逐字候選裡，只在**模型自己沒把握**的位置，
# 用成分／添加物字典挑一個能拼成真實詞彙的候選。
#
# 動機（`gap_vs_vision.py` 量出來的）：以 Vision 當標尺，PP-OCR 落後的 39 項
# 成分裡 **det 沒框到是 0 項**、rec 認錯字 19 項。所以要動的是 rec。
# 而 rec 的正確答案常常就在第二候選：
#     t=49  碳=0.92  磷=0.05  炭=0.00
# 輸出「多碳酸鈉」，而 `多磷酸鈉` 在添加物庫裡、`多碳酸鈉` 不在。
#
# **這與已否決的「OCR 後修正」是不同的操作，差別在有沒有用到模型的信心。**
# 既有結論：把讀出的字串用編輯距離對到最近的字典詞 → 添加物層 F1 70.0→53.0，
# 因為「短名稱最容易讀錯，但改一個字就會落到另一個真的添加物上」。
# 那個做法在**最終字串**上動刀，不知道模型對哪個字沒把握，會去改讀對的字。
# 這裡只在 top-1 機率低於門檻的位置動，讀到 0.99 的字根本不會被碰。
#
# 四道保護，缺一個就會變成上面那種災難：
#   1. 只在 top1 < CONF_HIGH 的位置考慮替換（模型自己猶豫過）
#   2. 替代候選的機率要 >= P_MIN（不是拿雜訊來換）
#   3. 替換後必須**剛好**拼出字典詞（完全相等，不是編輯距離）
#   4. 原本那段**不能已經**是字典詞（不去「修正」本來就對的東西）
#
# 用法：
#   import dict_decode; dict_decode.install()
#   python run_baseline_dict.py
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 0.90 太緊：實際觀察到的錯誤 `碳=0.92 磷=0.05` 會被擋掉。
# 真正把關的是保護 3、4（必須剛好拼出字典詞、原本不能已經是字典詞），
# 這個門檻只是排除模型幾乎確定的位置。
CONF_HIGH = 0.98    # top1 高於此值就當作模型有把握，不碰
P_MIN = 0.01        # 替代候選的機率下限
TOPK = 4
MAXLEN = 12         # 檢查字典詞時的最長視窗；再長的詞用不到這條路徑

_installed = False
_lex = None


def lexicon():
    """成分／添加物詞彙。只收 2 字以上——單字詞會讓任何替換都「合法」。"""
    global _lex
    if _lex is None:
        p = os.path.join(HERE, "data", "ingredient_dict.json")
        terms = json.load(open(p, encoding="utf-8"))
        _lex = {t for t in terms if 2 <= len(t) <= MAXLEN}
    return _lex


# 化學命名的鹽類／離子字尾。`酸 + 金屬` 是規律的構詞，字典收不齊所有組合——
# 實測 `琥珀酸鈉` 不在庫裡，但 `琥珀酸銅` 在，於是讀對的「鈉」被改成「銅」。
# 這正是既有結論警告的「改一個字就落到另一個真的添加物上」。
# 判準：位置 i 的字是鹽類字尾，且它前面剛好是一個字典詞（酸根）→ 本來就合法，不動。
SALT_SUFFIX = set("鈉鉀鈣鎂銨鐵鋅銅錳鋁磷硫氫")


def _is_salt_of_known_acid(text, i, lex):
    if text[i] not in SALT_SUFFIX:
        return False
    for a in range(max(0, i - MAXLEN), i):
        if i - a >= 2 and text[a:i] in lex:
            return True
    return False


def _covers(text, i, lex):
    """text 裡有沒有字典詞剛好蓋住位置 i。"""
    n = len(text)
    for a in range(max(0, i - MAXLEN + 1), i + 1):
        for b in range(i + 1, min(n, a + MAXLEN) + 1):
            if b - a >= 2 and text[a:b] in lex:
                return True
    return False


def decode_positions(p, charset, topk=TOPK):
    """CTC 逐字解碼，同時保留每個輸出字的候選。

    CTC 的輸出字來自「argmax 不是 blank 且與前一格不同」的時間步，
    所以可以把輸出位置對回時間步，拿到那一格的完整分佈。
    """
    import numpy as np
    idx = p.argmax(-1)
    out = []
    prev = -1
    for t in range(len(idx)):
        c = int(idx[t])
        if c != 0 and c != prev:
            order = np.argsort(p[t])[::-1][:topk]
            cands = [(charset[j] if j < len(charset) else "?", float(p[t][j]))
                     for j in order]
            out.append(cands)
        prev = c
    return out


def correct(cands_per_char):
    """回傳 (修正後文字, 替換明細)。"""
    lex = lexicon()
    text = "".join(c[0][0] for c in cands_per_char)
    fixes = []
    for i, cands in enumerate(cands_per_char):
        if cands[0][1] >= CONF_HIGH:
            continue                                   # 保護 1
        if _covers(text, i, lex):
            continue                                   # 保護 4
        if _is_salt_of_known_acid(text, i, lex):
            continue                                   # 保護 5
        for alt, pa in cands[1:]:
            if pa < P_MIN:
                break                                  # 保護 2
            if len(alt) != 1:
                continue
            cand = text[:i] + alt + text[i + 1:]
            if _covers(cand, i, lex):                  # 保護 3
                fixes.append((i, text[i], alt, round(cands[0][1], 3),
                              round(pa, 3)))
                text = cand
                break
    return text, fixes


def install(with_traditional=True, verbose=True, log=None):
    """log: 傳一個 list 進來會把所有替換記下來，供事後檢查。"""
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
            flat = p
            while flat.ndim > 3:
                flat = flat[0]
            if flat.ndim == 2:
                flat = flat[None, ...]
            fixed = []
            for bi, t in enumerate(texts):
                if bi >= len(flat):
                    fixed.append(t)
                    continue
                cands = decode_positions(flat[bi], charset)
                if len("".join(c[0][0] for c in cands)) != len(t):
                    fixed.append(t)          # 對不齊就不動，安全優先
                    continue
                nt, fx = correct(cands)
                if log is not None and fx:
                    log.append((t, nt, fx))
                fixed.append(nt)
            return fixed, scores
        except Exception as e:
            print(f"[dict_decode] 失敗，退回原解碼：{e}", file=sys.stderr)
            return orig(self, pred, *a, **k)

    PR.CTCLabelDecode.__call__ = patched
    _installed = True
    if verbose:
        print(f"[dict_decode] 已安裝：字典 {len(lexicon())} 詞"
              f"{'＋純繁體遮罩' if with_traditional else ''}")
