#!/usr/bin/env python3
"""用 Gemini 產**正解初稿**，填進 `_上傳區/intake.json`，人再逐案檢查修正。

**這不是評估，是標註輔助。** 產出的東西一律要人看過才算數——
`apply` 之後 `gt_source` 會標成 `llm_checked`，與手打的 `hand` 分得開，
日後要切片檢查「LLM 起草的那批是不是系統性偏差」時查得到。

⚠ **絕不覆蓋已經有內容的正解。** 預設只填 `gt` 是空的案例；
830 那批手打的 8 案會被跳過。要重跑某案得明確 `--force` 指名。

⚠ **一個必須知道的偏差**：模型會捏造。實測 Gemini 多判的 122 個添加物裡，
59 個在我方 OCR 與正解原文中都查無此物（c30 一案就佔 23 個，它補了一份
「麵包類應該有的添加物清單」）。所以**檢查時最該看的不是漏掉什麼，是多出什麼**——
漏的自己會發現，多的看起來很合理。schema 的 `description` 已經寫了
「只寫看得見的東西」，但那約束不了事實，只約束語法。

用法：
    python draft_gt.py --list                 # 只列出哪些案例會被處理
    python draft_gt.py --limit=2              # 先跑兩案看看
    python draft_gt.py                        # 全部沒填過的
    python draft_gt.py --force c99_海苔薄切洋芋片
    python draft_gt.py --model=gemini-3.6-flash
"""
import argparse
import io
import json
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import casetool                              # noqa: E402
import gemini_schema as GS                   # noqa: E402

INBOX = os.path.join(HERE, '_上傳區')
STORE = os.path.join(INBOX, 'intake.json')
DEFAULT_MODEL = 'gemini-3.6-flash'


def _key():
    """從 .env 讀金鑰。

    **不要用環境變數 `GOOGLE_API_KEY`**——這台機器上那把是 Google Vision 的，
    對 Generative Language API 會回 API_KEY_INVALID。SDK 預設會撿它，
    所以這裡明確從 .env 取 `GEMINI_API_KEY` 傳進去。
    """
    if os.environ.get('GEMINI_API_KEY'):
        return os.environ['GEMINI_API_KEY']
    for p in (os.path.join(HERE, '..', '..', '..', '.env'),
              os.path.join(HERE, '..', '..', 'server', '.env')):
        p = os.path.abspath(p)
        if not os.path.exists(p):
            continue
        for ln in io.open(p, encoding='utf-8', errors='replace'):
            m = re.match(r'\s*GEMINI_API_KEY\s*=\s*(.*)', ln)
            if m:
                return m.group(1).strip().strip('"\'')
    sys.exit('找不到 GEMINI_API_KEY（環境變數或 .env）')


def scan():
    """[(category, case_id, [jpg 絕對路徑…]), …]"""
    out = []
    for cat in sorted(casetool.VALID_CATEGORY):
        d = os.path.join(INBOX, cat)
        if not os.path.isdir(d):
            continue
        for cid in sorted(os.listdir(d)):
            cdir = os.path.join(d, cid)
            if not os.path.isdir(cdir):
                continue
            fs = [os.path.join(cdir, f) for f in sorted(os.listdir(cdir))
                  if f.lower().endswith('.jpg') and not f.startswith('._')]
            if fs:
                out.append((cat, cid, fs))
    return out


def has_gt(entry):
    """這一案是不是已經有人填過。

    判準是「有沒有實質內容」而不是「鍵存不存在」——`generate` 會替每案
    建出空殼，用鍵判斷會把所有案例都當成填過。
    """
    g = (entry or {}).get('gt') or {}
    if g.get('ingredients_list') or g.get('name') or g.get('ingredients_raw'):
        return True
    for k in ('nutrition', 'nutrition_per_serving'):
        if any(v is not None for v in (g.get(k) or {}).values()):
            return True
    return False


