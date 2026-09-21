#!/usr/bin/env python3
# 拍攝條件標註工作單：看圖打勾，不手改 JSON。
#
# 為何要有這支：difficulty 存於 cases.json，48 案徒手編輯 JSON 既無法邊看圖邊標，
# 也容易改錯案、拼錯標籤；casetool check 只能驗「拼字合法」，不能替人看圖。
# 此工具把「看圖判斷」與「寫入檔案」分開：
#   generate  產生可離線開啟的 HTML（圖片＋勾選欄），逐案勾完按「匯出」存 JSON
#             若同目錄有 labels.json（上次匯出的標註），自動預勾其內容，
#             重產工作單不必整批重看；沒有的案例才用 cases.json 的 difficulty
#   apply     把匯出的 JSON 套回 cases.json（驗證標籤與案例，僅動 difficulty 欄）
# 套用後仍以 casetool.py check 收尾，維持既有的檢查流程。
#
# 標註「不」調測試集版號：difficulty 是切片用的中繼資料，不影響任何案例的分數，
# 亦非照片或正解的修訂（版號規則見 README）。標註完成一事記入 README 版本沿革
# 的說明文字即可。
#
# 判斷基準（與 ground truth 同一種紀律——描述照片上看得到什麼，不是主觀難不難）：
#   - 只算影響到「標示區域」的問題：背景反光不算，成分欄上的反光斑才算
#   - 明顯才標，邊緣狀況不標——模稜兩可的標籤會把切片稀釋成雜訊
#   - 一案多張圖時，標的是「這一案送給模型的照片整體」有沒有該問題
#
# 用法：
#   python label_difficulty.py generate          # 產生 _difficulty_worksheet.html
#   python label_difficulty.py apply labels.json # 套回 cases.json
import html
import json
import os
import sys

import casetool

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「塩」「菓」…）。
# 沒有這兩行的後果不是印出亂碼，而是**整批任務在中途拋 UnicodeEncodeError 死掉**
# ——2026-09-07 run_eval 炸在第 10 案的「塩」、intake 炸在第 11 案的「菓」，
# 後者還留下半套用狀態（照片已搬、cases.json 沒存）。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, 'cases.json')
LABELS = os.path.join(HERE, 'labels.json')
OUT_HTML = os.path.join(HERE, '_difficulty_worksheet.html')

# 與 casetool.py 的 VALID_DIFFICULTY 一致；中文僅供顯示，存檔一律英文標籤。
# 依 casetool.DIFFICULTY_FAMILY 分兩族顯示——分族的判準是「重拍能不能改善」，
# 標註時看得到這個問題，比事後回想更容易標得一致。
TAGS = [('blurry', '模糊'), ('glare', '反光'),
        ('curved', '曲面'), ('crease', '摺痕')]
FAMILY_ZH = {'shot': '重拍可改善', 'pkg': '包裝自帶，重拍無效'}


