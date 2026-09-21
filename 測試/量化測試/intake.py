#!/usr/bin/env python3
# _上傳區的固定進件流程：轉檔 → 進件工作單（含全套正解轉錄）→ 套用建檔。
#
# 為何要有這支：新照片進測試集要跨四個檔案（images/、cases.json、
# ground_truth/、manifest.json），其中正解轉錄是「對著照片手改 JSON」——
# 既無法邊看圖邊填，欄位又多（營養兩欄各九項），錯一格評分就失真。
# 本工具沿用 label_difficulty.py 的模式：工具產 HTML、瀏覽器看圖填寫、
# 匯出 JSON、apply 寫回；「看圖判斷」與「寫入檔案」分開。
#
#   convert   _上傳區 內非 JPG（HEIC/PNG/WebP）轉為原解析度 JPG，
#             原始檔移入 _上傳區/_原始檔/ 留底。根目錄的散圖與案例資料夾
#             都處理——散圖必須先轉，瀏覽器不會顯示 HEIC，分組工作單才看得到。
#             不縮圖不壓縮——壓縮是 harness 用 EVAL_IMAGE_ROOT 的事
#             （見 compress_images.py）
#   group     一件商品常有多張照片（正面／成分欄／營養標示），從手機倒出來
#             是一堆散圖。此步產生 _group_worksheet.html：點選同一商品的
#             照片、填 case_id、選類別按「成組」，匯出 groups.json 後以
#             group <groups.json> 搬成 _上傳區/<類別>/<case_id>/ 案例資料夾
#   generate  產生 _intake_worksheet.html：每案照片＋desc＋difficulty＋
#             完整正解表單。若同目錄有 intake.json（上次匯出），自動預填。
#             每案有「人工已檢查」勾選框——正解可由 draft_gt.py 產 LLM 初稿，
#             但初稿與人工校對過的不能混在同一個切片裡比較，所以勾選框直接
#             對到 cases.json 的 gt_source（勾＝llm_checked，沒勾＝unknown）
#             每張照片有左轉／右轉按鈕；角度會隨 intake.json 匯出，
#             apply 時實際寫回 JPEG（沿用原量化表），不是只轉給人看
#   apply     驗證後一次完成：照片搬入 images/<類別>/、cases.json 登錄、
#             ground_truth 寫入。收尾仍跑 manifest_tool generate 與 casetool check
#
# 正解欄位的權威來源是 casetool.FOOD_SKELETON（與 score_eval.py 一致）；
# 本工具啟動時檢查骨架是否變動，變動未同步表單即拒絕執行，避免靜默漏欄。
#
# 用法：
#   python intake.py convert
#   python intake.py group                # 產生分組工作單
#   python intake.py group groups.json    # 套用分組，搬成案例資料夾
#   python intake.py generate
#   python intake.py apply intake.json [--set-version=v3.0]
import copy
import html
import json
import os
import re
import shutil
import sys

from PIL import Image, ImageOps

import casetool
from label_difficulty import TAGS

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「蘋菓西打」的「菓」）。
# 沒有這一行的後果不是印出亂碼而是**整支腳本在搬檔迴圈中途炸掉**——
# 2026-09-06 真的發生過：11 案照片已搬走、正解已寫，但 cases.json 尚未存檔
# （save_cases 在迴圈之後），變成半套用狀態，得手動把資料夾搬回去、
# 刪掉正解、還要把已套用的旋轉轉回來。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
# 進件暫存區與工作單的位置。**可移到雲端硬碟**（2026-09-10 加）——
# 這兩者都不屬於測試集本體：`_上傳區` 的 README 寫明 harness 與 casetool
# 都不讀它，工作單則是「產生 → 在瀏覽器填 → 下載 JSON」的中介物。
#
# ⚠ **`ground_truth/`、`images/`、`cases.json` 不可移動**——
#    `score_ocr.load_gt()` 等以 EVAL_ROOT 的相對路徑讀取，移了整套評分會壞。
#    正確的做法是把「填寫用的工作單」放雲端，`apply` 仍寫回本地的 ground_truth/。
#
# ⚠ **雲端同步與本腳本會打架**：`convert` 會搬動數百 MB 的照片、
#    `group` 會建資料夾。執行前先確認同步完成，否則可能讀到半個檔案，
#    或被雲端產生「衝突副本」。
INBOX = os.environ.get('EVAL_INBOX') or os.path.join(HERE, '_上傳區')
ORIGINALS = os.path.join(INBOX, '_原始檔')
WORKDIR = os.environ.get('EVAL_WORKSHEET_DIR') or HERE
OUT_HTML = os.path.join(WORKDIR, '_intake_worksheet.html')
INTAKE_NAME = 'intake.json'
GROUP_HTML = os.path.join(WORKDIR, '_group_worksheet.html')
# 縮圖要跟著工作單走——工作單以**相對路徑**引用縮圖（`src="_thumbs/..."`），
# 兩者不在同一個目錄，開啟工作單就會整頁沒有圖。
# 2026-09-10：把工作單搬到雲端時踩到這個（314 個引用全缺）。
THUMBS = os.path.join(WORKDIR, '_thumbs')
THUMB_PX = 400


def _build_thumbs(cases):
    """替工作單產小縮圖。

    為什麼不能直接把原檔縮小顯示：手機照片是 4032×3024，瀏覽器不管 CSS 標多小，
    都得先解碼成 4032×3024 的點陣圖（單張約 48 MB）。157 張共約 360 MB 的檔案、
    數 GB 的解碼結果，全靠瀏覽器邊丟邊重解撐著。縮圖檔 400px 後總共 4.1 MB。

    ⚠ 但這不是「打勾會卡」的原因。2026-09-05 在同一台機器上 A/B 過（157 張
    全部載入、量點擊到畫面更新）：原檔 18–24 ms、縮圖 18–24 ms，沒有差別，
    也沒有長任務。省的是磁碟與記憶體，不是那個延遲——別拿這個當卡頓的解釋。
    """
    n = 0
    for cat, cid, files in cases:
        sdir = os.path.join(INBOX, cat, cid)
        ddir = os.path.join(THUMBS, cat, cid)
        for fn in files:
            sp, dp = os.path.join(sdir, fn), os.path.join(ddir, fn)
            if not fn.lower().endswith('.jpg'):
                continue                      # 還沒 convert，apply 也會擋
            if os.path.exists(dp) and os.path.getmtime(dp) >= os.path.getmtime(sp):
                continue                      # 增量：原檔沒動就不重做
            os.makedirs(ddir, exist_ok=True)
            with Image.open(sp) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail((THUMB_PX, THUMB_PX))
                im.convert('RGB').save(dp, 'JPEG', quality=80)
            n += 1
    return n


def _find(name):
    """匯出的 JSON 放本目錄或 _上傳區/ 都認得——瀏覽器下載後隨手往哪丟都行。"""
    p = os.path.join(INBOX, name)
    return p if os.path.exists(p) else os.path.join(HERE, name)

# 包裝形狀／反光材質的中文顯示名。鍵一律以 casetool 的字彙為準
PKG_SHAPE_ZH = {'carton': '平面紙盒', 'can': '圓柱罐', 'pouch': '軟袋',
                'bottle': '曲面瓶', 'shrink_wrap': '收縮膜',
                'tray': '塑膠盒', 'cup': '杯裝'}
REFLECT_ZH = {'foil': '鋁箔鍍膜', 'glossy_plastic': '亮面塑膠',
              'matte_paper': '霧面紙', 'clear_film': '透明膜'}
