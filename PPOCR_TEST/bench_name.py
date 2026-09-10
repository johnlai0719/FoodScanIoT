#!/usr/bin/env python3
"""量 `product_name.py`。C 類欄位只報案數不報比率（見 `正解欄位定義.md`）。"""
import argparse, json, io, os, re, sys, unicodedata, difflib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '測試', '量化測試')))
import casetool                      # noqa: E402
import product_name as PN            # noqa: E402

PRESETS = [('v6_best', 'PP-OCR 整張圖'), ('vlcrop_hy_v4', 'vlcrop 裁切區')]


def norm(s):
    s = unicodedata.normalize('NFKC', str(s or ''))
    return re.sub(r'[\s\u3000·、,，.。:：;；()（）\[\]「」【】/／\-_®™©]', '', s).lower()


def lines(pre, cid):
    p = os.path.join(HERE, 'out', pre, cid + '.json')
    if not os.path.exists(p):
        return None
    d = json.load(io.open(p, encoding='utf-8'))
    return [ln.get('text') or '' for im in d.get('images', []) for ln in im.get('lines', [])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--miss', type=int, default=0)
    a = ap.parse_args()
    stat = {lab: [0, 0, 0, 0] for _, lab in PRESETS + [(None, '兩者合併')]}
    rows = []
    for c in casetool.load_cases()['cases']:
        cid = c['case_id']
        gp = casetool.gt_path(cid, c['category'])
        if not os.path.exists(gp):
            continue
        gt = json.load(io.open(gp, encoding='utf-8'))
        g = gt.get('name')
        if not g:
            continue
        got = {}
        for pre, lab in PRESETS:
            L = lines(pre, cid)
            got[lab] = PN.extract(L) if L else None
        got['兩者合併'] = PN.merge([got[lab] for _, lab in PRESETS])
        for lab, v in got.items():
            s = difflib.SequenceMatcher(None, norm(v), norm(g)).ratio() if v else 0
            i = 0 if v and norm(v) == norm(g) else (1 if s >= 0.85 else (2 if v else 3))
            stat[lab][i] += 1
        v = got['兩者合併']
        rows.append((cid, g, v,
                     difflib.SequenceMatcher(None, norm(v), norm(g)).ratio() if v else 0))
    print('正解有 name 的案例：%d\n' % len(rows))
    print('%-16s %8s %10s %10s %8s' % ('來源', '完全對', '近似≥85%', '有給但錯', '沒給'))
    for _, lab in PRESETS + [(None, '兩者合併')]:
        print('%-16s %8d %10d %10d %8d' % tuple([lab] + stat[lab]))
    e, s, w, m = stat['兩者合併']
    print('\n合併後命中（完全＋近似）：%d / %d｜天花板（文字裡讀得到）137'
          % (e + s, len(rows)))
    if a.miss:
        print('\n最差的案例：')
        for cid, g, v, s in sorted(rows, key=lambda r: r[3])[:a.miss]:
            print('  %-26s GT= %-26s 抽到= %s' % (cid[:26], g[:26], v))


if __name__ == '__main__':
    main()
