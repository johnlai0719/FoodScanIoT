#!/usr/bin/env python3
# 把 PP-OCR rec 的輸出字表限制成純繁體：解碼前把簡體字位的機率遮掉。
#
# 為什麼這樣做有效（實測證據）：
# `paddlex/.../text_recognition/processors.py:296` 的 argmax 之前，preds 是整個
# 字表上的機率分佈。攔下來看，第二候選幾乎清一色是同一個字的簡體——
#     t= 30  關=0.58  关=0.19
#     t= 56  鈉=0.64  钠=0.27
#     t= 72  劑=0.85  剂=0.10
#     t= 85  縮=0.83  缩=0.10
# 模型是真的在繁簡之間猶豫。台灣的食品標示不會出現簡體，把那些字位遮掉，
# argmax 自然落到繁體那個。
#
# **這與已否決的「OCR 後修正」是不同性質的操作。** 那個是在最終字串上用編輯
# 距離對到最近的字典詞，會把讀對的字改成另一個真的添加物（添加物層 F1
# 70.0→53.0）。這裡換掉的是**同一個字的另一種寫法**，不是另一個字，
# 所以不可能改出新的語意。
#
# 遮罩的安全條件：只遮「s2t(c) != c 且 s2t(c) 也在字表裡」的字。
# 如果某個簡體字的繁體形不在字表中，遮掉它會讓那個字整個消失，寧可留著。
#
# 用法：
#   import trad_decode; trad_decode.install()   # 要在跑推論前呼叫
#   python run_baseline_zht.py
import sys

_installed = False
_cache = {}


# 第一版直接用 `s2t` 判「是不是簡體」，實測造成破壞：
#     台→合 ×7、里→男 ×5、群→郡 ×3、热→熟 ×2、内→肉 ×2、吃→屹 ×2
# 遮掉 `台` 之後 argmax 落到第二候選 `合`，「台灣」就變成「合灣」。
# 原因是那些字**在繁體裡本來就合法**，只是同時也是某個繁體字的簡化形。
#
# 兩個判準把它們排除掉，都有依據、不必手寫黑名單：
#
# 1. **OpenCC 的 `STCharacters.txt` 是一對多的**，候選清單裡含不含自己就是答案：
#        台 → 臺 檯 颱 台      含自己 → 合法繁體，不遮
#        里 → 裏 里            含自己 → 不遮
#        干 → 幹 乾 干         含自己 → 不遮
#        纳 → 納               不含自己 → 遮
# 2. **用 `s2tw` 而不是 `s2t`**。s2t 是港式標準，會把台灣的標準字判成簡體：
#        s2t : 群→羣、吃→喫      （於是 群/吃 被誤遮）
#        s2tw: 群→群、吃→吃      （台灣標準，正確）
#
# 3. **`t2s(c) == c` 才算簡體**。s2tw 除了簡轉繁，也會把**繁體異體字**轉成
#    台灣標準字，那不是簡體：
#        s2tw(喫) = 吃   ← 喫 是繁體異體字，被誤判成簡體而遮掉，
#                          「純喫茶」變成「純噢茶」（統一的產品名，實測踩到）
#    簡體字在 t2s 下不會變（t2s(纳)=纳）；繁體字會變（t2s(喫)=吃）。
#
# 保守方向是寧可少遮：漏遮只留下一個繁簡差異，誤遮會變成錯字。


def _self_mapping_chars():
    """從 OpenCC 的 STCharacters.txt 讀出「候選清單含自己」的字。"""
    import glob
    import opencc
    import os
    out = set()
    base = os.path.dirname(opencc.__file__)
    for p in glob.glob(os.path.join(base, "**", "STCharacters.txt"),
                       recursive=True):
        for ln in open(p, encoding="utf-8"):
            parts = ln.split()
            if len(parts) >= 2 and parts[0] in parts[1:]:
                out.add(parts[0])
        break
    return out


def _mask_indices(charset):
    """回傳字表中該被遮掉的索引。用 charset 的前綴＋長度當快取鍵。"""
    key = tuple(charset[:8]) + (len(charset),)
    if key in _cache:
        return _cache[key]
    import opencc
    s2tw = opencc.OpenCC("s2tw")
    t2s = opencc.OpenCC("t2s")
    keep = _self_mapping_chars()
    cs = set(charset)
    idx = []
    for i, ch in enumerate(charset):
        if len(ch) != 1 or not ("一" <= ch <= "鿿"):
            continue                      # 只處理單一漢字，blank/英數/標點不動
        if ch in keep:
            continue                      # 本身也是合法繁體字
        if t2s.convert(ch) != ch:
            continue                      # 繁體（含異體字）——不是簡體，別碰
        t = s2tw.convert(ch)
        if t == ch or t not in cs:
            continue                      # 不是簡體，或它的繁體形字表裡沒有
        idx.append(i)
    _cache[key] = idx
    return idx


def install(verbose=True):
    global _installed
    if _installed:
        return
    import numpy as np
    from paddlex.inference.models.text_recognition import processors as PR

    orig = PR.CTCLabelDecode.__call__

    def patched(self, pred, *a, **k):
        try:
            p = np.array(pred)
            idx = _mask_indices(list(self.character))
            if idx:
                p[..., idx] = 0.0         # 機率設 0，argmax 不會選到
            pred = p
        except Exception as e:            # 出事就退回原行為，不要讓推論死掉
            print(f"[trad_decode] 遮罩失敗，退回原解碼：{e}", file=sys.stderr)
        return orig(self, pred, *a, **k)

    PR.CTCLabelDecode.__call__ = patched
    _installed = True
    if verbose:
        print("[trad_decode] 已安裝：解碼時遮蔽簡體字位")


def report(charset):
    """列出會被遮掉的字，供人工檢查。"""
    idx = _mask_indices(list(charset))
    import opencc
    s2t = opencc.OpenCC("s2t")
    print(f"字表 {len(charset)} 個字位，遮掉 {len(idx)} 個簡體字位")
    sample = [(charset[i], s2t.convert(charset[i])) for i in idx[:40]]
    print("  範例（簡→繁）：" + "、".join(f"{a}→{b}" for a, b in sample))
    return idx
