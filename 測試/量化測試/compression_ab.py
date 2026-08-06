#!/usr/bin/env python3
# 壓縮前後的 A/B 量測：同一批案例、同一支管線，只換輸入圖片。
#
# 回答的是 compress_images.py 檔頭所說的 Q1——「縮到 1280、品質 0.8 會不會讓
# 標示上的小字讀不出來」。Q2（組員的 App 實作有沒有照這組參數壓）不在此範圍。
#
# 為什麼需要這支，而不是手動跑兩次 run_eval：
#
# 1. **一遍分不出訊號與雜訊。** 同一張圖重跑，成分 F1 實測可差 0.29。跑一次
#    原圖、跑一次壓縮，得到的差值裡有多少是壓縮造成的、有多少是模型執行間的
#    抖動，無從分辨。本工具整批跑 N 遍，每遍各自評分並快照，最後給的是
#    每個條件的區間；兩個區間若重疊，就不能宣稱壓縮造成差異。
#
# 2. **score_eval.py 吃的是 predictions/，而 run_eval.py --repeat=N 只把最後
#    一次寫進去。** 所以 --repeat 能穩定延遲與 token，對辨識分數的雜訊毫無
#    幫助——要重複的是「跑＋評分」整組，不是只有跑。
#
# 3. **兩個條件要交錯跑。** 先把原圖 3 遍跑完再跑壓縮 3 遍的話，中間若模型
#    端有任何變動，整段差異會被誤記到壓縮頭上。本工具每一遍內先原圖後壓縮。
#
# predictions/ 與 results/ 會在過程中被覆寫 6 次。predictions/ 進版控，跑完
# 用 `git checkout -- predictions/` 復原即可；每一遍的產物都已快照到 out/。
#
# 用法：
#   python compression_ab.py run                    # 3 遍 × 2 條件，約 55 分鐘
#   python compression_ab.py run --passes=1
#   python compression_ab.py report                 # 只重新彙整既有快照，不打 API
import csv
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, 'results')
PRED = os.path.join(HERE, 'predictions')
PY = sys.executable

# (代號, EVAL_IMAGE_ROOT)。None = 用原圖，也就是 run_eval 的預設。
CONDITIONS = [('orig', None), ('comp', 'images_1280q80')]
SNAP_FILES = ['summary.json', 'per_case.csv', 'mismatches.csv',
              'latency_vision.json', 'samples.jsonl']


# ─── 跑 ───────────────────────────────────────────────────────────────────────

def one_pass(out_root, cond, image_root, k):
    dst = os.path.join(out_root, cond, f'run{k}')
    if os.path.exists(os.path.join(dst, 'summary.json')):
        print(f"[跳過] {cond}/run{k} 已有快照")
        return True
    os.makedirs(dst, exist_ok=True)

    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    env.pop('EVAL_IMAGE_ROOT', None)
    if image_root:
        env['EVAL_IMAGE_ROOT'] = image_root
    # thinking 的實驗開關若殘留在環境裡，兩個條件會在不同設定下量測。明確清掉。
    env.pop('EVAL_THINKING_BUDGET', None)

    print(f"\n=== {cond} / run{k} ===  {datetime.now():%H:%M:%S}"
          f"  image_root={image_root or 'images/（原圖）'}")
    log = os.path.join(dst, 'run_eval.log')
    with open(log, 'w', encoding='utf-8') as f:
        p = subprocess.run([PY, 'run_eval.py', f'--tag={cond}_run{k}'],
                           cwd=HERE, env=env, stdout=f,
                           stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        print(f"[失敗] run_eval 退出碼 {p.returncode}，見 {log}")
        return False

    lat = json.load(open(os.path.join(RES, 'latency_vision.json'), encoding='utf-8'))
    # 案例母體不同就不是同一把尺。找不到圖被 SKIP 時 run_eval 仍以 0 退出，
    # 不在此擋下的話，會拿 45 案的分數去跟 48 案比。
    if lat.get('skipped_cases'):
        print(f"[中止] 有案例被略過（找不到圖片）：{lat['skipped_cases']}")
        return False
    if lat.get('n_cases') != 48:
        print(f"[中止] 只跑到 {lat.get('n_cases')} 案，預期 48")
        return False
    if lat.get('n_failed'):
        print(f"[注意] {lat['n_failed']} 次呼叫失敗，該案會以空預測計分")

    subprocess.run([PY, 'score_eval.py'], cwd=HERE, env=env,
                   stdout=subprocess.DEVNULL, check=True)

    for fn in SNAP_FILES:
        src = os.path.join(RES, fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst, fn))
    shutil.copytree(PRED, os.path.join(dst, 'predictions'), dirs_exist_ok=True)

    s = json.load(open(os.path.join(dst, 'summary.json'), encoding='utf-8'))
    print(f"  ingredients_flat F1={s['ingredients_flat']['f1']}  "
          f"延遲 p50={lat['latency_ms']['p50']:.0f}ms  "
          f"送出 avg={lat['payload_raw_bytes']['avg']/1024:.0f}KB")
    return True


