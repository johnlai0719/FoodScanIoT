#!/usr/bin/env python3
# 正解編輯頁：邊看照片邊改 ground_truth，按「儲存」直接寫回檔案。
#
# 為何要有這支：review_gt.py 能核對但不能改，改仍得手開
# ground_truth/<類別>/<case_id>.json——照片在瀏覽器、JSON 在編輯器，兩邊來回切，
# 營養兩欄十八格很容易改錯格。intake.py 雖有完整正解表單，但寫死綁 _上傳區、
# 且 apply 會擋掉已登錄的 case_id，改不了既有案例。
#
# 與本目錄其他工具的不同：label_difficulty.py 與 intake.py 走「匯出 JSON → apply」，
# 本工具起一支只聽 127.0.0.1 的小伺服器直接寫檔。理由是修既有正解通常是零星的
# （核對時發現一案不對就改一案），走匯出／套用的來回成本比改動本身還高。
# 代價是少了「全部驗證通過才動手」的批次保護，因此改為**每案寫入前單獨驗證**，
# 不過關就拒絕寫入，並且寫檔採 temp + os.replace，中途失敗不會留下半個檔。
#
# **改正解要調測試集版號。** README 的規則：次版本 = 同一批案例但照片或正解有
# 修訂，案例可對應但數字受影響。本工具不自動改 set_version（那是跨案例的決定），
# 但每次儲存後會提醒。改完記得跑 casetool.py check 並更新 README 的版本沿革。
#
# 正解欄位的權威來源是 casetool.FOOD_SKELETON，與 intake.py／score_eval.py 一致；
# 骨架變動而本表單沒跟上時拒絕啟動，避免靜默漏欄。
#
# 用法：
#   python edit_gt.py serve                        # http://127.0.0.1:8765
#   python edit_gt.py serve --port=9000
#   python edit_gt.py serve --tailscale            # 綁 Tailscale IP，tailnet 內可連（可寫！）
#   python edit_gt.py serve --category=beverage    # 只載入某類別
#   python edit_gt.py serve --source=unknown       # 只載入 gt_source=unknown 的
#   python edit_gt.py serve c24_優質蛋白奶-堅果杏仁  # 單一案例
import copy
import html
import json
import os
import posixpath
import hashlib
import subprocess
import sys
import threading
from datetime import datetime
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import casetool

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「塩」「菓」…）。
# 沒有這兩行的後果不是印出亂碼，而是**整批任務在中途拋 UnicodeEncodeError 死掉**
# ——2026-09-07 run_eval 炸在第 10 案的「塩」、intake 炸在第 11 案的「菓」，
# 後者還留下半套用狀態（照片已搬、cases.json 沒存）。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))

# 顯示用中文；鍵名一律以 FOOD_SKELETON 為準（與 intake.py 同一份對照）
NUTRI_ZH = {
    'calories': '熱量(大卡)', 'protein': '蛋白質(g)', 'fat': '脂肪(g)',
    'saturated_fat': '飽和脂肪(g)', 'trans_fat': '反式脂肪(g)',
    'carbohydrates': '碳水化合物(g)', 'sugar': '糖(g)',
    'fiber': '膳食纖維(g)', 'sodium': '鈉(mg)',
}
# 2026-09-15：表單拿掉 brand、加入產地。
# - brand 只是不再顯示；validate 以既有檔為底，所以檔裡原本的 brand 值原樣保留，
#   score_eval 仍照舊計分。
# - 產地的鍵名 country_of_origin 與 server/models.py 的 products 欄位一致。
#   **尚未進 FOOD_SKELETON**（改骨架會讓 intake.py 的同步檢查拒絕啟動），
#   也還沒有評分——目前只是開始轉錄。
TEXT_FIELDS = [('name', '品名'), ('manufacturer', '製造商'), ('country_of_origin', '產地')]
EXTRA_TEXT_FIELDS = ('country_of_origin',)    # 不在 FOOD_SKELETON 裡的文字欄

# 外觀特徵（教授 08-29 建議 13：分層要用包裝的物理特性，不是商品類別）。
# 存在 cases.json、不在正解檔，與 gt_source 同一種寫法。字彙以 casetool 為準，
# 缺中文名就直接顯示鍵。⚠ casetool 的註解：這兩欄是選品描述，**不拿來分組報數字**。
PKG_SHAPE_ZH = {'carton': '平面紙盒', 'can': '圓柱罐', 'pouch': '軟袋',
                'bottle': '曲面瓶', 'shrink_wrap': '收縮膜',
                'tray': '塑膠盒', 'cup': '杯裝'}
REFLECT_ZH = {'foil': '鋁箔鍍膜', 'glossy_plastic': '亮面塑膠', 'matte_paper': '霧面紙',
              'clear_film': '透明膜', 'metal': '金屬', 'glass': '玻璃'}
CATEGORY_ZH = {'beverage': '飲料', 'snack': '零食', 'instant_noodle': '泡麵',
               'sauce': '醬料', 'biscuit': '餅乾', 'prepared_meal': '調理食品',
               'non_food': '非食品'}
CASE_FIELDS = {   # 欄位 -> (合法值, 中文名對照, 標籤)
    'pkg_shape': (casetool.VALID_PKG_SHAPE, PKG_SHAPE_ZH, '包裝形狀'),
    'reflect': (casetool.VALID_REFLECT, REFLECT_ZH, '反光材質'),
}
# cases.json 是整份讀改寫；ThreadingHTTPServer 下兩人同時存不同案也會互相覆蓋
_CASES_LOCK = threading.Lock()


def _log(msg):
    """終端機輸出絕不可讓請求失敗。

    Windows console 預設 cp950，遇到它編不出的字（實測「台灣森永製菓」的「菓」
    U+83D3）會丟 UnicodeEncodeError。原本這行 print 在 write_gt 之後執行，於是
    「檔案已寫好、但回應變成 500 未寫入」——最糟的組合：使用者以為沒存，
    重打一次。startup 時已把 stdout 轉成 utf-8，這裡再兜一層底。
    """
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode('utf-8', 'replace').decode('ascii', 'replace'))
        except Exception:
            pass


def _check_skeleton_sync():
    """FOOD_SKELETON 變動而表單沒跟上時，寧可擋下也不要靜默少收欄位。"""
    expected = {'is_food_label', 'name', 'brand', 'manufacturer',
                'ingredients_raw', 'ingredients_list',
                'nutrition', 'nutrition_per_serving',
                'serving_size', 'servings_per_container',
                'allergy_warning', 'certification_marks'}
    if (set(casetool.FOOD_SKELETON) != expected
            or set(casetool.FOOD_SKELETON['nutrition']) != set(NUTRI_ZH)
            or set(casetool.FOOD_SKELETON['nutrition_per_serving']) != set(NUTRI_ZH)):
        sys.exit("casetool.FOOD_SKELETON 已變動，請同步更新 edit_gt.py 的表單後再執行。")