# 正解不唯一 → 該欄不計分。值域見 casetool.VALID_EXCLUDE_REASON 的三道約束
EXCLUDE_ZH = {'multi_product_panel': '一包多商品（各有各的營養標示）'}


def _select(key, zh_label, vocab_zh, cur):
    opts = '<option value="">—</option>' + ''.join(
        f'<option value="{k}"{" selected" if cur == k else ""}>{v}</option>'
        for k, v in vocab_zh.items())
    return (f'<label class="sel">{zh_label}'
            f'<select data-k="{key}">{opts}</select></label>')


# 顯示用中文；鍵名一律以 FOOD_SKELETON 為準
NUTRI_ZH = {
    'calories': '熱量(大卡)', 'protein': '蛋白質(g)', 'fat': '脂肪(g)',
    'saturated_fat': '飽和脂肪(g)', 'trans_fat': '反式脂肪(g)',
    'carbohydrates': '碳水化合物(g)', 'sugar': '糖(g)',
    'fiber': '膳食纖維(g)', 'sodium': '鈉(mg)',
}


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
        sys.exit("casetool.FOOD_SKELETON 已變動，請同步更新 intake.py 的表單後再執行。")


def scan_loose():
    """_上傳區 根目錄的散圖（尚未分組成案例資料夾者）。"""
    if not os.path.isdir(INBOX):
        return []
    return [f for f in sorted(os.listdir(INBOX))
            if os.path.isfile(os.path.join(INBOX, f))
            and f.lower().endswith(casetool._EXTS) and not f.startswith('._')]


def scan_inbox():
    """[(category, case_id, [檔名…]), …]，只掃 VALID_CATEGORY 資料夾。"""
    out = []
    for cat in sorted(casetool.VALID_CATEGORY):
        d = os.path.join(INBOX, cat)
        if not os.path.isdir(d):
            continue
        for cid in sorted(os.listdir(d)):
            cdir = os.path.join(d, cid)
            if not os.path.isdir(cdir):
                continue
            files = [f for f in sorted(os.listdir(cdir))
                     if f.lower().endswith(casetool._EXTS) and not f.startswith('._')]
            if files:
                out.append((cat, cid, files))
    return out


# ─── convert ──────────────────────────────────────────────────────────────────

def _to_jpg(cdir, fn, keep_dir):
    """單檔轉正：回傳 'jpg'（本來就是）｜'renamed'（.jpeg 改名）｜'conv'（重編碼）。"""
    base, ext = os.path.splitext(fn)
    ext = ext.lower()
    sp = os.path.join(cdir, fn)
    if ext == '.jpg':
        return 'jpg'
    dp = os.path.join(cdir, base + '.jpg')
    if os.path.exists(dp):
        sys.exit(f"[錯誤] {os.path.relpath(dp, INBOX)} 已存在，"
                 f"無法轉換同名的 {fn}。請先改名。")
    if ext == '.jpeg':
        # 已是 JPEG，只改副檔名，不重編碼（重編碼徒增畫質損失）
        os.rename(sp, dp)
        return 'renamed'
    if ext == '.heic':
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            sys.exit("讀 HEIC 需要 pillow-heif：pip install pillow-heif")
    with Image.open(sp) as im:
        # 手機照片方向存於 EXIF，轉檔時烙進像素，
        # 否則工作單與部分讀圖程式會看到轉了 90 度的圖
        im = ImageOps.exif_transpose(im)
        im.convert('RGB').save(dp, 'JPEG', quality=95)
    os.makedirs(keep_dir, exist_ok=True)
    shutil.move(sp, os.path.join(keep_dir, fn))
    return 'conv'


def convert():
    loose = scan_loose()
    cases = scan_inbox()
    if not loose and not cases:
        sys.exit("_上傳區 沒有照片（根目錄散圖或 <類別>/<case_id>/ 資料夾皆空）。")

    n = {'jpg': 0, 'conv': 0, 'renamed': 0}
    for fn in loose:
        n[_to_jpg(INBOX, fn, os.path.join(ORIGINALS, '_未分組'))] += 1
    for cat, cid, files in cases:
        cdir = os.path.join(INBOX, cat, cid)
        for fn in files:
            n[_to_jpg(cdir, fn, os.path.join(ORIGINALS, cat, cid))] += 1

    print(f"完成：{n['conv']} 張轉為 JPG（原始檔移入 _原始檔/），"
          f"{n['renamed']} 張 .jpeg 改名，{n['jpg']} 張本來就是 .jpg。")
    if loose:
        print(f"根目錄有 {len(loose)} 張散圖，接著分組：python intake.py group")
    else:
        print("接著：python intake.py generate")


# ─── group ────────────────────────────────────────────────────────────────────