def generate():
    cases = json.load(open(CASES, encoding='utf-8'))['cases']

    # 歷史標註優先序：labels.json（已匯出、可能尚未 apply）＞ cases.json 的
    # difficulty。瀏覽器不讓 file:// 開啟的 HTML 讀本機檔案，所以歷史標註
    # 只能在產生 HTML 時預勾進去；沒有這一步，重產工作單就得整批重看。
    prior = {}
    if os.path.exists(LABELS):
        prior = json.load(open(LABELS, encoding='utf-8'))
        n = sum(1 for ts in prior.values() if ts)
        print(f"已載入 labels.json 的歷史標註（{len(prior)} 案，{n} 案帶標籤）。")

    cards = []
    for c in cases:
        cid = c['case_id']
        checked = prior[cid] if cid in prior else (c.get('difficulty') or [])
        imgs = ''.join(
            f'<img src="{html.escape(p)}" loading="lazy">' for p in c['images'])
        boxes = ''
        for fam in ('shot', 'pkg'):
            items = ''.join(
                f'<label><input type="checkbox" data-cid="{html.escape(cid)}" '
                f'value="{en}"{" checked" if en in checked else ""}>'
                f'{zh}<span class="en">{en}</span></label>'
                for en, zh in TAGS if casetool.DIFFICULTY_FAMILY[en] == fam)
            boxes += (f'<div class="fam"><span class="famname">{FAMILY_ZH[fam]}</span>'
                      f'{items}</div>')
        cards.append(
            f'<div class="card"><h3>{html.escape(cid)}'
            f'<span class="cat">{html.escape(c.get("category") or "")}</span></h3>'
            f'<p class="desc">{html.escape(c.get("desc") or "")}</p>'
            f'<div class="imgs">{imgs}</div><div class="boxes">{boxes}</div></div>')

    page = """<!doctype html><meta charset="utf-8">
<title>拍攝條件標註工作單</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 20px auto; max-width: 900px; }
  .hint { background: #fff6d6; border: 1px solid #e0c96a; padding: 10px 14px;
          border-radius: 8px; line-height: 1.6; }
  .card { border: 1px solid #ccc; border-radius: 10px; padding: 12px 16px; margin: 14px 0; }
  .card h3 { margin: 0 0 2px; }
  .cat { font-size: .75em; color: #666; margin-left: 8px; font-weight: normal; }
  .desc { color: #555; margin: 2px 0 8px; }
  .imgs img { max-width: 100%; max-height: 480px; display: block; margin: 6px 0; }
  .boxes label { display: inline-block; margin: 4px 12px 4px 0; cursor: pointer; }
  .en { color: #999; font-size: .75em; margin-left: 4px; }
  .fam { margin: 4px 0; }
  .famname { display: inline-block; min-width: 150px; color: #777; font-size: .8em; }
  #bar { position: sticky; bottom: 0; background: #f4f4f4; padding: 10px;
         border-top: 2px solid #bbb; text-align: center; }
  button { font-size: 1em; padding: 8px 18px; cursor: pointer; }
</style>
<div class="hint">
  <b>判斷基準</b>：只算影響到<b>標示區域</b>的問題（背景反光不算，成分欄上的反光斑才算）；
  明顯才標，邊緣狀況不標；沒有問題的案例什麼都不勾——「全部看完」後匯出，
  未勾即代表「看過了，是乾淨的照片」。
</div>
__CARDS__
<div id="bar">
  <button onclick="exp()">匯出 labels.json</button>
  <span id="msg"></span>
</div>
<script>
function collect() {
  const out = {};
  document.querySelectorAll('.card').forEach(card => {
    const cid = card.querySelector('input').dataset.cid;
    out[cid] = [...card.querySelectorAll('input:checked')].map(b => b.value);
  });
  return out;
}
function exp() {
  const blob = new Blob([JSON.stringify(collect(), null, 2)],
                        {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'labels.json';
  a.click();
  document.getElementById('msg').textContent =
    ' 已下載。接著：python label_difficulty.py apply labels.json';
}
</script>"""
    with open(OUT_HTML, 'w', encoding='utf-8') as f:
        f.write(page.replace('__CARDS__', '\n'.join(cards)))
    print(f"已產生 {os.path.basename(OUT_HTML)}（{len(cards)} 案）。")
    print("用瀏覽器開啟、逐案勾選後按「匯出」，再執行：")
    print("  python label_difficulty.py apply labels.json")


def apply(path):
    labels = json.load(open(path, encoding='utf-8'))
    data = json.load(open(CASES, encoding='utf-8'))
    by_id = {c['case_id']: c for c in data['cases']}
    valid = {en for en, _ in TAGS}

    unknown_case = [cid for cid in labels if cid not in by_id]
    unknown_tag = [(cid, t) for cid, ts in labels.items()
                   for t in ts if t not in valid]
    if unknown_case or unknown_tag:
        for cid in unknown_case:
            print(f"[錯誤] 標註檔有 cases.json 沒有的案例：{cid}")
        for cid, t in unknown_tag:
            print(f"[錯誤] {cid} 含未知標籤：{t}")
        sys.exit("未套用任何變更。")

    changed = 0
    for cid, tags in labels.items():
        if (by_id[cid].get('difficulty') or []) != tags:
            by_id[cid]['difficulty'] = tags
            changed += 1
    if not changed:
        print("與現況完全相同，未改寫 cases.json。")
        return
    with open(CASES, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')

    n_tagged = sum(1 for ts in labels.values() if ts)
    print(f"已套用：{len(labels)} 案中 {changed} 案有變更，{n_tagged} 案帶標籤。")
    print("收尾檢查：python casetool.py check")


def main():
    args = sys.argv[1:]
    if args[:1] == ['generate']:
        generate()
    elif args[:1] == ['apply'] and len(args) == 2:
        apply(args[1])
    else:
        sys.exit("用法：label_difficulty.py generate | apply <labels.json>")


if __name__ == '__main__':
    main()