# ─── 驗證與寫入 ──────────────────────────────────────────────────────────────
def _num_or_none(v, where, errors):
    """空字串一律回 None，**不轉成 0**。

    「標示上沒有」與「含量為零」是兩件事（見 FOOD_SKELETON 的註解）。
    表單留白代表前者，使用者要表示後者必須明確打 0。
    """
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v == '':
            return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        errors.append(f"{where}：「{v}」不是數字（沒有標示請留空，不要填 0）")
        return None
    return int(f) if f == int(f) else f


def validate(payload, existing=None):
    """回傳 (正解 dict, 錯誤列表)。錯誤非空時呼叫端不得寫入。

    以磁碟上的既有正解為底、只覆寫本表單管的欄位，**不從骨架重建**。
    理由：實測 46/58 個正解檔含 `reject_reason`，該欄不在 FOOD_SKELETON 裡也沒有
    任何程式在讀，但若從骨架重建，每次儲存都會把它靜默刪掉。工具不該移除自己
    不認識的欄位——那是資料損失，而且要等到很久以後才會有人發現。
    """
    errors = []
    gt = copy.deepcopy(existing) if existing else {}
    for k, v in casetool.FOOD_SKELETON.items():
        gt.setdefault(k, copy.deepcopy(v))

    if not payload.get('is_food_label', True):
        gt['is_food_label'] = False
        return gt, errors
    gt['is_food_label'] = True

    for key, zh in TEXT_FIELDS:
        gt[key] = (payload.get(key) or '').strip()
    # 骨架外的欄位：留白且原檔本來就沒有這個鍵時不寫入。否則每存一案都會
    # 多出一行 "country_of_origin": ""，diff 上看起來像改了東西。
    for key in EXTRA_TEXT_FIELDS:
        if not gt.get(key) and key not in (existing or {}):
            gt.pop(key, None)
    gt['ingredients_raw'] = (payload.get('ingredients_raw') or '').strip()
    gt['allergy_warning'] = (payload.get('allergy_warning') or '').strip()

    # 清單欄位在表單上是一行一項；空行忽略，不要產生空字串項目
    for key in ('ingredients_list', 'certification_marks'):
        raw = payload.get(key)
        items = raw if isinstance(raw, list) else str(raw or '').splitlines()
        gt[key] = [s.strip() for s in items if s and s.strip()]

    for scope in ('nutrition', 'nutrition_per_serving'):
        block = payload.get(scope) or {}
        for k in casetool.FOOD_SKELETON[scope]:
            gt[scope][k] = _num_or_none(block.get(k), f"{scope}.{NUTRI_ZH[k]}", errors)
    gt['serving_size'] = _num_or_none(payload.get('serving_size'), 'serving_size', errors)
    gt['servings_per_container'] = _num_or_none(
        payload.get('servings_per_container'), 'servings_per_container', errors)

    if not gt['name']:
        errors.append("品名空白（確定照片上真的沒有產品名？非食品請改用 non_food 類別）")
    return gt, errors


def diff_summary(old, new):
    """列出改了哪些欄位，給使用者在按下儲存後確認自己真的改了預期的東西。"""
    out = []

    def walk(a, b, prefix=''):
        for k in sorted(set(a) | set(b)):
            va, vb = a.get(k), b.get(k)
            if isinstance(va, dict) or isinstance(vb, dict):
                walk(va or {}, vb or {}, f"{prefix}{k}.")
            elif va != vb:
                zh = NUTRI_ZH.get(k) or dict(TEXT_FIELDS).get(k, k)
                out.append(f"{prefix}{zh}: {_short(va)} → {_short(vb)}")
    walk(old or {}, new or {})
    return out


def _short(v):
    if isinstance(v, list):
        return f"[{len(v)} 項]" if len(v) > 3 else json.dumps(v, ensure_ascii=False)
    s = '(空)' if v in (None, '') else str(v)
    return s if len(s) <= 40 else s[:40] + '…'