def cmd_run(out_root, passes):
    for k in range(1, passes + 1):
        for cond, root in CONDITIONS:
            if not one_pass(out_root, cond, root, k):
                sys.exit(1)
    print("\n全部跑完。predictions/ 已被覆寫，用 git checkout -- predictions/ 復原。")


# ─── 彙整 ─────────────────────────────────────────────────────────────────────

def load_runs(out_root, cond):
    runs = []
    d = os.path.join(out_root, cond)
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        p = os.path.join(d, name, 'summary.json')
        if os.path.exists(p):
            runs.append({
                'name': name,
                'summary': json.load(open(p, encoding='utf-8')),
                'latency': json.load(open(os.path.join(d, name, 'latency_vision.json'),
                                          encoding='utf-8')),
                'per_case': list(csv.DictReader(
                    open(os.path.join(d, name, 'per_case.csv'), encoding='utf-8-sig'))),
            })
    return runs


def rng(vals, nd=3):
    """區間字串。判斷差異是否為雜訊，看的是區間有沒有重疊，不是平均值差多少。"""
    if not vals:
        return '—'
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return f"{lo:.{nd}f}"
    return f"{lo:.{nd}f}–{hi:.{nd}f}"


def overlap(a, b):
    return not (max(a) < min(b) or max(b) < min(a))


def correct_counts(runs):
    """每一遍、每個欄位答對幾案。0/1 型的指標才算得出筆數；F1 之類另外處理。"""
    out = defaultdict(list)   # field -> [每遍的 (答對, 總數)]
    for r in runs:
        agg = defaultdict(lambda: [0, 0])
        for row in r['per_case']:
            if row['metric'] not in ('correct', 'exact', 'within_tol'):
                continue
            agg[row['field']][1] += 1
            agg[row['field']][0] += 1 if float(row['value']) >= 1 else 0
        for f, (ok, n) in agg.items():
            out[f].append((ok, n))
    return out


def case_values(runs, field, metric):
    """case_id -> [每遍的值]"""
    out = defaultdict(list)
    for r in runs:
        for row in r['per_case']:
            if row['field'] == field and row['metric'] == metric:
                out[row['case_id']].append(float(row['value']))
    return out