def group_generate():
    loose = scan_loose()
    if not loose:
        sys.exit("_上傳區 根目錄沒有散圖。照片直接倒進 _上傳區/ 後先跑 convert。")
    not_jpg = [f for f in loose if not f.lower().endswith('.jpg')]
    if not_jpg:
        sys.exit("散圖仍有非 JPG（瀏覽器顯示不了 HEIC）："
                 + '、'.join(not_jpg[:5]) + "\n請先跑：python intake.py convert")

    taken = {c['case_id'] for c in casetool.load_cases()['cases']}
    taken |= {cid for _, cid, _ in scan_inbox()}
    nums = [int(m.group(1)) for cid in taken
            if (m := re.match(r'^c(\d+)_', cid))]
    next_id = f"c{max(nums) + 1:02d}_" if nums else "c01_"

    # 歷史預填：groups.json 中檔案仍全在散圖區的組別，重開工作單不必重分
    prior = {}
    groups_path = _find('groups.json')
    if os.path.exists(groups_path):
        loose_set = set(loose)
        for cid, g in json.load(open(groups_path, encoding='utf-8')).items():
            if cid not in taken and set(g.get('files') or []) <= loose_set:
                prior[cid] = g
        if prior:
            print(f"已載入 groups.json 的歷史分組（{len(prior)} 組）。")

    figs = ''.join(
        f'<figure class="ph" data-f="{html.escape(f)}">'
        f'<img src="_上傳區/{html.escape(f)}" loading="lazy">'
        f'<figcaption>{html.escape(f)}</figcaption><b class="tag"></b></figure>'
        for f in loose)
    cats = ''.join(f'<option>{c}</option>'
                   for c in sorted(casetool.VALID_CATEGORY))

    page = """<!doctype html><meta charset="utf-8">
<title>照片分組工作單</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0 auto; max-width: 1100px;
         padding: 0 16px; }
  #top { position: sticky; top: 0; background: #f4f4f4; padding: 10px;
         border-bottom: 2px solid #bbb; z-index: 2; }
  #top input, #top select, #top button { font-size: 1em; padding: 6px 10px; }
  #cid { width: 220px; }
  .hint { background: #fff6d6; border: 1px solid #e0c96a; padding: 8px 12px;
          border-radius: 8px; margin: 10px 0; line-height: 1.6; }
  #grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
          gap: 10px; margin: 12px 0; }
  .ph { margin: 0; border: 3px solid #ddd; border-radius: 8px; padding: 4px;
        cursor: pointer; position: relative; }
  .ph img { width: 100%; height: 180px; object-fit: contain; display: block; }
  .ph figcaption { font-size: .72em; color: #666; word-break: break-all; }
  .ph.sel { border-color: #06c; background: #eef5ff; }
  .ph.asg { border-color: #2a7; opacity: .55; }
  .ph .tag { position: absolute; top: 6px; left: 6px; background: #2a7; color: #fff;
             font-size: .72em; padding: 1px 6px; border-radius: 4px; display: none; }
  .ph.asg .tag { display: block; }
  #glist { margin: 10px 0; }
  #glist li button { margin-left: 8px; }
  #bar { position: sticky; bottom: 0; background: #f4f4f4; padding: 10px;
         border-top: 2px solid #bbb; text-align: center; }
</style>
<div id="top">
  case_id：<input id="cid" placeholder="__NEXT__反光洋芋片" value="__NEXT__">
  類別：<select id="cat">__CATS__</select>
  <button onclick="makeSet()">把選取的照片成組</button>
  <span id="topmsg"></span>
</div>
<div class="hint">
  點照片選取（藍框）同一件商品的所有照片 → 填 case_id、選類別 → 按「成組」。
  綠色為已分組，點一下可自組中移出。全部分完（或先分一部分）後按「匯出」。
</div>
<div id="grid">__FIGS__</div>
<ul id="glist"></ul>
<div id="bar">
  <button onclick="exp()">匯出 groups.json</button>
  <span id="msg"></span>
</div>
<script>
const TAKEN = __TAKEN__;
const groups = __PRIOR__;
const byFile = {};
Object.entries(groups).forEach(([cid, g]) => g.files.forEach(f => byFile[f] = cid));

function render() {
  document.querySelectorAll('.ph').forEach(ph => {
    const cid = byFile[ph.dataset.f];
    ph.classList.toggle('asg', !!cid);
    if (cid) { ph.classList.remove('sel'); ph.querySelector('.tag').textContent = cid; }
  });
  const ul = document.getElementById('glist');
  ul.innerHTML = '';
  Object.entries(groups).forEach(([cid, g]) => {
    const li = document.createElement('li');
    li.textContent = cid + '（' + g.category + '，' + g.files.length + ' 張）';
    const btn = document.createElement('button');
    btn.textContent = '解散';
    btn.onclick = () => { g.files.forEach(f => delete byFile[f]); delete groups[cid]; render(); };
    li.appendChild(btn);
    ul.appendChild(li);
  });
}
document.querySelectorAll('.ph').forEach(ph => ph.onclick = () => {
  const f = ph.dataset.f, cid = byFile[f];
  if (cid) {  // 自組中移出；組空了就順便解散
    const g = groups[cid];
    g.files = g.files.filter(x => x !== f);
    delete byFile[f];
    if (!g.files.length) delete groups[cid];
    render();
  } else { ph.classList.toggle('sel'); }
});
function makeSet() {
  const cid = document.getElementById('cid').value.trim();
  const cat = document.getElementById('cat').value;
  const sel = [...document.querySelectorAll('.ph.sel')].map(p => p.dataset.f);
  const msg = document.getElementById('topmsg');
  if (!sel.length) { msg.textContent = '尚未選取照片'; return; }
  if (!/^c\\d+_.+/.test(cid)) { msg.textContent = 'case_id 格式：c編號_名稱'; return; }
  if (TAKEN.includes(cid) || groups[cid]) { msg.textContent = cid + ' 已被使用'; return; }
  groups[cid] = { category: cat, files: sel };
  sel.forEach(f => byFile[f] = cid);
  msg.textContent = '';
  const m = cid.match(/^c(\\d+)_/);
  document.getElementById('cid').value =
    'c' + String(Number(m[1]) + 1).padStart(2, '0') + '_';
  render();
}
function exp() {
  const blob = new Blob([JSON.stringify(groups, null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'groups.json';
  a.click();
  const left = document.querySelectorAll('.ph:not(.asg)').length;
  document.getElementById('msg').textContent =
    (left ? '（' + left + ' 張未分組，會留在原地）' : '') +
    ' 已下載。接著：python intake.py group groups.json';
}
render();
</script>"""
    page = (page.replace('__FIGS__', figs).replace('__CATS__', cats)
                .replace('__NEXT__', next_id)
                .replace('__TAKEN__', json.dumps(sorted(taken), ensure_ascii=False))
                .replace('__PRIOR__', json.dumps(prior, ensure_ascii=False)))
    with open(GROUP_HTML, 'w', encoding='utf-8') as f:
        f.write(page)
    print(f"已產生 {os.path.basename(GROUP_HTML)}（{len(loose)} 張散圖，"
          f"下一個編號建議 {next_id}）。")
    print("用瀏覽器開啟、分組後按「匯出」，再執行：")
    print("  python intake.py group groups.json")


def group_apply(path):
    entries = json.load(open(path, encoding='utf-8'))
    if not entries:
        sys.exit("groups.json 是空的。")
    loose = set(scan_loose())
    taken = {c['case_id'] for c in casetool.load_cases()['cases']}
    taken |= {cid for _, cid, _ in scan_inbox()}

    errors, claimed = [], {}
    for cid, g in entries.items():
        cat = g.get('category')
        if cat not in casetool.VALID_CATEGORY:
            errors.append(f"{cid}：未知類別 {cat}")
        if cid in taken:
            errors.append(f"{cid}：case_id 已被使用（cases.json 或 _上傳區）")
        if not g.get('files'):
            errors.append(f"{cid}：沒有照片")
        for f in g.get('files') or []:
            if f not in loose:
                errors.append(f"{cid}：散圖區找不到 {f}（已搬走？）")
            if f in claimed:
                errors.append(f"{f} 同時被 {claimed[f]} 與 {cid} 認領")
            claimed[f] = cid
    if errors:
        for m in errors:
            print(f"[錯誤] {m}")
        sys.exit("未搬動任何檔案。")

    for cid, g in entries.items():
        d = os.path.join(INBOX, g['category'], cid)
        os.makedirs(d, exist_ok=True)
        for f in g['files']:
            shutil.move(os.path.join(INBOX, f), os.path.join(d, f))
        print(f"已成組 {g['category']}/{cid}（{len(g['files'])} 張）")

    left = len(loose) - len(claimed)
    print(f"\n共 {len(entries)} 組" + (f"，{left} 張散圖未分組留在原地" if left else "")
          + "。接著：python intake.py generate")


# ─── generate ─────────────────────────────────────────────────────────────────

def _text_input(cid, path, label, prior, kind='text', textarea=False, hint=''):
    v = prior
    for p in path.split('.'):
        v = (v or {}).get(p) if isinstance(v, dict) else None
    if isinstance(v, list):
        v = '\n'.join(v) if kind == 'lines' else '、'.join(v)
    v = html.escape(str(v)) if v not in (None, '') else ''
    attrs = f'data-gt="{path}" data-kind="{kind}"'
    hint_html = f'<span class="hint2">{hint}</span>' if hint else ''
    if textarea:
        return (f'<label class="f">{label}{hint_html}'
                f'<textarea {attrs} rows="3">{v}</textarea></label>')
    typ = 'number" step="any' if kind == 'num' else 'text'
    return (f'<label class="f">{label}{hint_html}'
            f'<input type="{typ}" {attrs} value="{v}"></label>')


def _nutri_table(prior):
    rows = []
    for k, zh in NUTRI_ZH.items():
        cells = ''
        for col in ('nutrition', 'nutrition_per_serving'):
            v = ((prior or {}).get(col) or {}).get(k)
            v = '' if v is None else html.escape(str(v))
            cells += (f'<td><input type="number" step="any" '
                      f'data-gt="{col}.{k}" data-kind="num" value="{v}"></td>')
        rows.append(f'<tr><th>{zh}</th>{cells}</tr>')
    return ('<table class="nutri"><tr><th></th><th>每100g/mL</th><th>每份</th></tr>'
            + ''.join(rows) + '</table>')


