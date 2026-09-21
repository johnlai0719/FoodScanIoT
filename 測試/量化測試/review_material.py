#!/usr/bin/env python3
# 包裝形狀／反光材質的複查頁。
#
# 為何獨立成一支：這兩欄在進件工作單填過一輪，但填的時候要同時顧 22 個正解欄位，
# 材質很容易隨手選。而它們是教授建議 13 要的「證明選品涵蓋不同物理特性」，
# 分佈明顯不合理會在報告裡被追問——2026-09-06 實測分佈：零食 44 案 foil 0 個、
# 飲料 41 案 bottle 只有 2 個、杯麵 clear_film 13 個而 shrink_wrap 0 個。
#
# **預設只列配對案例**。教授那條講的是配對組，`casetool.py check` 也只對配對案例
# 報缺；其餘 144 案可以永遠留空，不必為了整齊去填。
#
# ⚠ 這兩欄**不進任何數字**（見 casetool.VALID_PKG_SHAPE 的註解）。填錯個別案例
# 不污染分數，只有整體分佈不像真的才有問題。所以複查看的是「這批東西合不合理」，
# 不是逐案錙銖必較。
#
# 用法：
#   python review_material.py              # 產生 _material_worksheet.html（配對案例）
#   python review_material.py --all        # 全部 177 案
#   python review_material.py apply material.json
import html
import io
import json
import os
import sys

from PIL import Image, ImageOps

import casetool

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_HTML = os.path.join(HERE, '_material_worksheet.html')
# 「照片上分不出、要拿實物看一眼」的清單。純粹是複查動線用的便條，
# 不進 cases.json——那裡只該放結論，不放待辦。
TODO_JSON = os.path.join(HERE, '_material_todo.json')
THUMBS = os.path.join(HERE, '_thumbs_mat')
THUMB_PX = 420

PKG = {'carton': '平面紙盒', 'can': '圓柱罐', 'pouch': '軟袋', 'bottle': '曲面瓶',
       'shrink_wrap': '收縮膜', 'tray': '塑膠盒', 'cup': '杯裝'}
REF = {'foil': '鋁箔鍍膜', 'glossy_plastic': '亮面塑膠',
       'matte_paper': '霧面紙', 'clear_film': '透明膜',
       'metal': '金屬罐', 'glass': '玻璃'}

# 少一個值，該值的案例在下拉裡會變成空白，匯出時就被清掉——2026-09-06 補
# metal/glass 時漏了這裡，11 個鋁罐玻璃罐差點被歸零。啟動就擋。
assert set(PKG) == casetool.VALID_PKG_SHAPE,     '中文對照與 VALID_PKG_SHAPE 不同步：%s' % (set(PKG) ^ casetool.VALID_PKG_SHAPE)
assert set(REF) == casetool.VALID_REFLECT,     '中文對照與 VALID_REFLECT 不同步：%s' % (set(REF) ^ casetool.VALID_REFLECT)


def _thumb(rel):
    """縮圖檔。原圖是 4000px 級的手機照片，直接塞進 <img> 會讓瀏覽器
    為了畫一個 200px 的框而解碼整張——見 intake._build_thumbs 的說明。"""
    src = os.path.join(casetool.IMAGES, rel[len('images/'):])
    dst = os.path.join(THUMBS, rel[len('images/'):])
    if not os.path.exists(src):
        return None
    if not (os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src)):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((THUMB_PX, THUMB_PX))
            im.convert('RGB').save(dst, 'JPEG', quality=80)
    return '_thumbs_mat/' + rel[len('images/'):].replace(os.sep, '/')


def _sel(key, cur, vocab):
    o = '<option value="">—</option>' + ''.join(
        '<option value="%s"%s>%s</option>' % (k, ' selected' if cur == k else '', v)
        for k, v in vocab.items())
    return '<select data-k="%s">%s</select>' % (key, o)


