#!/usr/bin/env python3
"""運算分層：本地先跑，不夠好才上雲。畫出「上雲比例 vs 準確度」的取捨曲線。

**為什麼可以離線算**：177 案的本地預測（`out/json_pponly`，Pi 跑得動的組態）
與雲端預測（`predictions_v4`，現行部署）**兩份都已經存在**。
分層策略只是在逐案決定採用哪一份，所以整條曲線不需要重跑任何模型、
也不需要任何 API 費用。

**升級訊號一律只看本地自己的輸出**，不看正解、不看雲端的答案——
那是 Pi 在現場唯一拿得到的東西。目前的候選：

    n_add    本地比對到幾個添加物
    n_item   本地抽出幾個成分項
    n_line   PP-OCR 讀到幾行
    found    有沒有找到成分區

⚠ **這支畫的是曲線，不是挑一個門檻。** 從測試集挑最佳門檻等於用測試集
   訂參數（本專案的既有約束）。曲線是描述取捨的形狀，挑點要另外用
   開發集或事前訂的需求來做。

用法：
    python cascade.py                # 逐訊號畫曲線
    python cascade.py --signal=n_add
"""
import collections
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S    # noqa: E402
import sim_match as SM   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.environ.get('CAS_LOCAL') or 'json_pponly'
CLOUD = os.environ.get('CAS_CLOUD') or 'predictions_v4'


def load_local(cid):
    p = os.path.join(HERE, 'out', LOCAL, cid + '.json')
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


def load_cloud(cid):
    p = os.path.join(S.EVAL_ROOT, CLOUD, cid + '.json')
    if not os.path.exists(p):
        return None
    return (json.load(open(p, encoding='utf-8')) or {}).get('prediction')


def f1(h, t, m):
    r = h / t if t else 0
    p = h / m if m else 0
    return 100 * 2 * r * p / (r + p) if r + p else 0


def main():
    sig = 'n_add'
    for a in sys.argv[1:]:
        if a.startswith('--signal='):
            sig = a.split('=', 1)[1]
        else:
            sys.exit('看不懂的參數：%s' % a)
    adds, generic = SM.load_additives()
    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    rows = []
    for c in cases:
        cid = c['case_id']
        gt = S.load_gt(cid, c.get('category') or '')
        if not gt or not gt.get('is_food_label', True):
            continue
        lo, cl = load_local(cid), load_cloud(cid)
        if lo is None or cl is None:
            continue
        t = SM.additives_of(gt.get('ingredients_list') or [], adds, generic)
        ml = SM.additives_of(lo.get('ingredients_list') or [], adds, generic)
        mc = SM.additives_of(cl.get('ingredients_list') or [], adds, generic)
        items = lo.get('ingredients_list') or []
        # 升級訊號：只看本地輸出
        nline = 0
        p = os.path.join(HERE, 'out', 'v6_best', cid + '.json')
        if os.path.exists(p):
            nline = sum(len(im.get('lines') or [])
                        for im in json.load(open(p, encoding='utf-8')).get('images') or [])
        rows.append({'cid': cid, 'gt': t, 'local': ml, 'cloud': mc,
                     'n_add': len(ml), 'n_item': len(items), 'n_line': nline})

    print('可用案數 %d ｜ 本地=%s　雲端=%s' % (len(rows), LOCAL, CLOUD))

    def score(pick):
        h = sum(len(r['gt'] & r[pick(r)]) for r in rows)
        t = sum(len(r['gt']) for r in rows)
        m = sum(len(r[pick(r)]) for r in rows)
        z = sum(1 for r in rows if r['gt'] and not (r['gt'] & r[pick(r)]))
        return f1(h, t, m), z

    fl, zl = score(lambda r: 'local')
    fc, zc = score(lambda r: 'cloud')
    print('\n兩個端點')
    print('   全部本地   添加物 F1 %.1f   空清單 %d 案   上雲 0%%' % (fl, zl))
    print('   全部上雲   添加物 F1 %.1f   空清單 %d 案   上雲 100%%' % (fc, zc))

    vals = sorted({r[sig] for r in rows})
    print('\n升級訊號：%s ＝「本地的%s低於門檻就上雲」' % (
        sig, {'n_add': '添加物數', 'n_item': '成分項數', 'n_line': 'OCR 行數'}[sig]))
    print('\n%8s %10s %12s %10s %14s' % ('門檻', '上雲比例', '添加物 F1', '空清單',
                                          '對照：全部上雲'))
    best = None
    for th in [0] + vals:
        up = [r for r in rows if r[sig] < th]
        rate = 100 * len(up) / len(rows)
        f, z = score(lambda r: 'cloud' if r[sig] < th else 'local')
        mark = ''
        if f >= fc and (best is None or rate < best[1]):
            best = (th, rate, f, z)
            mark = '  ← 已追平全部上雲'
        print('%8d %9.0f%% %12.1f %10d %14s%s' % (th, rate, f, z, '%.1f' % fc, mark))
        if rate >= 100:
            break
    if best:
        th, rate, f, z = best
        print('\n**只送 %.0f%% 上雲即可追平「全部上雲」的準確度**'
              '（門檻 %s<%d，F1 %.1f vs %.1f，空清單 %d vs %d）'
              % (rate, sig, th, f, fc, z, zc))
        print('   → 雲端呼叫減少 %.0f%%' % (100 - rate))
    else:
        print('\n此訊號在任何門檻下都追不平「全部上雲」。')


if __name__ == '__main__':
    main()