def generate():
    _check_skeleton_sync()
    cases = scan_inbox()
    if not cases:
        sys.exit("_上傳區 沒有任何案例資料夾（_上傳區/<類別>/<case_id>/照片）。")

    # 歷史預填：同 label_difficulty 的教訓——重產工作單不該丟掉已填的內容
    prior_all = {}
    intake_path = _find(INTAKE_NAME)
    if os.path.exists(intake_path):
        prior_all = json.load(open(intake_path, encoding='utf-8'))
        print(f"已載入 intake.json 的歷史填寫（{len(prior_all)} 案）。")

    pending = [(cat, cid, fs) for cat, cid, fs in cases
               if any(not f.lower().endswith('.jpg') for f in fs)]
    for cat, cid, fs in pending:
        print(f"[提醒] {cat}/{cid} 仍有非 JPG 檔，apply 會擋下；請先跑 convert。")

    paired = casetool.paired_ids()
    n_thumb = _build_thumbs(cases)
    if n_thumb:
        print(f"已產生 {n_thumb} 張縮圖 → _thumbs/")

    registered = {c['case_id'] for c in casetool.load_cases()['cases']}
    cards = []
    for cat, cid, files in cases:
        prior = prior_all.get(cid) or {}
        gt_prior = prior.get('gt') or {}
        note = ('<p class="warn">此 case_id 已存在於 cases.json，apply 會擋下。</p>'
                if cid in registered else '')
        rot_prior = prior.get('rotate') or {}
        # 卡片只放縮圖，點了在右欄大圖檢視——標示上的字很小，
        # 480px 高的圖看不清，來回捲動又慢
        imgs = ''.join(
            f'<figure class="im" data-f="{html.escape(f)}"'
            f' data-rot="{int(rot_prior.get(f) or 0)}"'
            f' data-src="_上傳區/{html.escape(cat)}/{html.escape(cid)}/{html.escape(f)}"'
            f' onclick="pick(this)">'
            # src 用縮圖檔、data-src 才是原檔：原檔進不了縮圖框，見 _build_thumbs
            f'<img src="_thumbs/{html.escape(cat)}/{html.escape(cid)}/{html.escape(f)}"'
            f' loading="lazy" decoding="async"><figcaption>{html.escape(f)}'
            f' <b class="deg"></b></figcaption></figure>' for f in files)
        desc_v = html.escape(prior.get('desc') or '')
        diff_prior = prior.get('difficulty') or []
        boxes = ''.join(
            f'<label><input type="checkbox" class="diff" value="{en}"'
            f'{" checked" if en in diff_prior else ""}>{zh}'
            f'<span class="en">{en}</span></label>' for en, zh in TAGS)

        if cat == 'non_food':
            form = '<p class="auto">非食品：正解自動完成（is_food_label=false），無須填寫。</p>'
        else:
            form = (
                _text_input(cid, 'name', '產品完整名稱', gt_prior)
                + _text_input(cid, 'brand', '品牌', gt_prior)
                + _text_input(cid, 'manufacturer', '製造商', gt_prior)
                + _text_input(cid, 'ingredients_raw', '成分原文', gt_prior,
                              textarea=True, hint='逐字照抄，含括號與標點')
                + _text_input(cid, 'ingredients_list', '成分清單', gt_prior,
                              kind='lines', textarea=True, hint='一行一項，拆自上欄原文')
                + _nutri_table(gt_prior)
                + _text_input(cid, 'serving_size', '每一份量(g/mL)', gt_prior, kind='num')
                + _text_input(cid, 'servings_per_container', '本包裝含幾份', gt_prior, kind='num')
                + _text_input(cid, 'allergy_warning', '過敏原警語', gt_prior, textarea=True)
                + _text_input(cid, 'certification_marks', '標章', gt_prior,
                              kind='csv', hint='頓號或逗號分隔，如 TQF、CAS'))

        rev = bool(prior.get('reviewed'))
        cards.append(
            f'<div class="card{" done" if rev else ""}" data-cid="{html.escape(cid)}"'
            f' data-cat="{html.escape(cat)}">'
            f'<label class="rev"><input type="checkbox" data-k="reviewed"'
            f'{" checked" if rev else ""}>人工已檢查</label>'
            f'<h3>{html.escape(cid)}<span class="cat">{html.escape(cat)}</span></h3>{note}'
            f'<div class="imgs">{imgs}</div>'
            f'<label class="f">desc（一句話描述）<input type="text" data-k="desc" '
            f'value="{desc_v}"></label>'
            f'<div class="sels">'
            + _select('pkg_shape', '包裝形狀', PKG_SHAPE_ZH, prior.get('pkg_shape'))
            + _select('reflect', '反光材質', REFLECT_ZH, prior.get('reflect'))
            + _select('exclude_reason', '不計分', EXCLUDE_ZH,
                      prior.get('exclude_reason'))
            + ('<b class="pairtag">配對案例：必填</b>' if cid in paired else '')
            + f'</div>'
            f'<div class="boxes">{boxes}</div>{form}</div>')

    page = """<!doctype html><meta charset="utf-8">
<title>進件工作單</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; }
  /* 左欄捲動填表、右欄釘住看大圖。右欄彈出到另一個視窗時 body 加 .popped，
     左欄就吃滿整個寬度 */
  #wrap { display: flex; align-items: flex-start; gap: 16px; padding: 16px; }
  #left { flex: 1; min-width: 0; max-width: 980px; margin: 0 auto; }
  #right { position: sticky; top: 16px; width: 46vw; height: calc(100vh - 100px);
           display: flex; flex-direction: column;
           border: 1px solid #bbb; border-radius: 10px; background: #fafafa; }
  body.popped #right { display: none; }
  #stage { flex: 1; overflow: hidden; position: relative; background: #222;
           border-radius: 9px 9px 0 0; cursor: grab; }
  #stage.drag { cursor: grabbing; }
  #big { position: absolute; left: 50%; top: 50%; max-width: 100%; max-height: 100%;
         transform-origin: center center; will-change: transform; }
  #vbar { padding: 6px 10px; border-top: 1px solid #ddd; font-size: .85em;
          display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  #vbar button { font-size: .85em; padding: 4px 9px; }
  #vname { color: #666; margin-left: auto; overflow: hidden; text-overflow: ellipsis;
           white-space: nowrap; max-width: 40%; }
  #vhint { color: #888; padding: 40% 20px; text-align: center; }
  .hint { background: #fff6d6; border: 1px solid #e0c96a; padding: 10px 14px;
          border-radius: 8px; line-height: 1.6; }
  /* 邊框固定 2px、打勾只換顏色：改寬度會逼整頁 120 張卡重排。
     content-visibility 讓捲出畫面的卡片完全跳過版面與繪製——整份文件約 18 萬
     像素高、一萬兩千個元素，不這樣做的話任何一次互動都要碰整份文件。
     contain-intrinsic-size 的 auto 會記住實際高度，捲軸才不會亂跳。 */
  .card { border: 2px solid #ccc; border-radius: 10px; padding: 12px 16px; margin: 14px 0;
          content-visibility: auto; contain-intrinsic-size: auto 1400px; }
  .card h3 { margin: 0 0 2px; }
  /* 打勾後整張卡變色：120 案要用捲的，逐張點開看勾沒勾太慢 */
  .card.done { border-color: #2a7; background: #f5fbf7; }
  .rev { float: right; font-weight: 600; cursor: pointer; user-select: none;
         background: #eee; border-radius: 6px; padding: 4px 10px; font-size: .9em; }
  .card.done .rev { background: #2a7; color: #fff; }
  body.hideDone .card.done { display: none; }
  #prog { font-weight: 600; margin-right: 14px; }
  .cat { font-size: .75em; color: #666; margin-left: 8px; font-weight: normal; }
  .imgs { display: flex; flex-wrap: wrap; gap: 8px; margin: 6px 0; }
  /* 縮圖固定正方框 ＋ object-fit:contain：這樣旋轉 90 度後一定還在框內，
     不必像大圖那樣重算容器高度 */
  .im { width: 150px; margin: 0; cursor: pointer; }
  .im img { width: 150px; height: 150px; object-fit: contain; background: #eee;
            border: 2px solid #ddd; border-radius: 6px; box-sizing: border-box;
            display: block; transition: transform .15s; }
  .im.sel img { border-color: #06c; }
  .im figcaption { font-size: .65em; color: #888; text-align: center;
                   overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .im .deg { color: #c60; }
  .boxes label { display: inline-block; margin: 4px 12px 4px 0; cursor: pointer; }
  .sels { margin: 6px 0; }
  .sels .sel { margin-right: 16px; font-weight: 600; font-size: .9em; }
  .sels select { font: inherit; font-weight: 400; margin-left: 5px; padding: 3px 5px; }
  .pairtag { color: #c60; font-size: .8em; }
  .en { color: #999; font-size: .75em; margin-left: 4px; }
  .f { display: block; margin: 8px 0; font-weight: 600; }
  .f input[type=text], .f input[type=number], .f textarea {
    display: block; width: 100%; box-sizing: border-box; margin-top: 3px;
    font: inherit; font-weight: 400; padding: 5px 8px; }
  .hint2 { color: #888; font-size: .8em; font-weight: 400; margin-left: 8px; }
  .nutri { border-collapse: collapse; margin: 8px 0; }
  .nutri th, .nutri td { border: 1px solid #ddd; padding: 3px 8px; font-size: .9em; }
  .nutri input { width: 90px; border: none; font: inherit; }
  .auto { color: #2a7; font-weight: 600; }
  .warn { color: #c33; font-weight: 600; }
  #bar { position: sticky; bottom: 0; background: #f4f4f4; padding: 10px;
         border-top: 2px solid #bbb; text-align: center; }
  button { font-size: 1em; padding: 8px 18px; cursor: pointer; }
</style>
<div id="wrap">
<div id="left">
<div class="hint">
  <b>正解紀律</b>：照著照片上看得到的填。<b>標示上沒有的營養欄位留空（=null），
  不可填 0</b>——「標示為零」才填 0。營養兩欄照抄，不要互相換算。
  成分原文逐字照抄；difficulty 只算影響標示區域的問題，明顯才勾。
  <br><b>看圖</b>：點縮圖 → 右欄放大。滾輪縮放、拖曳平移、雙擊還原。
  雙螢幕可按「另開視窗」把大圖丟到另一個螢幕。
</div>
__CARDS__
</div>
<div id="right">
  <div id="stage"><div id="vhint">點左邊任一張縮圖<br>在這裡放大檢視</div></div>
  <div id="vbar">
    <button type="button" onclick="rot(-90)">⟲ 左轉</button>
    <button type="button" onclick="rot(90)">⟳ 右轉</button>
    <b id="vdeg"></b>
    <button type="button" onclick="fit()">適合視窗</button>
    <span id="vzoom"></span>
    <button type="button" onclick="popout()">另開視窗</button>
    <span id="vname"></span>
  </div>
</div>
</div>
<div id="bar">
  <span id="prog"></span>
  <label style="margin-right:14px;cursor:pointer">
    <input type="checkbox" id="hide" onchange="toggleHide()">只顯示未檢查</label>
  <button onclick="exp()">匯出 intake.json</button>
  <button onclick="clearSaved()" style="background:#eee">清除暫存</button>
  <span id="auto" class="auto"></span>
  <span id="lag" class="warn"></span>
  <span id="msg"></span>
</div>
<script>
function collect() {
  const out = {}, empties = [];
  document.querySelectorAll('.card').forEach(card => {
    const cid = card.dataset.cid, cat = card.dataset.cat;
    const o = { category: cat,
                reviewed: card.querySelector('[data-k=reviewed]').checked,
                rotate: rotsOf(card),
                pkg_shape: card.querySelector('[data-k=pkg_shape]').value,
                reflect: card.querySelector('[data-k=reflect]').value,
                exclude_reason: card.querySelector('[data-k=exclude_reason]').value,
                desc: card.querySelector('[data-k=desc]').value.trim(),
                difficulty: [...card.querySelectorAll('input.diff:checked')]
                            .map(b => b.value) };
    if (cat !== 'non_food') {
      const gt = {};
      card.querySelectorAll('[data-gt]').forEach(el => {
        const path = el.dataset.gt.split('.'), kind = el.dataset.kind || 'text';
        let v;
        if (kind === 'num') {
          const s = el.value.trim();
          v = s === '' || isNaN(Number(s)) ? null : Number(s);
        } else if (kind === 'lines') {
          v = el.value.split('\\n').map(s => s.trim()).filter(Boolean);
        } else if (kind === 'csv') {
          v = el.value.split(/[,\\u3001\\uff0c]/).map(s => s.trim()).filter(Boolean);
        } else { v = el.value.trim(); }
        let t = gt;
        for (let i = 0; i < path.length - 1; i++) { t = t[path[i]] = t[path[i]] || {}; }
        t[path[path.length - 1]] = v;
      });
      if (!gt.name) empties.push(cid);
      o.gt = gt;
    }
    out[cid] = o;
  });
  return { out, empties };
}
function exp() {
  const { out, empties } = collect();
  const blob = new Blob([JSON.stringify(out, null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'intake.json';
  a.click();
  const un = Object.entries(out).filter(([, v]) => !v.reviewed).length;
  document.getElementById('msg').textContent =
    (empties.length ? '（注意：' + empties.join('、') + ' 的 name 仍空白）' : '') +
    (un ? '（' + un + ' 案未打勾，會記為 gt_source=unknown）' : '') +
    ' 已下載。接著：python intake.py apply intake.json';
  save();
}

// ── 大圖檢視 ────────────────────────────────────────────────────────────────
// 轉向在這裡只是 CSS，真正的檔案由 apply 依匯出的 rotate 欄位重寫。
// 兩邊必須一致，否則工作單上是正的、送進辨識管線的還是躺著的。
const V = { fig: null, z: 1, tx: 0, ty: 0, pop: null };
const $ = id => document.getElementById(id);

function degOf(fig) { return ((+fig.dataset.rot || 0) % 360 + 360) % 360; }

function applyRot(fig) {
  const r = degOf(fig);
  fig.querySelector('.deg').textContent = r ? r + '°' : '';
  // 縮圖在正方框裡 object-fit:contain，轉 90 度後可視寬高互換仍在框內
  fig.querySelector('img').style.transform = r ? 'rotate(' + r + 'deg)' : '';
  if (V.fig === fig) render();
}

function pick(fig) {
  document.querySelectorAll('.im.sel').forEach(f => f.classList.remove('sel'));
  fig.classList.add('sel');
  V.fig = fig;
  $('vname').textContent = fig.dataset.f;
  $('vhint').style.display = 'none';
  let img = $('big');
  if (!img) {
    img = document.createElement('img');
    img.id = 'big';
    $('stage').appendChild(img);
    img.addEventListener('load', fit);
  }
  if (img.getAttribute('src') !== fig.dataset.src) img.src = fig.dataset.src;
  else fit();
  if (V.pop && !V.pop.closed) pushPop();
}

function render() {
  const img = $('big');
  if (!img || !V.fig) return;
  const r = degOf(V.fig);
  $('vdeg').textContent = r ? r + '°' : '';
  // left/top 各 50% 再往回推一半，才是以圖心對準 stage 中心；
  // 先平移再旋轉（transform 由右往左套用）
  img.style.transform = 'translate(-50%,-50%) translate(' + V.tx + 'px,' + V.ty
    + 'px) scale(' + V.z + ') rotate(' + r + 'deg)';
  $('vzoom').textContent = Math.round(V.z * 100) + '%';
}

function fit() {
  const img = $('big'), st = $('stage');
  if (!img || !V.fig) return;
  V.tx = V.ty = 0;
  // max-width/height 已讓未旋轉時剛好塞滿；轉 90/270 後可視寬高互換，
  // 得再縮一次才不會爆出 stage
  const w = img.clientWidth, h = img.clientHeight;
  V.z = (degOf(V.fig) % 180 && w && h)
    ? Math.min(1, st.clientWidth / h, st.clientHeight / w) : 1;
  render();
}

function rot(d) {
  if (!V.fig) { alert('先點一張縮圖。'); return; }
  V.fig.dataset.rot = (degOf(V.fig) + d % 360 + 360) % 360;
  applyRot(V.fig);
  fit();
  if (V.pop && !V.pop.closed) pushPop();
  save();                       // 按鈕不觸發 input/change，得自己存
}

function rotsOf(card) {
  const o = {};
  card.querySelectorAll('.im').forEach(f => {
    const r = degOf(f);
    if (r) o[f.dataset.f] = r;
  });
  return o;
}

// ── 縮放與平移 ──────────────────────────────────────────────────────────────
$('stage').addEventListener('wheel', ev => {
  if (!V.fig) return;
  ev.preventDefault();
  V.z = Math.min(12, Math.max(0.1, V.z * (ev.deltaY < 0 ? 1.15 : 1 / 1.15)));
  render();
}, { passive: false });

let drag = null;
$('stage').addEventListener('mousedown', ev => {
  if (!V.fig) return;
  drag = { x: ev.clientX - V.tx, y: ev.clientY - V.ty };
  $('stage').classList.add('drag');
  ev.preventDefault();
});
window.addEventListener('mousemove', ev => {
  if (!drag) return;
  V.tx = ev.clientX - drag.x; V.ty = ev.clientY - drag.y;
  render();
});
window.addEventListener('mouseup', () => {
  drag = null; $('stage').classList.remove('drag');
});
$('stage').addEventListener('dblclick', fit);

// ── 彈出到第二個視窗 ────────────────────────────────────────────────────────
// file:// 的每個檔案是各自獨立的 opaque origin，跨視窗讀 DOM 會被擋。
// 但「設定 location」是少數跨源仍允許的操作，所以改用 URL 片段傳圖：
// 只換 # 後面不會重新載入，彈出視窗自己聽 hashchange 就好。
function pushPop() {
  if (!V.pop || V.pop.closed || !V.fig) return;
  try {
    V.pop.location.href = '_intake_viewer.html#'
      + encodeURIComponent(V.fig.dataset.src) + '|' + degOf(V.fig);
  } catch (e) { /* 視窗被關掉的競態，忽略 */ }
}
function popout() {
  if (V.pop && !V.pop.closed) { V.pop.focus(); pushPop(); return; }
  V.pop = window.open('_intake_viewer.html', 'intake_viewer',
                      'width=1100,height=900');
  if (!V.pop) { alert('瀏覽器擋掉了彈出視窗，請允許此頁開新視窗。'); return; }
  document.body.classList.add('popped');
  setTimeout(pushPop, 400);     // 等新視窗把 hashchange 監聽掛好
  const t = setInterval(() => {
    if (V.pop && V.pop.closed) {
      clearInterval(t);
      document.body.classList.remove('popped');
      fit();
    }
  }, 800);
}

// ── 檢查進度 ────────────────────────────────────────────────────────────────
function upd() {
  const cards = document.querySelectorAll('.card');
  let n = 0;
  cards.forEach(c => {
    const on = c.querySelector('[data-k=reviewed]').checked;
    c.classList.toggle('done', on);
    if (on) n++;
  });
  document.getElementById('prog').textContent =
    '已檢查 ' + n + ' / ' + cards.length;
}
function toggleHide() {
  document.body.classList.toggle('hideDone', document.getElementById('hide').checked);
}

// ── 自動暫存 ────────────────────────────────────────────────────────────────
// 存的是**原始欄位值**，不是 collect() 的結果——collect 會把 lines/csv/num
// 轉過型，還原時轉不回去（"1、2" 與 "1,2" 匯出後同形，但使用者打的不同）。
// 所以逐欄位存字串，還原就是原樣貼回去，不經過任何轉換。
const LS_KEY = 'intake_ws_v1';

function snapshot() {
  const all = {};
  document.querySelectorAll('.card').forEach(card => {
    const c = { desc: card.querySelector('[data-k=desc]').value,
                rev: card.querySelector('[data-k=reviewed]').checked,
                rot: rotsOf(card),
                pkg: card.querySelector('[data-k=pkg_shape]').value,
                ref: card.querySelector('[data-k=reflect]').value,
                exc: card.querySelector('[data-k=exclude_reason]').value,
                diff: [...card.querySelectorAll('input.diff:checked')].map(b => b.value),
                gt: {} };
    card.querySelectorAll('[data-gt]').forEach(el => { c.gt[el.dataset.gt] = el.value; });
    all[card.dataset.cid] = c;
  });
  return all;
}

function save() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({t: Date.now(), d: snapshot()}));
    const d = new Date();
    document.getElementById('auto').textContent =
      '已暫存 ' + d.toTimeString().slice(0, 8);
  } catch (e) {
    document.getElementById('auto').textContent = '暫存失敗：' + e.name;
  }
}

function restore() {
  let raw;
  try { raw = localStorage.getItem(LS_KEY); } catch (e) { return; }
  if (!raw) return;
  let box;
  try { box = JSON.parse(raw); } catch (e) { return; }
  const all = box.d || {};
  let n = 0;
  document.querySelectorAll('.card').forEach(card => {
    const c = all[card.dataset.cid];
    if (!c) return;
    const de = card.querySelector('[data-k=desc]');
    if (c.desc) { de.value = c.desc; n++; }
    // 打勾直接覆蓋（不像文字欄只補空白）：暫存一定比 intake.json 新，
    // 若只補不清，取消掉的勾會被舊值救回來，那一案就再也不會被檢查。
    if ('rev' in c) card.querySelector('[data-k=reviewed]').checked = !!c.rev;
    if (c.pkg) { card.querySelector('[data-k=pkg_shape]').value = c.pkg; n++; }
    if (c.ref) { card.querySelector('[data-k=reflect]').value = c.ref; n++; }
    if (c.exc) { card.querySelector('[data-k=exclude_reason]').value = c.exc; n++; }
    // 用比對而不是屬性選擇器：檔名可能有中文、空白、括號，逃逸規則太容易踩雷
    const rots = c.rot || {};
    card.querySelectorAll('.im').forEach(fig => {
      const r = rots[fig.dataset.f];
      if (r) { fig.dataset.rot = r; applyRot(fig); n++; }
    });
    (c.diff || []).forEach(v => {
      const b = card.querySelector('input.diff[value="' + v + '"]');
      if (b) { b.checked = true; n++; }
    });
    Object.entries(c.gt || {}).forEach(([k, v]) => {
      if (v === '') return;
      const el = card.querySelector('[data-gt="' + k + '"]');
      if (el) { el.value = v; n++; }
    });
  });
  if (n) {
    const d = new Date(box.t);
    document.getElementById('auto').textContent =
      '已還原 ' + n + ' 格（暫存於 ' + d.toLocaleString() + '）';
  }
}

function clearSaved() {
  if (!confirm('清除瀏覽器暫存？頁面上已填的內容不會被清掉，但重整後就沒了。')) return;
  try { localStorage.removeItem(LS_KEY); } catch (e) {}
  document.getElementById('auto').textContent = '暫存已清除';
}

let timer = null;
document.addEventListener('input', () => {
  clearTimeout(timer); timer = setTimeout(save, 600);
});
// upd() 要立刻跑（打勾的顏色與計數是即時回饋），但 save() 會讀三千多個欄位
// 再寫一份 150 KB 進 localStorage，沒必要每點一下就同步做一次
document.addEventListener('change', () => {
  upd();
  clearTimeout(timer); timer = setTimeout(save, 300);
  lag();
});

// 卡頓量測：無頭瀏覽器重現不出使用者回報的延遲，所以直接在真機上量。
// 從事件到畫面實際更新（連兩個 rAF）超過 80 ms 才顯示，平常不礙眼。
function lag() {
  const t0 = performance.now();
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const ms = Math.round(performance.now() - t0);
    const el = document.getElementById('lag');
    el.textContent = ms > 80 ? '慢：' + ms + ' ms' : '';
    el.title = '點擊到畫面更新 ' + ms + ' ms';
  }));
}
window.addEventListener('DOMContentLoaded', () => {
  restore(); upd();
  document.querySelectorAll('.im').forEach(applyRot);   // intake.json 預填的角度
});
// 視窗變寬變窄時 stage 尺寸跟著變，轉過的大圖縮放比例要重算
let rt = null;
window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(fit, 150); });
</script>"""
    with open(OUT_HTML, 'w', encoding='utf-8') as f:
        f.write(page.replace('__CARDS__', '\n'.join(cards)))
    _write_viewer()
    n_food = sum(1 for cat, _, _ in cases if cat != 'non_food')
    print(f"已產生 {os.path.basename(OUT_HTML)}"
          f"（{len(cases)} 案：食品 {n_food}、非食品 {len(cases) - n_food}）。")
    print(f"（同時更新 {os.path.basename(VIEWER_HTML)}：按「另開視窗」時用的大圖頁）")
    print("用瀏覽器開啟、看圖填寫後按「匯出」，再執行：")
    print("  python intake.py apply intake.json")


