#!/usr/bin/env python3
"""量 `manufacturer.py` 的成績。C 類欄位——**只報命中案數，不報比率**
（每案一個單位，n=150，任何比率的 CI 都寬到判不出東西；見 `正解欄位定義.md`）。

用法：
  python bench_manufacturer.py            # 總表
  python bench_manufacturer.py --miss 20  # 看沒中的長什麼樣
"""
import argparse, json, io, os, re, sys, unicodedata, difflib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '測試', '量化測試')))
import casetool                      # noqa: E402
import manufacturer as M             # noqa: E402

PRESETS = [('v6_best', 'PP-OCR 整張圖'), ('vlcrop_hy_v4', 'vlcrop 裁切區')]


def norm(s):
    s = unicodedata.normalize('NFKC', str(s or ''))
    return re.sub(r'[\s\u3000·、,，.。:：;；()（）\[\]「」【】/／\-_®™©股份有限]', '', s).lower()


def lines(pre, cid):
    p = os.path.join(HERE, 'out', pre, cid + '.json')
    if not os.path.exists(p):
        return None
    d = json.load(io.open(p, encoding='utf-8'))
    return [ln.get('text') or '' for im in d.get('images', []) for ln in im.get('lines', [])]


def sim(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--miss', type=int, default=0)
    a = ap.parse_args()
    rows, stat = [], {}
    for _, lab in PRESETS + [(None, '兩者合併')]:
        stat[lab] = [0, 0, 0, 0]     # 完全對, 近似>=0.85, 有給但錯, 沒給
    for c in casetool.load_cases()['cases']:
        cid = c['case_id']
        gp = casetool.gt_path(cid, c['category'])
        if not os.path.exists(gp):
            continue
        gt = json.load(io.open(gp, encoding='utf-8'))
        g = gt.get('manufacturer')
        if not g:
            continue
        got, det = {}, []
        for pre, lab in PRESETS:
            L = lines(pre, cid)
            d = M.extract_detail(L) if L else (None, 99)
            got[lab], _ = d
            det.append(d)
        got['兩者合併'] = M.merge(det)
        for lab, v in got.items():
            s = sim(v, g) if v else 0
            i = 0 if v and norm(v) == norm(g) else (1 if s >= 0.85 else (2 if v else 3))
            stat[lab][i] += 1
        rows.append((cid, g, got['兩者合併'], sim(got['兩者合併'], g) if got['兩者合併'] else 0))

    n = len(rows)
    print('正解有 manufacturer 的案例：%d\n' % n)
    print('%-16s %8s %10s %10s %8s' % ('來源', '完全對', '近似≥85%', '有給但錯', '沒給'))
    for _, lab in PRESETS + [(None, '兩者合併')]:
        e, s, w, m = stat[lab]
        print('%-16s %8d %10d %10d %8d' % (lab, e, s, w, m))
    e, s, w, m = stat['兩者合併']
    print('\n合併後命中（完全＋近似）：%d / %d｜天花板（文字裡讀得到）115' % (e + s, n))
    if a.miss:
        print('\n沒中的案例：')
        for cid, g, v, s in sorted(rows, key=lambda r: r[3])[:a.miss]:
            print('  %-26s GT= %-32s 抽到= %s' % (cid[:26], g[:32], v))


if __name__ == '__main__':
    main()