def to_gt(r):
    """LabelResult → intake.json 的 gt 形狀。

    只取 intake 會用到的欄位。`nutrition_other` / `nutrition_raw` /
    `serving_description` 不在 intake 的表單裡，丟掉不留——留著會讓
    `apply` 寫出正解檔沒有的鍵。
    """
    def nut(n):
        d = n.model_dump() if hasattr(n, 'model_dump') else dict(n or {})
        return {k: d.get(k) for k in ('calories', 'protein', 'fat',
                                      'saturated_fat', 'trans_fat',
                                      'carbohydrates', 'sugar', 'fiber',
                                      'sodium')}
    return {
        'name': r.name or '',
        'brand': r.brand or '',
        'manufacturer': r.manufacturer or '',
        'ingredients_raw': r.ingredients_raw or '',
        'ingredients_list': list(r.ingredients_list or []),
        'nutrition': nut(r.nutrition),
        'nutrition_per_serving': nut(r.nutrition_per_serving),
        'serving_size': r.serving_size,
        'servings_per_container': r.servings_per_container,
        'allergy_warning': r.allergy_warning or '',
        'certification_marks': list(r.certification_marks or []),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('force', nargs='*', help='指名要重跑的 case_id（會覆蓋既有正解）')
    ap.add_argument('--model', default=DEFAULT_MODEL)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--list', action='store_true', help='只列出會處理哪些案例')
    a = ap.parse_args()

    store = json.load(io.open(STORE, encoding='utf-8')) if os.path.exists(STORE) else {}
    # 動 intake.json 之前先留一份帶時戳的備份。
    # 由來：2026-09-02 修 c77（兩件商品被放進同一案）時直接 pop 掉舊草稿再重跑，
    # 那份「合併判讀」的錯誤結果就沒了——它本身是壞資料沒錯，
    # 但它同時也是「模型會把兩件商品混成一份」的證據，而且重跑失敗就無處可退。
    if store:
        import shutil, time
        bak = STORE + time.strftime('.bak-%m%d-%H%M%S')
        shutil.copy2(STORE, bak)
        print('已備份 →', os.path.basename(bak))
    cases = scan()

    todo, skip = [], []
    for cat, cid, fs in cases:
        if a.force:
            (todo if cid in a.force else skip).append((cat, cid, fs))
        elif has_gt(store.get(cid)):
            skip.append((cat, cid, fs))
        else:
            todo.append((cat, cid, fs))
    if a.limit:
        todo = todo[:a.limit]

    print('掃到 %d 案；已有正解 %d 案（跳過）；本次處理 %d 案，共 %d 張圖'
          % (len(cases), len(skip), len(todo), sum(len(f) for _, _, f in todo)))
    if a.list or not todo:
        for cat, cid, fs in todo:
            print('   %-46s %-15s %d 張' % (cid[:44], cat, len(fs)))
        return

    from google import genai
    from google.genai import types
    client = genai.Client(api_key=_key())
    cfg = types.GenerateContentConfig(
        system_instruction=GS.SYSTEM_INSTRUCTION,
        response_mime_type='application/json',
        response_schema=GS.LabelResult,
        temperature=0,
    )

    t0 = time.time()
    ok = fail = 0
    for i, (cat, cid, fs) in enumerate(todo, 1):
        parts = [types.Part.from_bytes(data=open(f, 'rb').read(),
                                       mime_type='image/jpeg') for f in fs]
        parts.append(types.Part.from_text(
            text='這是同一件商品的 %d 張照片，請合併判讀後輸出一份結果。' % len(fs)))
        try:
            resp = client.models.generate_content(
                model=a.model, contents=parts, config=cfg)
            r = resp.parsed
            if r is None:
                raise ValueError('parsed 為 None：%s' % (resp.text or '')[:200])
            e = store.setdefault(cid, {'category': cat, 'desc': '', 'difficulty': []})
            e['category'] = cat
            e['gt'] = to_gt(r)
            e['_draft'] = {'model': a.model, 'n_images': len(fs)}
            u = getattr(resp, 'usage_metadata', None)
            print('[%2d/%d] %-42s 成分 %2d 項｜營養 %d 格｜%s'
                  % (i, len(todo), cid[:40], len(r.ingredients_list or []),
                     sum(1 for v in r.nutrition.model_dump().values() if v is not None),
                     ('%d→%d tok' % (u.prompt_token_count, u.candidates_token_count))
                     if u else '—'), flush=True)
            ok += 1
        except Exception as ex:
            print('[%2d/%d] %-42s ✗ %s' % (i, len(todo), cid[:40],
                                           str(ex)[:90]), flush=True)
            fail += 1
        json.dump(store, io.open(STORE, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)   # 每案就存，中斷不會白跑

    print('\n完成 %d 案、失敗 %d 案，%.0f 秒。寫入 %s' % (ok, fail, time.time()-t0, STORE))
    print('接著：python intake.py generate   然後開網頁逐案檢查')
    print('⚠ 檢查時最該看的是**多出來的成分**——模型會捏造，而捏造的名稱看起來很合理。')


if __name__ == '__main__':
    main()