VIEWER_HTML = os.path.join(HERE, '_intake_viewer.html')

VIEWER_PAGE = """<!doctype html><meta charset="utf-8">
<title>大圖檢視</title>
<style>
  html, body { margin: 0; height: 100%; background: #222; overflow: hidden;
               font-family: system-ui, sans-serif; }
  #s { position: absolute; inset: 0 0 34px 0; overflow: hidden; cursor: grab; }
  #s.drag { cursor: grabbing; }
  #b { position: absolute; left: 50%; top: 50%; max-width: 100%; max-height: 100%;
       transform-origin: center center; will-change: transform; }
  #f { position: absolute; left: 0; right: 0; bottom: 0; height: 34px;
       background: #111; color: #bbb; font-size: .8em; line-height: 34px;
       padding: 0 12px; box-sizing: border-box; }
  #h { color: #888; text-align: center; padding-top: 40vh; }
</style>
<div id="s"><div id="h">在工作單點縮圖，大圖會出現在這裡</div></div>
<div id="f"><span id="n"></span> <span id="z"></span></div>
<script>
// 工作單那邊只能設定本視窗的 location（跨 file:// origin 唯一允許的操作），
// 所以圖片路徑與角度由 # 片段傳進來，這裡自己聽 hashchange。
var img = null, r = 0, z = 1, tx = 0, ty = 0, src = '';
var S = document.getElementById('s');

function render() {
  if (!img) return;
  img.style.transform = 'translate(-50%,-50%) translate(' + tx + 'px,' + ty
    + 'px) scale(' + z + ') rotate(' + r + 'deg)';
  document.getElementById('z').textContent = Math.round(z * 100) + '%  '
    + (r ? r + '\\u00b0' : '');
}
function fit() {
  if (!img) return;
  tx = ty = 0;
  var w = img.clientWidth, h = img.clientHeight;
  z = (r % 180 && w && h) ? Math.min(1, S.clientWidth / h, S.clientHeight / w) : 1;
  render();
}
function load() {
  var p = decodeURIComponent(location.hash.slice(1)).split('|');
  if (!p[0]) return;
  r = ((+p[1] || 0) % 360 + 360) % 360;
  if (!img) {
    img = document.createElement('img');
    img.id = 'b';
    img.addEventListener('load', fit);
    S.appendChild(img);
  }
  document.getElementById('h').style.display = 'none';
  document.getElementById('n').textContent = p[0].split('/').pop();
  if (p[0] !== src) { src = p[0]; img.src = p[0]; } else { fit(); }
}
window.addEventListener('hashchange', load);
window.addEventListener('resize', fit);
S.addEventListener('wheel', function (e) {
  if (!img) return;
  e.preventDefault();
  z = Math.min(12, Math.max(0.1, z * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
  render();
}, { passive: false });
var d = null;
S.addEventListener('mousedown', function (e) {
  if (!img) return;
  d = { x: e.clientX - tx, y: e.clientY - ty };
  S.classList.add('drag'); e.preventDefault();
});
window.addEventListener('mousemove', function (e) {
  if (!d) return;
  tx = e.clientX - d.x; ty = e.clientY - d.y; render();
});
window.addEventListener('mouseup', function () { d = null; S.classList.remove('drag'); });
S.addEventListener('dblclick', fit);
load();
</script>"""


