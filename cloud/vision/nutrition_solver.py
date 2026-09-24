#!/usr/bin/env python3
# 用營養標示的法規算術約束，修正 OCR 與解析的錯誤。
#
# 這一層做的事，PPOCR 原理上做不到——它是把**領域知識**加回來：
# 食品標示應遵行事項規定要同時標「每份」與「每100公克」，兩者關係固定：
#
#     每份值 = 每100g值 × (每一份量 / 100)
#
# 實測這條關係在 ground_truth 的 430 組欄位對裡成立 422 組（98%），
# 57 案有 55 案整案吻合。剩下 8 組全來自 c24——而那是**正解自己記錯份量**
# （記 180，由數值反推應為 375），約束反過來抓出了 GT 的錯。
#
# 有了這條約束就能做三件解析器單獨做不到的事：
#   1. **修正 OCR 讀錯的數字**——c28 的 330.8 被讀成 30.8，
#      但 79.4 ÷ (24/100) = 330.8，一算就知道少了一位
#   2. **判斷哪一欄是每份、哪一欄是每100g**——不必靠版面推論
#   3. **補回讀不到的值**——有一欄加份量就能推另一欄
#
# 設計原則：**只在有證據時才改，並記錄改了什麼**。
# 推補出來的值標為 derived，讓下游（或報告）能區分「讀到的」與「算出來的」，
# 不要讓它假裝成觀測值。
import re
import unicodedata

NUTRITION_FIELDS = ['calories', 'protein', 'fat', 'saturated_fat', 'trans_fat',
                    'carbohydrates', 'sugar', 'fiber', 'sodium']
SERVING = re.compile(r'每\s*一?\s*份\s*量\D{0,3}(\d+(?:[.,]\d+)?)')


def parse_serving_size(text):
    """從「每一份量353公克」抓份量。抓不到回 None。"""
    t = unicodedata.normalize('NFKC', text or '').replace('·', '.')
    m = SERVING.search(t)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(',', '.'))
    except ValueError:
        return None
    # 份量合理範圍：小包裝 5g 到大瓶 2000ml。超出多半是把別的數字抓進來了
    return v if 5 <= v <= 2000 else None


def _consistent(per_serving, per_100, ss, tol=0.06):
    if None in (per_serving, per_100, ss):
        return False
    pred = per_100 * ss / 100
    return abs(pred - per_serving) <= max(0.6, abs(per_serving) * tol)


# OCR 在數字上最常見的錯：掉一位數、小數點消失、首位被吃掉。
# 這裡不做通用的字元級猜測，只試這幾種「有結構的」還原，
# 而且每一種都必須通過算術約束才採用——猜錯了會被擋下來。
def _repairs(v):
    """只產生「首位數字被吃掉」的候選。

    第一版還包含 ×10 / ÷10，候選一多就容易湊巧滿足約束——結果是把本來
    **正確**的那一欄改壞，整體反而掉了 10-18 格。倍率型錯誤沒有結構上的
    證據支持（小數點漂移在這批資料上並不常見），拿掉。
    首位遺漏則有明確樣態：c28 的 330.8 被讀成 30.8，原值是正解的字尾。
    """
    s = f'{v:g}'
    out = []
    for d in '123456789':
        try:
            cand = float(d + s)
        except ValueError:
            continue
        if cand != v:
            out.append(cand)
    return out


def solve(fields, serving_size, log=None):
    """fields: {欄位: (每份, 每100g)}，任一可為 None。回傳 (修正後, 說明列表)。

    只在算術約束能佐證時才動手：
      - 兩欄都有且相符 → 不動
      - 兩欄都有但不符 → 試著修其中一個（用 _repairs 的候選），修得通才採用
      - 只有一欄        → 用約束推另一欄，標為 derived
    """
    notes = [] if log is None else log
    out = {}
    ss = serving_size
    for k in NUTRITION_FIELDS:
        v = fields.get(k)
        if not v:
            continue
        a, b = (v + (None,))[:2] if len(v) < 2 else v[:2]
        if ss is None:
            out[k] = (a, b, 'read')
            continue
        if _consistent(a, b, ss):
            out[k] = (a, b, 'read')
            continue
        if a is not None and b is not None:
            # **候選必須唯一才採用。** 有兩個以上候選都能滿足約束時，
            # 代表約束不足以分辨，猜一個等於賭博——實測那樣會把正確值改壞。
            c100 = [c for c in _repairs(b) if _consistent(a, c, ss)]
            cserv = [c for c in _repairs(a) if _consistent(c, b, ss)]
            if len(c100) == 1 and not cserv:
                out[k] = (a, c100[0], 'repaired_100')
                notes.append(f'{k}: 每100g {b} → {c100[0]}（由每份 {a} 與份量 {ss} 反推）')
            elif len(cserv) == 1 and not c100:
                out[k] = (cserv[0], b, 'repaired_serving')
                notes.append(f'{k}: 每份 {a} → {cserv[0]}（由每100g {b} 與份量 {ss} 反推）')
            else:
                out[k] = (a, b, 'inconsistent')
                why = '無候選' if not (c100 or cserv) else '候選不唯一，不猜'
                notes.append(f'{k}: 每份 {a} / 每100g {b} 不符份量 {ss}（{why}），保留原值')
        elif a is not None:
            d = a * 100 / ss
            out[k] = (a, round(d, 1), 'derived_100')
            notes.append(f'{k}: 每100g 由每份 {a} 推得 {d:.1f}')
        elif b is not None:
            d = b * ss / 100
            out[k] = (round(d, 1), b, 'derived_serving')
            notes.append(f'{k}: 每份 由每100g {b} 推得 {d:.1f}')
    return out, notes


def infer_serving_size(fields):
    """反過來用：兩欄都讀到時，可以推回份量。

    份量本身讀錯或讀不到時很有用；也可以拿來驗證讀到的份量對不對
    （實測抓出 c24 的正解把 375 記成 180）。
    取各欄位推出來的中位數，避免單一欄位的 OCR 錯誤帶偏。
    """
    cand = []
    for k in NUTRITION_FIELDS:
        v = fields.get(k)
        if not v or len(v) < 2:
            continue
        a, b = v[0], v[1]
        if a is None or b is None or not b:
            continue
        r = a * 100 / b
        if 5 <= r <= 2000:
            cand.append(r)
    if len(cand) < 3:
        return None
    cand.sort()
    return round(cand[len(cand) // 2], 1)