def write_gt(case_id, category, gt):
    """temp + os.replace，中途失敗不會留下半個檔。

    `newline='\\n'` 不可省：Windows 上 open(..., 'w') 預設把 \\n 轉成 \\r\\n，
    而既有的正解檔是 LF。少了它，每存一次就整檔變成 CRLF，git 會顯示
    「整個檔案都改了」，真正的一行修訂被埋在雜訊裡。
    """
    path = casetool.gt_path(case_id, category)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(gt, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def set_gt_source(case_id, source):
    """gt_source 存在 cases.json 而非正解檔，所以單獨寫。

    人工改過的正解理應標成 hand——這個欄位是「只看手工轉錄案例的分數」那組
    對照數字的依據（見 casetool.VALID_GT_SOURCE）。
    """
    if source not in casetool.VALID_GT_SOURCE:
        return f"未知的 gt_source：{source}"
    with _CASES_LOCK:
        data = casetool.load_cases()
        for c in data['cases']:
            if c['case_id'] == case_id:
                if c.get('gt_source') == source:
                    return None
                c['gt_source'] = source
                casetool.save_cases(data)
                return None
    return f"cases.json 找不到 {case_id}"


def set_case_field(case_id, field, value):
    """寫 cases.json 的外觀欄位。空值＝移除該鍵（回到「未填」），不存空字串。"""
    vocab = CASE_FIELDS[field][0]
    if value and value not in vocab:
        return f"未知的 {field}：{value}"
    with _CASES_LOCK:
        data = casetool.load_cases()
        for c in data['cases']:
            if c['case_id'] == case_id:
                if (c.get(field) or '') == (value or ''):
                    return None
                if value:
                    c[field] = value
                else:
                    c.pop(field, None)
                casetool.save_cases(data)
                return None
    return f"cases.json 找不到 {case_id}"


# ─── 頁面 ────────────────────────────────────────────────────────────────────
# ─── 確認紀錄 ────────────────────────────────────────────────────────────────
# 同學核對完一案就打勾。紀錄放獨立檔案，不寫進正解檔也不寫進 cases.json：
# 「誰看過」是工作進度，不是資料本身，混進去會讓每次打勾都變成測試集的修訂。
#
# 每筆記下確認當時的內容雜湊。之後有人改了正解（或外觀欄位），雜湊對不上，
# 該案就顯示「確認後有修改」——否則打勾代表的是舊內容，看起來卻像確認過新內容。
REVIEW_PATH = os.path.join(HERE, 'review_status.json')
_REVIEW_LOCK = threading.Lock()


def content_hash(case, gt):
    blob = json.dumps({'gt': gt, **{k: case.get(k) or '' for k in CASE_FIELDS}},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]


def load_reviews():
    try:
        return json.load(open(REVIEW_PATH, encoding='utf-8'))
    except FileNotFoundError:
        return {}


def save_reviews(d):
    tmp = REVIEW_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(d, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, REVIEW_PATH)


def review_state(case, gt, reviews):
    r = reviews.get(case['case_id'])
    if not r:
        return None
    return dict(r, stale=(r.get('hash') != content_hash(case, gt)))


_WHO = {}


def tailnet_device(ip):
    """連線者的 Tailscale 裝置名，輔助名字欄（名字是自己打的，裝置名不是）。查不到回空字串。"""
    if ip in _WHO:
        return _WHO[ip]
    name = ''
    try:
        out = subprocess.run(['tailscale', 'whois', '--json', ip],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            name = json.loads(out.stdout)['Node']['Name'].split('.')[0]
    except Exception:
        pass
    _WHO[ip] = name
    return name


def esc(v):
    return html.escape('' if v is None else str(v))


def render_case(case, gt, rev=None):
    cid, cat = case['case_id'], case['category']
    is_food = gt.get('is_food_label', True)
    # 縮圖點了送到右欄大圖檢視器；src 是伺服器現產的小圖，data-src 才是原檔
    # （理由同 intake._build_thumbs：原檔 4032×3024，177 案全用原檔當縮圖太重）
    imgs = ''.join(
        f'<figure class="im" data-src="/img/{esc(p)}" data-f="{esc(p.rsplit("/", 1)[-1])}" '
        f'onclick="pick(this)"><img src="/thumb/{esc(p)}" loading="lazy">'
        f'<figcaption>{esc(p.rsplit("/", 1)[-1])}</figcaption></figure>'
        for p in case.get('images', []))

    if not is_food:
        body = ('<p class="nonfood">此案為 <code>is_food_label: false</code>（非食品）。'
                '本頁不編輯非食品案例——若判定有誤，請改動案例類別後重跑。</p>')
    else:
        rows = ''.join(
            f'<label>{esc(zh)}<input data-f="{k}" value="{esc(gt.get(k))}"></label>'
            for k, zh in TEXT_FIELDS)
        nutri = ''.join(
            f'<tr><th>{esc(zh)}</th>'
            f'<td><input data-n="nutrition.{k}" value="{esc(gt.get("nutrition", {}).get(k))}"></td>'
            f'<td><input data-n="nutrition_per_serving.{k}" '
            f'value="{esc(gt.get("nutrition_per_serving", {}).get(k))}"></td></tr>'
            for k, zh in NUTRI_ZH.items())
        body = f"""
        <div class="grid">{rows}
          <label>每份容量 serving_size
            <input data-f="serving_size" value="{esc(gt.get('serving_size'))}"></label>
          <label>本包裝含幾份 servings_per_container
            <input data-f="servings_per_container"
                   value="{esc(gt.get('servings_per_container'))}"></label>
        </div>
        <label class="full">成分原文 ingredients_raw
          <textarea data-f="ingredients_raw" rows="4">{esc(gt.get('ingredients_raw'))}</textarea></label>
        <label class="full">成分逐項 ingredients_list<span class="hint">一行一項</span>
          <textarea data-f="ingredients_list" rows="8">{esc(chr(10).join(gt.get('ingredients_list') or []))}</textarea></label>
        <label class="full">過敏原警語 allergy_warning
          <textarea data-f="allergy_warning" rows="2">{esc(gt.get('allergy_warning'))}</textarea></label>
        <label class="full">認證標章 certification_marks<span class="hint">一行一項</span>
          <textarea data-f="certification_marks" rows="2">{esc(chr(10).join(gt.get('certification_marks') or []))}</textarea></label>
        <table class="nutri"><tr><th></th><th>每 100g/mL<br><small>nutrition</small></th>
          <th>每份<br><small>nutrition_per_serving</small></th></tr>{nutri}</table>
        <p class="warn">留空 = 標示上沒有；含量真的是零才填 0。兩者在評分上不同。</p>
        """

    def csel(field):
        vocab, zh, label = CASE_FIELDS[field]
        cur = case.get(field) or ''
        o = '<option value="">（未填）</option>' + ''.join(
            f'<option value="{k}"{" selected" if k == cur else ""}>{esc(zh.get(k, k))}</option>'
            for k in sorted(vocab))
        return f'<label>{label}<select data-c="{field}">{o}</select></label>'
    feat = csel('pkg_shape') + csel('reflect')

    src = (case.get('gt_source') or 'unknown')
    opts = ''.join(f'<option value="{s}"{" selected" if s == src else ""}>{s}</option>'
                   for s in sorted(casetool.VALID_GT_SOURCE))
    return f"""
    <section class="case" id="{esc(cid)}" data-cid="{esc(cid)}" data-cat="{esc(cat)}"
             data-pkg="{esc(case.get('pkg_shape') or '')}"
             data-ref="{esc(case.get('reflect') or '')}"
             data-revjson="{esc(json.dumps(rev, ensure_ascii=False)) if rev else ''}"
             data-food="{'1' if is_food else '0'}">
      <h2>{esc(cid)} <small>{esc(CATEGORY_ZH.get(cat, cat))}</small></h2>
      <div class="feat">{feat}</div>
      <div class="imgs">{imgs}</div>
      <div class="form">{body}
        <div class="bar">
          <label class="src">gt_source <select data-f="gt_source">{opts}</select></label>
          <button onclick="save(this)">儲存</button>
          <label class="chk"><input type="checkbox" data-rev onchange="confirmCase(this)">
            已確認正確</label>
          <span class="msg"></span>
        </div>
        <div class="revinfo"></div>
      </div>
    </section>"""


PAGE_CSS = """
body{font-family:system-ui,sans-serif;margin:0;padding:0}
#nav{position:sticky;top:0;background:#f6f6f6;border-bottom:2px solid #bbb;padding:8px 16px;
     z-index:5;display:flex;align-items:center;gap:8px}
#nav select{width:auto;min-width:24em;max-width:60vw;font-size:1rem;padding:5px 8px}
#nav button{padding:5px 12px;background:#eee;color:#222;border:1px solid #bbb}
#dirtyinfo{color:#b40;font-weight:700;font-size:.85em}
#nav{flex-wrap:wrap}
#nav .flt{width:auto;font-size:.9rem;padding:4px 6px}
#fcount{color:#555;font-size:.85em}
.case.hid{display:none}
.feat{display:flex;gap:12px;margin:2px 0 4px}
.feat label{flex:0 0 190px;margin:0}
label.chk{display:flex;align-items:center;gap:5px;margin:0;font-size:.95rem;color:#222;
          white-space:nowrap;cursor:pointer}
label.chk input{width:auto;transform:scale(1.3)}
.revinfo{font-size:.8em;padding:2px 0 4px}
.revinfo.ok{color:#080}.revinfo.stale{color:#c60;font-weight:600}
.case[data-rev=ok] h2::after{content:' ✓ 已確認';color:#2a7;font-size:.6em;font-weight:600}
.case[data-rev=stale] h2::after{content:' ⚠ 確認後有修改';color:#c60;font-size:.6em;font-weight:600}
#who{width:8em;font-size:.9rem;padding:4px 6px}
#revcount{color:#080;font-weight:600;font-size:.85em}
#wrap{display:flex;align-items:flex-start;gap:16px;padding:0 16px 80px}
#left{flex:1;min-width:0}
#right{position:sticky;top:64px;width:50vw;height:calc(100vh - 80px);margin-top:12px;
       display:flex;flex-direction:column;border:1px solid #bbb;border-radius:10px;background:#fafafa}
#stage{flex:1;overflow:hidden;position:relative;background:#222;border-radius:9px 9px 0 0;
       cursor:grab;touch-action:none}
#stage.drag{cursor:grabbing}
#big{position:absolute;left:50%;top:50%;max-width:100%;max-height:100%;
     transform-origin:center center;will-change:transform}
#vbar{padding:6px 10px;border-top:1px solid #ddd;font-size:.85em;
      display:flex;align-items:center;gap:6px;flex-wrap:wrap}
#vbar button{font-size:.85em;padding:4px 9px;background:#eee;color:#222;border:1px solid #bbb}
#vname{color:#666;margin-left:auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:40%}
#vhint{color:#888;padding:40% 20px;text-align:center}
.case{border-top:3px solid #ddd;padding-top:10px;margin-top:28px;
      content-visibility:auto;contain-intrinsic-size:auto 1200px}
h2 small{color:#888;font-weight:400;margin-left:8px}
.imgs{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 10px}
.im{width:120px;margin:0;cursor:pointer}
.im img{width:120px;height:120px;object-fit:contain;background:#eee;border:2px solid #ddd;
        border-radius:6px;box-sizing:border-box;display:block}
.im.sel img{border-color:#06c}
.im figcaption{font-size:.65em;color:#888;text-align:center;overflow:hidden;
               text-overflow:ellipsis;white-space:nowrap}
.form{min-width:0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px 12px}
label{display:block;font-size:.8em;color:#555;margin-bottom:8px}
label.full{display:block}
.hint{color:#999;margin-left:6px}
input,textarea,select{width:100%;font:inherit;font-size:.95rem;padding:4px 6px;
     border:1px solid #bbb;border-radius:3px;box-sizing:border-box}
textarea{resize:vertical;font-family:ui-monospace,monospace;font-size:.85rem}
table.nutri{border-collapse:collapse;width:100%;margin-top:10px}
table.nutri th,table.nutri td{border:1px solid #ddd;padding:2px 6px;font-size:.8em}
table.nutri th{background:#fafafa;text-align:left;font-weight:600}
.bar{margin-top:14px;display:flex;align-items:center;gap:12px;
     position:sticky;bottom:0;background:#fff;padding:8px 0;border-top:1px solid #eee}
.bar .src{flex:0 0 200px;margin:0}
button{font:inherit;padding:6px 18px;cursor:pointer;background:#06c;color:#fff;
       border:0;border-radius:4px}
button:disabled{background:#999;cursor:default}
.msg{font-size:.85em;white-space:pre-line}
.msg.ok{color:#080}.msg.err{color:#c00}
.warn{font-size:.75em;color:#a60;background:#fffbe6;padding:6px 8px;border-radius:3px}
.nonfood{color:#666;background:#f4f4f4;padding:10px;border-radius:4px}
"""

PAGE_JS = """
function collect(sec){
  const p={is_food_label: sec.dataset.food==='1'};
  sec.querySelectorAll('[data-f]').forEach(el=>{
    if(el.dataset.f==='gt_source') return;
    p[el.dataset.f] = (el.dataset.f==='ingredients_list'||el.dataset.f==='certification_marks')
      ? el.value.split('\\n') : el.value;
  });
  p.nutrition={}; p.nutrition_per_serving={};
  sec.querySelectorAll('[data-n]').forEach(el=>{
    const [s,k]=el.dataset.n.split('.'); p[s][k]=el.value;
  });
  const sel=sec.querySelector('[data-f="gt_source"]');
  const cf={};
  sec.querySelectorAll('[data-c]').forEach(el=>{ cf[el.dataset.c]=el.value; });
  return {gt:p, gt_source: sel?sel.value:null, case_fields:cf};
}
function saved(sec){
  setDirty(sec.dataset.cid,false);
  // 篩選依 data-pkg／data-ref；存檔後同步，但不立刻重篩（免得剛改完的案例當場消失）
  sec.querySelectorAll('[data-c]').forEach(el=>{
    sec.dataset[el.dataset.c==='pkg_shape'?'pkg':'ref']=el.value; });
}
// ── 篩選：類別／包裝形狀／反光材質 ─────────────────────────────────────────
function applyFilter(){
  const f={cat:document.getElementById('fcat').value,
           pkg:document.getElementById('fpkg').value,
           ref:document.getElementById('fref').value,
           rev:document.getElementById('frev').value};
  const s=document.getElementById('jump'), cur=s.value;
  s.innerHTML=''; let n=0;
  document.querySelectorAll('.case').forEach(sec=>{
    const ok=(f.cat==='*'||sec.dataset.cat===f.cat)
          &&(f.pkg==='*'||sec.dataset.pkg===f.pkg)
          &&(f.ref==='*'||sec.dataset.ref===f.ref)
          &&(f.rev==='*'||(f.rev==='todo'?sec.dataset.rev!=='ok':sec.dataset.rev===f.rev));
    sec.classList.toggle('hid',!ok);
    if(ok){ s.add(new Option((DIRTY.has(sec.dataset.cid)?'● ':'')+sec.dataset.cid,
                             sec.dataset.cid)); n++; }
  });
  document.getElementById('fcount').textContent=
    n+' / '+document.querySelectorAll('.case').length+' 案';
  if(n){ const keep=[...s.options].some(o=>o.value===cur); go(keep?cur:s.options[0].value); }
}
// 未儲存的案例：選單項目前面加 ●，旁邊顯示件數
const DIRTY=new Set();
function optOf(cid){
  return [...document.getElementById('jump').options].find(o=>o.value===cid);
}
function setDirty(cid,on){
  if(on) DIRTY.add(cid); else DIRTY.delete(cid);
  const o=optOf(cid); if(o) o.textContent=(on?'● ':'')+cid;
  document.getElementById('dirtyinfo').textContent=DIRTY.size?DIRTY.size+' 案未儲存':'';
}
function mark(sec){ setDirty(sec.dataset.cid,true); }
// 長距離跳轉時，途經的案例因 content-visibility 從估計高度換成實際高度，
// 版面會位移——第一次捲動落點會偏，捲動同步也會把選單改回鄰案。
// 所以跳轉期間暫停同步，並在版面穩定後再對齊幾次。
let jumping=0;
function go(cid){
  const sec=document.getElementById(cid); if(!sec) return;
  const s=document.getElementById('jump'); s.value=cid;
  clearTimeout(jumping);
  const align=()=>{ window.scrollBy(0,sec.getBoundingClientRect().top-60); };  // 60＝選單列高
  align();
  requestAnimationFrame(()=>requestAnimationFrame(align));
  setTimeout(align,250);
  jumping=setTimeout(()=>{jumping=0;},500);
  const f=sec.querySelector('.im'); if(f) pick(f);
}
function step(d){
  const s=document.getElementById('jump');
  const i=Math.min(s.options.length-1,Math.max(0,s.selectedIndex+d));
  s.selectedIndex=i; go(s.value);
}
// 捲動時選單跟著顯示目前看到的案例
const secObs=new IntersectionObserver(es=>{
  if(jumping) return;
  es.forEach(e=>{ if(e.isIntersecting){ const s=document.getElementById('jump');
    if(s.value!==e.target.dataset.cid) s.value=e.target.dataset.cid; } });
},{rootMargin:'-80px 0px -70% 0px'});
document.querySelectorAll('.case').forEach(s=>secObs.observe(s));
async function save(btn){
  const sec=btn.closest('.case'), msg=sec.querySelector('.msg');
  btn.disabled=true; msg.className='msg'; msg.textContent='儲存中…';
  try{
    const r=await fetch('/save/'+encodeURIComponent(sec.dataset.cid),
      {method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify(collect(sec))});
    const j=await r.json();
    if(j.ok){
      msg.className='msg ok';
      msg.textContent = j.changed.length
        ? '已寫入：\\n'+j.changed.join('\\n')+'\\n\\n記得調 set_version 並跑 casetool.py check'
        : '沒有變動，未寫檔。';
      saved(sec); showRev(sec, j.review);
    }else{
      msg.className='msg err'; msg.textContent='未寫入：\\n'+j.errors.join('\\n');
    }
  }catch(e){
    // 網路層失敗（伺服器忙、連線被砍）重試一次再放棄。重點是明確告訴使用者
    // 表單內容還在，不要以為改的東西不見了而重打一遍。
    try{
      await new Promise(r=>setTimeout(r,600));
      const r2=await fetch('/save/'+encodeURIComponent(sec.dataset.cid),
        {method:'POST',headers:{'Content-Type':'application/json'},
         body:JSON.stringify(collect(sec))});
      const j2=await r2.json();
      if(j2.ok){
        msg.className='msg ok';
        msg.textContent=(j2.changed.length?'已寫入（重試後成功）：\\n'+j2.changed.join('\\n')
                                          :'沒有變動，未寫檔。');
        saved(sec); showRev(sec, j2.review);
      }else{
        msg.className='msg err'; msg.textContent='未寫入：\\n'+j2.errors.join('\\n');
      }
    }catch(e2){
      msg.className='msg err';
      msg.textContent='連線失敗（已重試）：'+e2+
        '\\n你改的內容還在表單裡，沒有遺失。確認終端機的伺服器還在跑，再按一次儲存。';
    }
  }
  btn.disabled=false;
}
document.addEventListener('input',e=>{
  if(e.target.hasAttribute('data-rev')) return;     // 打勾不算未儲存的修改
  const sec=e.target.closest('.case'); if(sec) mark(sec);
});

// ── 確認打勾 ────────────────────────────────────────────────────────────────
async function confirmCase(cb){
  const sec=cb.closest('.case');
  if(cb.checked&&DIRTY.has(sec.dataset.cid)){
    cb.checked=false; alert('這案有還沒儲存的修改，先按「儲存」再打勾。'); return;
  }
  const by=(document.getElementById('who').value||'').trim();
  if(!by){
    cb.checked=!cb.checked; alert('先在最上面填你的名字。');
    document.getElementById('who').focus(); return;
  }
  cb.disabled=true;
  try{
    const r=await fetch('/confirm/'+encodeURIComponent(sec.dataset.cid),
      {method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({by:by,confirmed:cb.checked})});
    const j=await r.json();
    if(j.ok) showRev(sec,j.review);
    else { cb.checked=!cb.checked; alert('未記錄：'+j.errors.join('、')); }
  }catch(e){ cb.checked=!cb.checked; alert('連線失敗，未記錄：'+e); }
  cb.disabled=false;
}
function showRev(sec,r){
  const cb=sec.querySelector('[data-rev]'), info=sec.querySelector('.revinfo');
  const st=!r?'':(r.stale?'stale':'ok');
  sec.dataset.rev=st;
  if(cb) cb.checked=(st==='ok');
  if(info){
    info.className='revinfo '+st;
    info.textContent=!r?'':(r.stale
      ? '⚠ '+r.by+' 於 '+r.at+' 確認過，之後內容有修改，需要重新確認'
      : '✓ '+r.by+(r.device?'（'+r.device+'）':'')+' 於 '+r.at+' 確認');
  }
  updRevCount();
}
function updRevCount(){
  const all=document.querySelectorAll('.case'); let n=0;
  all.forEach(s=>{ if(s.dataset.rev==='ok') n++; });
  document.getElementById('revcount').textContent='已確認 '+n+' / '+all.length;
}
(function(){
  const who=document.getElementById('who');
  try{ who.value=localStorage.getItem('gt_reviewer')||''; }catch(e){}
  who.addEventListener('change',()=>{ try{ localStorage.setItem('gt_reviewer',who.value.trim()); }catch(e){} });
  document.querySelectorAll('.case').forEach(sec=>{
    showRev(sec, sec.dataset.revjson?JSON.parse(sec.dataset.revjson):null); });
})();
window.addEventListener('beforeunload',e=>{
  if(DIRTY.size){ e.preventDefault(); e.returnValue=''; }
});

// ── 右欄大圖檢視（沿用 intake.py 工作單的做法）─────────────────────────────
// 這裡的旋轉**只是給人看**，不會寫回照片檔——既有案例的照片改動屬於測試集
// 修訂，要走 intake／版號流程，不該在正解編輯頁順手做掉。
const V={fig:null,z:1,tx:0,ty:0};
const $=id=>document.getElementById(id);
function degOf(f){return ((+f.dataset.rot||0)%360+360)%360;}
function pick(fig){
  document.querySelectorAll('.im.sel').forEach(f=>f.classList.remove('sel'));
  fig.classList.add('sel'); V.fig=fig;
  const sec=fig.closest('.case');
  $('vname').textContent=(sec?sec.dataset.cid+' / ':'')+fig.dataset.f;
  $('vhint').style.display='none';
  let img=$('big');
  if(!img){ img=document.createElement('img'); img.id='big';
    img.draggable=false; $('stage').appendChild(img); img.addEventListener('load',fit); }
  if(img.getAttribute('src')!==fig.dataset.src) img.src=fig.dataset.src; else fit();
}
function render(){
  const img=$('big'); if(!img||!V.fig) return;
  const r=degOf(V.fig);
  $('vdeg').textContent=r?r+'°':'';
  img.style.transform='translate(-50%,-50%) translate('+V.tx+'px,'+V.ty+'px) scale('+V.z+') rotate('+r+'deg)';
  $('vzoom').textContent=Math.round(V.z*100)+'%';
}
function fit(){
  const img=$('big'),st=$('stage'); if(!img||!V.fig) return;
  V.tx=V.ty=0;
  const w=img.clientWidth,h=img.clientHeight;
  V.z=(degOf(V.fig)%180&&w&&h)?Math.min(1,st.clientWidth/h,st.clientHeight/w):1;
  render();
}
function rot(d){
  if(!V.fig){alert('先點一張縮圖。');return;}
  V.fig.dataset.rot=(degOf(V.fig)+d+360)%360;
  const r=degOf(V.fig);
  V.fig.querySelector('img').style.transform=r?'rotate('+r+'deg)':'';
  fit();
}
function zoom(k){ if(!V.fig) return; V.z=Math.min(12,Math.max(0.1,V.z*k)); render(); }
$('stage').addEventListener('wheel',ev=>{
  if(!V.fig) return; ev.preventDefault(); zoom(ev.deltaY<0?1.15:1/1.15);
},{passive:false});
// pointer events：滑鼠與觸控（平板、手機走 Tailscale 連進來）共用一套
let drag=null;
$('stage').addEventListener('pointerdown',ev=>{
  if(!V.fig) return;
  drag={x:ev.clientX-V.tx,y:ev.clientY-V.ty};
  $('stage').classList.add('drag'); $('stage').setPointerCapture(ev.pointerId);
  ev.preventDefault();
});
$('stage').addEventListener('pointermove',ev=>{
  if(!drag) return; V.tx=ev.clientX-drag.x; V.ty=ev.clientY-drag.y; render();
});
$('stage').addEventListener('pointerup',()=>{drag=null;$('stage').classList.remove('drag');});
$('stage').addEventListener('dblclick',fit);
// 在某案的表單裡打字時，右欄自動切到那一案的第一張圖（除非已經在看該案的圖）
document.addEventListener('focusin',e=>{
  const sec=e.target.closest('.case'); if(!sec) return;
  if(V.fig&&V.fig.closest('.case')===sec) return;
  const f=sec.querySelector('.im'); if(f) pick(f);
});
let rt=null;
window.addEventListener('resize',()=>{clearTimeout(rt);rt=setTimeout(fit,150);});
"""


def _filter(fid, label, get, zh, pairs):
    """篩選下拉：每個值附件數，「（未填）」獨立一項——pkg_shape／reflect 還有七十多案沒填。"""
    from collections import Counter
    cnt = Counter(get(c) for c, _ in pairs)
    opts = f'<option value="*">{label}：全部</option>' + ''.join(
        f'<option value="{esc(k)}">{esc(zh.get(k, k) if k else "（未填）")}（{n}）</option>'
        for k, n in sorted(cnt.items(), key=lambda kv: (kv[0] == '', -kv[1])))
    return f'<select id="{fid}" class="flt" onchange="applyFilter()">{opts}</select>'


def build_page(pairs):
    # 177 案攤成一串連結會佔掉好幾行還得捲，改成下拉選單＋上一案／下一案
    opts = ''.join(f'<option value="{esc(c["case_id"])}">{esc(c["case_id"])}</option>'
                   for c, _ in pairs)
    nav = (f'<button type="button" onclick="step(-1)">◀ 上一案</button>'
           f'<select id="jump" onchange="go(this.value)">{opts}</select>'
           f'<button type="button" onclick="step(1)">下一案 ▶</button>'
           f'<span id="dirtyinfo"></span>'
           + _filter('fcat', '類別', lambda c: c['category'], CATEGORY_ZH, pairs)
           + _filter('fpkg', '包裝形狀', lambda c: c.get('pkg_shape') or '', PKG_SHAPE_ZH, pairs)
           + _filter('fref', '反光材質', lambda c: c.get('reflect') or '', REFLECT_ZH, pairs)
           + '<select id="frev" class="flt" onchange="applyFilter()">'
             '<option value="*">確認狀態：全部</option>'
             '<option value="todo">待確認（含確認後有修改）</option>'
             '<option value="ok">已確認</option>'
             '<option value="stale">確認後有修改</option></select>'
           + '<span id="fcount"></span>'
           + '<input id="who" placeholder="你的名字（打勾用）">'
           + '<span id="revcount"></span>')
    reviews = load_reviews()
    cases = ''.join(render_case(c, g, review_state(c, g, reviews)) for c, g in pairs)
    return (f'<!doctype html><meta charset="utf-8"><title>正解編輯 — {len(pairs)} 案</title>'
            f'<style>{PAGE_CSS}</style><div id="nav">{nav}</div>'
            f'<div id="wrap"><div id="left">{cases}</div>{VIEWER_HTML}</div>'
            f'<script>{PAGE_JS}</script>')


VIEWER_HTML = """<div id="right">
  <div id="stage"><div id="vhint">點左邊任一張縮圖，或點進某案的欄位<br>
    大圖會出現在這裡。滾輪縮放、拖曳平移、雙擊還原</div></div>
  <div id="vbar">
    <button type="button" onclick="rot(-90)">⟲ 左轉</button>
    <button type="button" onclick="rot(90)">⟳ 右轉</button>
    <b id="vdeg"></b>
    <button type="button" onclick="zoom(1/1.25)">－</button>
    <button type="button" onclick="zoom(1.25)">＋</button>
    <button type="button" onclick="fit()">適合視窗</button>
    <span id="vzoom"></span>
    <span id="vname"></span>
  </div>
</div>"""


# ─── 伺服器 ──────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    pairs = []          # [(case, gt)]，由 serve() 填入
    index = {}          # case_id -> case

    # 每頁會併發抓數十張 3MB 的原圖。HTTP/1.0 每張都要重開連線，容易把
    # 連線佇列塞滿，讓同時發出的 POST 直接被拒——瀏覽器端看到的就是
    # 「TypeError: Failed to fetch」，而伺服器上什麼錯都沒有。
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        """只顯示 /save/，但**不可吃掉錯誤**。

        原本寫成 `if '/save/' in args[0]`：log_error() 傳進來的 args[0] 是
        int（"code %d, message %s"），比對會丟 TypeError，於是連錯誤都送不出去。
        """
        try:
            line = fmt % args
        except Exception:
            line = ' '.join(str(a) for a in args)
        if '/save/' in line or 'code 4' in line or 'code 5' in line:
            sys.stderr.write(f"  {line}\n")

    def handle_one_request(self):
        # 例外若竄到 socketserver 才被接住，連線已經斷了，前端只拿得到
        # 「Failed to fetch」。在這裡攔下來至少能把 traceback 印在終端機上。
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            pass        # 瀏覽器捲動時取消 lazy 圖片的請求，屬正常
        except Exception:
            traceback.print_exc()

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    _thumbs = {}        # 圖片相對路徑 -> 縮圖 bytes；只在記憶體，不在測試集目錄留檔
    _thumb_lock = threading.Lock()

    def _thumb(self):
        """現產 400px 縮圖。不寫檔：測試集目錄會整包備份，多一個快取資料夾只是雜訊。"""
        from urllib.parse import unquote
        norm = posixpath.normpath(unquote(self.path[len('/thumb/'):]))
        if norm.startswith(('../', '/')) or not norm.startswith('images/'):
            return self._send(403, b'forbidden', 'text/plain')
        full = os.path.join(HERE, *norm.split('/'))
        if not os.path.isfile(full):
            return self._send(404, b'not found', 'text/plain')
        data = self._thumbs.get(norm)
        if data is None:
            from io import BytesIO
            from PIL import Image, ImageOps
            with Image.open(full) as im:
                im.draft('RGB', (800, 800))      # JPEG 直接以縮小尺度解碼，快很多
                im = ImageOps.exif_transpose(im)  # 與瀏覽器顯示原圖的方向一致
                im.thumbnail((400, 400))
                buf = BytesIO()
                im.convert('RGB').save(buf, 'JPEG', quality=80)
            data = buf.getvalue()
            with self._thumb_lock:
                self._thumbs[norm] = data
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'max-age=3600')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            return self._send(200, build_page(self.pairs), 'text/html; charset=utf-8')
        if self.path.startswith('/thumb/'):
            return self._thumb()
        if self.path.startswith('/img/'):
            from urllib.parse import unquote
            rel = unquote(self.path[5:])
            # 只允許 images/ 底下的檔案——避免路徑穿越把 repo 其他檔案送出去
            norm = posixpath.normpath(rel)
            if norm.startswith(('../', '/')) or not norm.startswith('images/'):
                return self._send(403, b'forbidden', 'text/plain')
            full = os.path.join(HERE, *norm.split('/'))
            if not os.path.isfile(full):
                return self._send(404, b'not found', 'text/plain')
            # 照片在編輯期間不會變，讓瀏覽器快取——否則每次捲動回去都重抓 3MB，
            # 大量重複傳輸正是壓垮連線佇列的原因
            with open(full, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'max-age=3600')
            self.end_headers()
            return self.wfile.write(data)
        return self._send(404, b'not found', 'text/plain')

    def do_POST(self):
        # 任何未預期的例外都要變成一則 JSON 錯誤回給前端。讓它竄出去只會
        # 斷連線，使用者看到「Failed to fetch」卻不知道是哪裡出錯。
        try:
            return self._do_post()
        except Exception:
            traceback.print_exc()
            return self._send(500, json.dumps(
                {'ok': False, 'errors': ['伺服器錯誤，詳見終端機的 traceback：'
                                         + traceback.format_exc().strip().splitlines()[-1]]},
                ensure_ascii=False))

    def _confirm(self):
        from urllib.parse import unquote
        cid = unquote(self.path[len('/confirm/'):])
        case = self.index.get(cid)
        if not case:
            return self._send(404, json.dumps(
                {'ok': False, 'errors': [f'未載入的案例：{cid}']}, ensure_ascii=False))
        n = int(self.headers.get('Content-Length') or 0)
        try:
            payload = json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception as e:
            return self._send(400, json.dumps(
                {'ok': False, 'errors': [f'請求解析失敗：{e}']}, ensure_ascii=False))
        by = (payload.get('by') or '').strip()[:40]
        if not by:
            return self._send(200, json.dumps(
                {'ok': False, 'errors': ['沒有填名字']}, ensure_ascii=False))
        # 雜湊用磁碟上的正解，不用記憶體副本——確認的對象是實際會被評分的那份檔
        path = casetool.gt_path(cid, case['category'])
        gt = json.load(open(path, encoding='utf-8'))
        device = tailnet_device(self.client_address[0])
        with _REVIEW_LOCK:
            reviews = load_reviews()
            if payload.get('confirmed'):
                reviews[cid] = {'by': by, 'device': device,
                                'at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                                'hash': content_hash(case, gt)}
            else:
                reviews.pop(cid, None)
            save_reviews(reviews)
        _log(f"[{'確認' if payload.get('confirmed') else '取消確認'}] {cid} — {by}"
             + (f"（{device}）" if device else ''))
        return self._send(200, json.dumps(
            {'ok': True, 'review': review_state(case, gt, reviews)}, ensure_ascii=False))

    def _do_post(self):
        if self.path.startswith('/confirm/'):
            return self._confirm()
        if not self.path.startswith('/save/'):
            return self._send(404, json.dumps({'ok': False, 'errors': ['未知路徑']}))
        from urllib.parse import unquote
        cid = unquote(self.path[len('/save/'):])
        case = self.index.get(cid)
        if not case:
            return self._send(404, json.dumps(
                {'ok': False, 'errors': [f'未載入的案例：{cid}']}, ensure_ascii=False))

        n = int(self.headers.get('Content-Length') or 0)
        try:
            payload = json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception as e:
            return self._send(400, json.dumps(
                {'ok': False, 'errors': [f'請求解析失敗：{e}']}, ensure_ascii=False))

        cat = case['category']
        path = casetool.gt_path(cid, cat)
        old = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}

        gt, errors = validate(payload.get('gt') or {}, existing=old)
        if errors:
            return self._send(200, json.dumps(
                {'ok': False, 'errors': errors}, ensure_ascii=False))

        # 正解內容的變動與 gt_source 的變動要分開判斷。gt_source 存在 cases.json，
        # 把它併進同一個旗標會讓「只改來源標記」也去重寫正解檔——檔案內容一個字
        # 沒變卻整份重排，git 上看起來像改了一整檔，真正的修訂被雜訊蓋掉。
        gt_changed = diff_summary(old, gt)
        changed = list(gt_changed)

        want_src = payload.get('gt_source')
        if want_src and want_src != (case.get('gt_source') or 'unknown'):
            src_err = set_gt_source(cid, want_src)
            if src_err:
                return self._send(200, json.dumps(
                    {'ok': False, 'errors': [src_err]}, ensure_ascii=False))
            changed.append(f"gt_source: {case.get('gt_source') or 'unknown'} → {want_src}")
            case['gt_source'] = want_src

        for fld, val in (payload.get('case_fields') or {}).items():
            if fld not in CASE_FIELDS:
                continue
            val, old_v = (val or ''), (case.get(fld) or '')
            if val == old_v:
                continue
            err = set_case_field(cid, fld, val)
            if err:
                return self._send(200, json.dumps(
                    {'ok': False, 'errors': [err]}, ensure_ascii=False))
            changed.append(f"{CASE_FIELDS[fld][2]}: {old_v or '(未填)'} → {val or '(未填)'}")
            if val:
                case[fld] = val
            else:
                case.pop(fld, None)

        if gt_changed:
            write_gt(cid, cat, gt)
            # 記憶體中的副本同步更新，否則重新整理頁面會看到舊值
            for i, (c, _) in enumerate(self.pairs):
                if c['case_id'] == cid:
                    self.pairs[i] = (c, gt)
        if changed:
            _log(f"[已寫入] {cid}")
            for line in changed:
                _log(f"    {line}")
        # 回傳存檔後的確認狀態：改了已確認的案例，前端要立刻顯示「確認後有修改」
        cur = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else gt
        return self._send(200, json.dumps(
            {'ok': True, 'changed': changed,
             'review': review_state(case, cur, load_reviews())}, ensure_ascii=False))


def serve(argv):
    # Windows console 預設 cp950，印出成分／廠商名很容易撞到編不出的字。
    # errors='replace' 讓最壞情況只是終端機顯示問號，不會中斷請求。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    _check_skeleton_sync()
    port, want_cat, want_src, want_id = 8765, None, None, None
    host = '127.0.0.1'
    for a in argv:
        if a.startswith('--port='):
            port = int(a.split('=', 1)[1])
        elif a == '--tailscale':
            # 只綁 Tailscale 介面，理由同 review_server.py：綁 0.0.0.0 會讓同網段
            # 任何人都能寫入正解。這是可寫的伺服器，暴露面比唯讀檢視站更要緊。
            from review_server import tailscale_ip
            host = tailscale_ip()
            if not host:
                sys.exit("取不到 Tailscale IP，確認 Tailscale 在執行。")
        elif a.startswith('--category='):
            want_cat = a.split('=', 1)[1]
        elif a.startswith('--source='):
            want_src = a.split('=', 1)[1]
        elif not a.startswith('--'):
            want_id = a
        else:
            sys.exit(f"看不懂的參數：{a}")

    pairs = []
    for c in casetool.load_cases()['cases']:
        if want_id and c['case_id'] != want_id:
            continue
        if want_cat and c['category'] != want_cat:
            continue
        if want_src and (c.get('gt_source') or 'unknown') != want_src:
            continue
        gp = casetool.gt_path(c['case_id'], c['category'])
        if not os.path.exists(gp):
            print(f"[略過] {c['case_id']} 沒有正解檔（新案例請用 intake.py）")
            continue
        pairs.append((c, json.load(open(gp, encoding='utf-8'))))
    if not pairs:
        sys.exit("沒有符合條件的案例。")
    # cases.json 是字串排序（c09 → c100…c109 → c10），照編號數字排才好找
    import re

    def _natkey(c):
        m = re.match(r'c(\d+)', c['case_id'])
        return (int(m.group(1)) if m else 10 ** 9, c['case_id'])
    pairs.sort(key=lambda p: _natkey(p[0]))

    Handler.pairs = pairs
    Handler.index = {c['case_id']: c for c, _ in pairs}
    url = f'http://{host}:{port}/'
    ThreadingHTTPServer.daemon_threads = True   # Ctrl+C 不必等連線收乾淨
    ThreadingHTTPServer.request_queue_size = 64  # 一頁會併發抓數十張圖
    # **必須關掉位址重用。** HTTPServer 預設 allow_reuse_address=1，在 Windows 上
    # 那等同 SO_REUSEPORT：第二個實例會安靜地綁上同一個埠，連線隨機分派到其中
    # 一個行程。實測同時有三個實例聽在 8765，瀏覽器按儲存時打到半死的舊實例，
    # 前端只看得到「TypeError: Failed to fetch」。寧可啟動就失敗。
    ThreadingHTTPServer.allow_reuse_address = False
    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        sys.exit(f"埠 {port} 已被佔用（{e}）。\n"
                 f"先關掉舊的編輯頁伺服器，或用 --port= 換一個。\n"
                 f"查佔用者：netstat -ano | findstr :{port}   然後 taskkill /PID <pid> /F")
    print(f"正解編輯頁：{url}（{len(pairs)} 案）")
    print("改完記得：調 set_version → python casetool.py check → 更新 README 版本沿革")
    print("Ctrl+C 結束\n")
    webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已結束。")
    finally:
        srv.server_close()


def main():
    args = sys.argv[1:]
    if not args or args[0] != 'serve':
        sys.exit(__doc__ or "用法：python edit_gt.py serve [--port=8765] "
                            "[--category=X] [--source=Y] [case_id]")
    serve(args[1:])


if __name__ == '__main__':
    main()