def _write_viewer():
    """彈出視窗用的大圖頁。與工作單同目錄，圖片路徑才對得上。"""
    with open(VIEWER_HTML, 'w', encoding='utf-8') as f:
        f.write(VIEWER_PAGE)


# ─── apply ────────────────────────────────────────────────────────────────────

def _rotate_files(cdir, rots, cid):
    """把工作單上轉過向的照片實際寫回檔案。

    工作單的旋轉若只停在 CSS，人看到的是正的、辨識管線拿到的還是躺著的，
    正解與預測就對不起來——所以一定要落到像素。

    沿用原檔的量化表與色度取樣（`quality='keep'` 在旋轉後的影像上不能用，
    PIL 會擋「原圖不是 JPEG」），只重排係數，比預設重編碼失真小得多。
    存檔不帶 EXIF：方向已烙進像素，再留著方向旗標會被轉第二次。
    """
    from PIL import JpegImagePlugin
    for fn, deg in sorted(rots.items()):
        deg = int(deg) % 360
        if deg == 0:
            continue
        if deg % 90:
            sys.exit(f"[錯誤] {cid}/{fn}：旋轉角度 {deg} 不是 90 的倍數。")
        p = os.path.join(cdir, fn)
        if not os.path.exists(p):
            print(f"[警告] {cid}：rotate 指到不存在的 {fn}，略過。")
            continue
        with Image.open(p) as im:
            q, ss = im.quantization, JpegImagePlugin.get_sampling(im)
            # 先把 EXIF 方向烙進像素，才與瀏覽器上看到的那一版對齊；
            # PIL 讀圖不套 EXIF，瀏覽器會套，不先對齊就會差 90 度
            out = ImageOps.exif_transpose(im).rotate(-deg, expand=True)
            out.save(p, 'JPEG', qtables=q, subsampling=ss, optimize=True)
        print(f"        {fn} 已轉 {deg}°")