def cmd_report(out_root):
    data = {c: load_runs(out_root, c) for c, _ in CONDITIONS}
    if not all(data.values()):
        sys.exit(f"{out_root} 內找不到兩個條件的快照，先跑 run。")
    n = min(len(v) for v in data.values())
    L = []

    def w(s=''):
        L.append(s)

    comp_report = os.path.join(HERE, 'images_1280q80', '_compression_report.json')
    cr = json.load(open(comp_report, encoding='utf-8')) if os.path.exists(comp_report) else {}

    px, q = cr.get('max_width', 1280), int(cr.get('quality', 0.8) * 100)
    w(f"# 影像壓縮 A/B：原圖 vs {px}px / q{q}")
    w()
    w(f"產生於 {datetime.now():%Y-%m-%d %H:%M}　每個條件各 {n} 遍，交錯執行。")
    w("測試集 v2.1（48 案 / 54 張圖）。同一批案例、同一支管線，只換輸入圖片。")
    w()
    w("**區間讀法**：每個數字是 N 遍的最小–最大值。兩個條件的區間重疊，")
    w("就不能宣稱壓縮造成了差異——模型執行間本身就有這麼大的抖動。")
    w()

    # ── 1. 壓縮買到了什麼（這部分沒有雜訊問題，是確定的）
    w("## 1. 壓縮換到的東西")
    w()
    if cr:
        w(f"- 檔案：{cr['bytes_before']/1024/1024:.1f} MB → {cr['bytes_after']/1024/1024:.1f} MB"
          f"（剩 {cr['ratio']*100:.1f}%），54 張中 {cr['n_resized']} 張實際被縮小尺寸")
    w()
    w("| 項目 | 原圖 | 1280/q80 |")
    w("|---|---|---|")
    for label, path, nd, unit in [
        ('每案送出位元組（avg）', ('payload_raw_bytes', 'avg'), 0, 'KB'),
        ('Gemini 視覺延遲 p50', ('latency_ms', 'p50'), 0, 'ms'),
        ('Gemini 視覺延遲 p95', ('latency_ms', 'p95'), 0, 'ms'),
        ('prompt token（avg）', ('tokens_total', 'avg'), 0, ''),
    ]:
        cells = []
        for c, _ in CONDITIONS:
            vals = [r['latency'][path[0]][path[1]] for r in data[c][:n]]
            if unit == 'KB':
                vals = [v / 1024 for v in vals]
            cells.append(rng(vals, nd))
        w(f"| {label}{'（' + unit + '）' if unit else ''} | {cells[0]} | {cells[1]} |")
    w()

    # ── 2. 答對筆數（比率會被分母綁架，筆數不會）
    w("## 2. 答對筆數")
    w()
    w("分母是「該欄位有正解可比的案例數」，不是 48。")
    w()
    cc = {c: correct_counts(data[c][:n]) for c, _ in CONDITIONS}
    fields = sorted(set(cc['orig']) | set(cc['comp']))
    w("**分母也是區間**：模型某一遍沒給某個欄位（回 null）時，該案在那一遍就沒得比，")
    w("分母會少 1。分母若不固定，分子的多寡就不能直接對照——標成「分母浮動」的列，")
    w("差異要回去看逐案，不要當成分數。")
    w()
    w("| 欄位 | 原圖 | 1280/q80 | 判讀 |")
    w("|---|---|---|---|")
    for f in fields:
        a, b = cc['orig'].get(f, []), cc['comp'].get(f, [])
        if not a or not b:
            continue
        va, vb = [x[0] for x in a], [x[0] for x in b]
        da, db = [x[1] for x in a], [x[1] for x in b]
        stable = len(set(da)) == 1 and len(set(db)) == 1 and da[0] == db[0]
        if not stable:
            verdict = '分母浮動'
        else:
            verdict = '✅' if overlap(va, vb) else '⚠️ 分離'
        w(f"| {f} | {rng(va,0)} / {rng(da,0)} | {rng(vb,0)} / {rng(db,0)} | {verdict} |")
    w()

    # ── 3. 連續型指標
    w("## 3. 成分擷取（F1，連續值）")
    w()
    w("| 指標 | 原圖 | 1280/q80 | 重疊 |")
    w("|---|---|---|---|")
    for field, key in [('ingredients_flat', 'f1'), ('ingredients_list', 'f1'),
                       ('ingredients_raw', 'char_sim')]:
        cells, vals = [], {}
        for c, _ in CONDITIONS:
            k = 'char_sim' if key == 'char_sim' else 'f1'
            v = [r['summary'][field][k] for r in data[c][:n]]
            vals[c] = v
            cells.append(rng(v))
        ov = overlap(vals['orig'], vals['comp'])
        w(f"| {field}.{key} | {cells[0]} | {cells[1]} | {'✅' if ov else '⚠️ 分離'} |")
    w()

    # ── 4. 逐案清單：可以直接當工作單的東西
    w("## 4. 逐案差異（工作單）")
    w()
    w("只列「一個條件全對、另一個條件全錯」的案例——這種才穩定到值得去看照片。")
    w("兩邊都時對時錯的，是模型抖動，不是壓縮造成的。")
    w()
    metric_of = {r['field']: r['metric'] for r in data['orig'][0]['per_case']}
    rows = []
    for f in fields:
        metric = metric_of.get(f)
        if metric not in ('correct', 'exact', 'within_tol'):
            continue
        a = case_values(data['orig'][:n], f, metric)
        b = case_values(data['comp'][:n], f, metric)
        for cid in sorted(set(a) & set(b)):
            va, vb = a[cid], b[cid]
            if all(x >= 1 for x in va) and all(x < 1 for x in vb):
                rows.append((cid, f, '壓縮後失守'))
            elif all(x < 1 for x in va) and all(x >= 1 for x in vb):
                rows.append((cid, f, '壓縮後反而對'))
    if rows:
        w("| 案例 | 欄位 | 方向 |")
        w("|---|---|---|")
        for cid, f, d in sorted(rows):
            w(f"| {cid} | {f} | {d} |")
    else:
        w("沒有任何欄位出現穩定的單向差異。")
    w()

    # ── 5. 成分項目層級：壓縮後少讀到哪些字
    w("## 5. 成分項目：壓縮後 F1 掉最多的案例")
    w()
    a = case_values(data['orig'][:n], 'ingredients_flat', 'f1')
    b = case_values(data['comp'][:n], 'ingredients_flat', 'f1')
    deltas = []
    for cid in sorted(set(a) & set(b)):
        if not a[cid] or not b[cid]:
            continue
        d = sum(b[cid]) / len(b[cid]) - sum(a[cid]) / len(a[cid])
        deltas.append((d, cid, a[cid], b[cid]))
    deltas.sort()
    w("| 案例 | 原圖 F1（逐遍） | 壓縮 F1（逐遍） | 平均差 |")
    w("|---|---|---|---|")
    for d, cid, va, vb in deltas[:8]:
        w(f"| {cid} | {' / '.join(f'{x:.2f}' for x in va)} | "
          f"{' / '.join(f'{x:.2f}' for x in vb)} | {d:+.2f} |")
    w()
    w("逐遍值攤開來看：同一案自己 3 遍就跳很大的，別當成壓縮的影響。")
    w()

    out = os.path.join(out_root, 'report.md')
    with open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    print('\n'.join(L))
    print(f"\n報告：{os.path.relpath(out, HERE)}")


def main():
    args = sys.argv[1:]
    cmd = args[0] if args and not args[0].startswith('--') else 'run'
    passes, out_root = 3, os.path.join(RES, 'compression_1280q80')
    for a in args:
        if a.startswith('--passes='):
            passes = int(a.split('=', 1)[1])
        elif a.startswith('--out='):
            out_root = os.path.join(HERE, a.split('=', 1)[1])
    os.makedirs(out_root, exist_ok=True)
    if cmd == 'run':
        cmd_run(out_root, passes)
        cmd_report(out_root)
    elif cmd == 'report':
        cmd_report(out_root)
    else:
        sys.exit(f"看不懂的指令：{cmd}")


if __name__ == '__main__':
    main()