def generate(only_paired=True):
    cases = casetool.load_cases()['cases']
    paired = casetool.paired_ids()
    rows = [c for c in cases if c['category'] != 'non_food'
            and (not only_paired or c['case_id'] in paired)]
    todo = {}
    if os.path.exists(TODO_JSON):
        todo = json.load(io.open(TODO_JSON, encoding='utf-8'))
    # 待確認的排前面：31 案裡只有 11 案要動，讓它們不必用找的
    rows.sort(key=lambda c: (c['case_id'] not in todo, c['case_id']))
    cards = []
    for c in rows:
        cid = c['case_id']
        imgs = ''.join(
            '<img src="%s" loading="lazy">' % html.escape(t)
            for t in filter(None, (_thumb(r) for r in c.get('images') or [])))
        tag = '<b class="p">配對案例</b>' if cid in paired else ''
        ask = todo.get(cid)
        note = '<div class="q">要確認：%s</div>' % html.escape(ask) if ask else ''
        cards.append(
            '<div class="card%s" data-cid="%s" data-todo="%d">'
            '<h3>%s<span class="c">%s</span>%s</h3>'
            '%s<div class="im">%s</div><div class="s">包裝形狀 %s　反光材質 %s</div></div>'
            % (' todo' if ask else '', html.escape(cid), 1 if ask else 0,
               html.escape(cid), html.escape(c['category']), tag, note,
               imgs, _sel('pkg_shape', c.get('pkg_shape'), PKG),
               _sel('reflect', c.get('reflect'), REF)))

    page = """<!doctype html><meta charset="utf-8"><title>包裝材質複查</title>
<style>
 body{font-family:system-ui,sans-serif;margin:0;padding:16px 16px 70px;}
 .hint{background:#fff6d6;border:1px solid #e0c96a;padding:10px 14px;border-radius:8px;
       line-height:1.7;max-width:1100px;margin:0 auto 14px;}
 .grid{display:flex;flex-wrap:wrap;gap:12px;max-width:1100px;margin:0 auto;}
 .card{border:2px solid #ccc;border-radius:10px;padding:10px;width:340px;
       content-visibility:auto;contain-intrinsic-size:auto 420px;}
 .card.ok{border-color:#2a7;background:#f5fbf7;}
 .card.todo{border-color:#e08a00;background:#fffaf0;}
 .q{color:#a05000;font-size:.82em;margin:2px 0 6px;line-height:1.4;}
 body.onlyTodo .card:not(.todo){display:none;}
 .card h3{margin:0 0 6px;font-size:.9em;}
 .c{color:#666;font-weight:400;margin-left:6px;font-size:.85em;}
 .p{color:#c60;font-size:.8em;margin-left:6px;}
 .im{display:flex;gap:6px;flex-wrap:wrap;}
 .im img{width:150px;height:150px;object-fit:contain;background:#eee;
         border:1px solid #ddd;border-radius:6px;cursor:zoom-in;}
 .s{margin-top:8px;font-size:.85em;font-weight:600;}
 select{font:inherit;font-weight:400;padding:3px 5px;}
 #bar{position:fixed;left:0;right:0;bottom:0;background:#f4f4f4;padding:10px;
      border-top:2px solid #bbb;text-align:center;}
 #big{position:fixed;inset:0;background:rgba(0,0,0,.9);display:none;z-index:9;
      align-items:center;justify-content:center;cursor:zoom-out;}
 #big img{max-width:96vw;max-height:96vh;}
</style>
<div class="hint">
 <b>reflect 看最外層、pkg_shape 看容器。</b>反光產生在最外面那一層——杯麵外面
 套印刷收縮膜就記那層膜；但形狀是幾何，套了膜的杯麵仍是「杯裝」。
 <code>透明膜</code>指的是<b>最外層是透明的</b>，不是「看得到食物」。<br>
 照片上分不出的兩組，要拿實物看：<b>紙罐 vs 金屬罐</b>看罐底有沒有金屬捲邊；
 <b>鋁箔 vs 亮面塑膠</b>看撕口斷面或內層是不是銀色。<br>
 這兩欄<b>不進任何數字</b>，填錯個別案例不影響分數，要看的是整批合不合理。
 點縮圖可放大。
</div>
<div class="grid">__CARDS__</div>
<div id="big" onclick="this.style.display='none'"><img></div>
<div id="bar"><span id="p"></span>　
 <label style="margin-right:12px;cursor:pointer">
  <input type="checkbox" id="ot" onchange="document.body.classList.toggle('onlyTodo',this.checked)">
  只顯示要確認的</label>
 <button onclick="exp()">匯出 material.json</button>
 <span id="m"></span></div>
<script>
// 鍵帶版本：cases.json 的值換過一輪，舊暫存會把新值蓋回去
const KEY='material_ws_v2';
function upd(){let n=0;document.querySelectorAll('.card').forEach(c=>{
  const a=c.querySelector('[data-k=pkg_shape]').value,b=c.querySelector('[data-k=reflect]').value;
  const ok=a&&b;c.classList.toggle('ok',ok);if(ok)n++;});
  const t=document.querySelectorAll('.card.todo').length;
  document.getElementById('p').textContent='兩欄都填了 '+n+' / '
    +document.querySelectorAll('.card').length+(t?'　待確認 '+t+' 案':'');}
function collect(){const o={};document.querySelectorAll('.card').forEach(c=>{
  const a=c.querySelector('[data-k=pkg_shape]').value,b=c.querySelector('[data-k=reflect]').value;
  if(a||b)o[c.dataset.cid]={pkg_shape:a,reflect:b};});return o;}
function exp(){const b=new Blob([JSON.stringify(collect(),null,2)],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='material.json';a.click();
  document.getElementById('m').textContent=' 已下載 → python review_material.py apply material.json';save();}
function save(){try{localStorage.setItem(KEY,JSON.stringify(collect()));}catch(e){}}
function restore(){let r;try{r=localStorage.getItem(KEY);}catch(e){return;}if(!r)return;
  const d=JSON.parse(r);document.querySelectorAll('.card').forEach(c=>{const v=d[c.dataset.cid];
   if(!v)return;if(v.pkg_shape)c.querySelector('[data-k=pkg_shape]').value=v.pkg_shape;
   if(v.reflect)c.querySelector('[data-k=reflect]').value=v.reflect;});}
document.addEventListener('change',()=>{upd();save();});
document.addEventListener('click',e=>{if(e.target.tagName==='IMG'&&e.target.closest('.im')){
  const b=document.getElementById('big');b.querySelector('img').src=e.target.src;b.style.display='flex';}});
window.addEventListener('DOMContentLoaded',()=>{restore();upd();});
</script>"""
    io.open(OUT_HTML, 'w', encoding='utf-8').write(page.replace('__CARDS__', '\n'.join(cards)))
    print('已產生 %s（%d 案%s）' % (os.path.basename(OUT_HTML), len(rows),
                                 '，只列配對案例' if only_paired else ''))
    print('瀏覽器開啟、改完按「匯出」，再執行：')
    print('  python review_material.py apply material.json')


def apply_(path):
    d = json.load(io.open(path, encoding='utf-8'))
    data = casetool.load_cases()
    idx = {c['case_id']: c for c in data['cases']}
    bad = [(cid, v) for cid, v in d.items()
           if (v.get('pkg_shape') and v['pkg_shape'] not in casetool.VALID_PKG_SHAPE)
           or (v.get('reflect') and v['reflect'] not in casetool.VALID_REFLECT)]
    if bad:
        for cid, v in bad:
            print('[錯誤] %s：%s' % (cid, v))
        sys.exit('未套用任何變更。')
    n = 0
    for cid, v in d.items():
        c = idx.get(cid)
        if not c:
            print('[略過] %s 不在 cases.json' % cid)
            continue
        for k in ('pkg_shape', 'reflect'):
            if v.get(k) and c.get(k) != v[k]:
                print('%-40s %s: %s → %s' % (cid[:38], k, c.get(k) or '－', v[k]))
                c[k] = v[k]
                n += 1
    casetool.save_cases(data)
    print('\n共更新 %d 格。收尾：python casetool.py check' % n)


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['apply'] and len(a) == 2:
        apply_(a[1])
    else:
        generate(only_paired='--all' not in a)