def apply(path, set_version):
    _check_skeleton_sync()
    entries = json.load(open(path, encoding='utf-8'))
    data = casetool.load_cases()
    registered = {c['case_id'] for c in data['cases']}
    inbox = {cid: (cat, files) for cat, cid, files in scan_inbox()}
    paired = casetool.paired_ids()

    # 全部驗證通過才動手——搬檔＋登錄＋寫正解跨三處，做一半最難收拾
    errors, warns = [], []
    for cid, e in entries.items():
        cat = e.get('category')
        if cid not in inbox:
            errors.append(f"{cid}：_上傳區 找不到（已套用過？）")
            continue
        if inbox[cid][0] != cat:
            errors.append(f"{cid}：intake.json 記 {cat}，資料夾在 {inbox[cid][0]}")
        if any(not f.lower().endswith('.jpg') for f in inbox[cid][1]):
            errors.append(f"{cid}：仍有非 JPG 檔，請先跑 python intake.py convert")
        if cid in registered:
            errors.append(f"{cid}：已存在於 cases.json")
        if os.path.exists(casetool.gt_path(cid, cat)):
            errors.append(f"{cid}：ground_truth/{cat}/{cid}.json 已存在")
        bad = set(e.get('difficulty') or []) - casetool.VALID_DIFFICULTY
        if bad:
            errors.append(f"{cid}：未知 difficulty {sorted(bad)}")
        er = e.get('exclude_reason')
        if er and er not in casetool.VALID_EXCLUDE_REASON:
            errors.append(f"{cid}：未知 exclude_reason '{er}'"
                          f"（可用：{sorted(casetool.VALID_EXCLUDE_REASON)}）")
        elif er:
            g = e.get('gt') or {}
            for fld in casetool.EXCLUDE_FIELDS_BY_REASON[er]:
                v = g.get(fld)
                if (any(x is not None for x in v.values())
                        if isinstance(v, dict) else v is not None):
                    errors.append(f"{cid}：宣告 {er} 但 {fld} 有填"
                                  f" → 正解不唯一時填了等於自己挑一個答案")
        for fld, vocab in (('pkg_shape', casetool.VALID_PKG_SHAPE),
                           ('reflect', casetool.VALID_REFLECT)):
            v = e.get(fld)
            if v and v not in vocab:
                errors.append(f"{cid}：未知 {fld} '{v}'（可用：{sorted(vocab)}）")
            elif not v and cid in paired:
                warns.append(f"{cid}：有高反光配對臂但沒填 {fld}"
                             f"（casetool.py check 會再提醒一次）")
        if cat != 'non_food' and not (e.get('gt') or {}).get('name'):
            warns.append(f"{cid}：name 空白（確定照片上真的沒有產品名？）")
    if errors:
        for m in errors:
            print(f"[錯誤] {m}")
        sys.exit("未套用任何變更。")
    for m in warns:
        print(f"[警告] {m}")
    unrev = [cid for cid, e in entries.items() if not e.get('reviewed')]
    if unrev:
        print(f"[警告] {len(unrev)} 案未勾「人工已檢查」，會記為 gt_source=unknown："
              f"{'、'.join(unrev[:8])}{' …' if len(unrev) > 8 else ''}")
        print("        補檢查後重跑 generate → 打勾 → 匯出，或事後改 cases.json。")

    # 搬檔與寫正解逐案進行、cases.json 卻在迴圈之後才存檔——中途炸掉就變成
    # 「照片已搬、正解已寫、cases.json 沒登記」的半套用狀態，只能手動搬回去。
    # 2026-09-06 因主控台編碼炸在第 11 案，踩過一次。改成中途失敗也先存檔，
    # 讓 cases.json 至少與實際檔案一致，並印出續做指引。
    done = []
    try:
        _apply_loop(entries, data, done, set_version)
    except BaseException:
        if done:
            data['cases'].sort(key=lambda c: c['case_id'])
            casetool.save_cases(data)
            print(f"\n[中斷] 已完成 {len(done)} 案並存檔，其餘仍在 _上傳區。")
            print("       修正原因後重跑同一道 apply 即可續做"
                  "（已完成者會被『已存在於 cases.json』擋下，屬預期）。")
        raise

    data['cases'].sort(key=lambda c: c['case_id'])
    casetool.save_cases(data)
    print(f"\n共 {len(entries)} 案，set_version={set_version}。收尾：")
    print("  python manifest_tool.py generate && python casetool.py check")


def _apply_loop(entries, data, done, set_version):
    for cid, e in entries.items():
        cat = e['category']
        os.makedirs(os.path.join(casetool.IMAGES, cat), exist_ok=True)
        shutil.move(os.path.join(INBOX, cat, cid),
                    os.path.join(casetool.IMAGES, cat, cid))
        _rotate_files(os.path.join(casetool.IMAGES, cat, cid),
                      e.get('rotate') or {}, cid)
        # 縮圖是工作單的暫存品，案子建完就沒用了，留著只會誤導下次 generate
        shutil.rmtree(os.path.join(THUMBS, cat, cid), ignore_errors=True)
        imgs = casetool.images_for(cid, cat)

        data['cases'].append({
            "case_id": cid,
            "desc": e.get('desc', ''),
            "barcode": "TEST",
            "images": imgs,
            "tags": {"is_food": cat != 'non_food'},
            "set_version": set_version,
            "category": cat,
            "difficulty": e.get('difficulty') or [],
            # 工作單的「人工已檢查」直接對到既有的 gt_source：沒看過的正解是
            # LLM 初稿，來源不明確，不能與手打的混在同一個切片裡比較。
            "gt_source": 'llm_checked' if e.get('reviewed') else 'unknown',
            # 空值不寫欄位：cases.json 裡出現 "" 會讓「沒填」與「填了空」分不開
            **{k: e[k] for k in ('pkg_shape', 'reflect', 'exclude_reason',
                                 'raw_issue') if e.get(k)},
        })

        gp = casetool.gt_path(cid, cat)
        os.makedirs(os.path.dirname(gp), exist_ok=True)
        if cat == 'non_food':
            gt = {"is_food_label": False}
        else:
            gt = copy.deepcopy(casetool.FOOD_SKELETON)
            for k, v in (e.get('gt') or {}).items():
                if k in gt and isinstance(gt[k], dict) and isinstance(v, dict):
                    gt[k].update({kk: vv for kk, vv in v.items() if kk in gt[k]})
                elif k in gt:
                    gt[k] = v
            gt['is_food_label'] = True
        with open(gp, 'w', encoding='utf-8') as f:
            json.dump(gt, f, ensure_ascii=False, indent=2)
        done.append(cid)
        print(f"已建檔 {cid}（{cat}，{len(imgs)} 張"
              + (f"，困難度 {'/'.join(e['difficulty'])}" if e.get('difficulty') else "")
              + "）")


def main():
    args = sys.argv[1:]
    if args[:1] == ['convert']:
        convert()
    elif args[:1] == ['group']:
        if len(args) == 1:
            group_generate()
        elif len(args) == 2:
            group_apply(args[1])
        else:
            sys.exit("用法：intake.py group [groups.json]")
    elif args[:1] == ['generate']:
        generate()
    elif args[:1] == ['apply'] and len(args) >= 2:
        sv = 'v3.0'
        for a in args[2:]:
            if a.startswith('--set-version='):
                sv = a.split('=', 1)[1]
            else:
                sys.exit(f"看不懂的參數：{a}")
        apply(args[1], sv)
    else:
        sys.exit("用法：intake.py convert | group [groups.json] | generate "
                 "| apply <intake.json> [--set-version=vX.Y]")


if __name__ == '__main__':
    main()
