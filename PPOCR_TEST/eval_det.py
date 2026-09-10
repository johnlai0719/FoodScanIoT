#!/usr/bin/env python3
# 直接量偵測品質：把模型輸出的框與人工 GT 做 IoU 比對，算 precision/recall/hmean。
#
# 為什麼不用 PaddleOCR 的 tools/eval.py：
#   1. 它的 DetResizeForTest 預設是 limit_side_len=736 / limit_type='min'，
#      對 4284x5712 的照片幾乎不縮（只被 max_side_limit=4000 擋住），
#      等於在 3000x4000 上評估——既慢又量不到實際表現，因為
#      run_baseline.py 的 v6_best 用的是 2048 / max。
#   2. 實測它在這批資料上會拋例外，而且 traceback 印到一半自己遞迴爆掉
#      （RecursionError in traceback.py），原始錯誤看不到。
# 自己算反而簡單、快、而且縮放設定完全可控。
#
# 比對規則沿用 ICDAR 慣例：
#   - IoU >= 0.5 視為命中，一個 GT 只能配一個預測（貪婪，取最大 IoU）
#   - GT 標 ### 的是忽略區：落在其中的預測不算誤報，也不要求被偵測到
#
# 用法：
#   python eval_det.py --gt ../FoodScanData/eval_det_gt
#   python eval_det.py --gt ../FoodScanData/eval_det_gt --model output/det_foodlabel_ft/inference
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from shapely.geometry import Polygon
    _SHAPELY = True
except ImportError:
    _SHAPELY = False


def poly_area(p):
    n = len(p)
    return abs(sum(p[i][0] * p[(i + 1) % n][1] - p[(i + 1) % n][0] * p[i][1]
                   for i in range(n))) / 2.0


def signed_area(p):
    n = len(p)
    return sum(p[i][0] * p[(i + 1) % n][1] - p[(i + 1) % n][0] * p[i][1]
               for i in range(n)) / 2.0


def orient(p):
    """統一成 side() <= 0 視為「內側」的繞行方向。

    Sutherland–Hodgman 對裁剪多邊形的繞行方向有假設，方向反了會把所有點
    都判成外側、裁到空集合。實測沒做這件事時 62x54 對框的 IoU **全部恰好
    0.000**——不是低，是整齊的零，那正是「全被裁光」的特徵。
    單元檢查：相同方框的 IoU 必須是 1.0（見檔尾 _selftest）。
    """
    return p[::-1] if signed_area(p) > 0 else list(p)


def clip(subject, clipper):
    """Sutherland–Hodgman：凸多邊形裁剪。det 的框都是凸的，夠用。"""
    subject, clipper = orient(subject), orient(clipper)
    out = subject
    for i in range(len(clipper)):
        if not out:
            return []
        a, b = clipper[i], clipper[(i + 1) % len(clipper)]
        inp, out = out, []

        def side(p):
            return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

        for j in range(len(inp)):
            cur, prv = inp[j], inp[j - 1]
            sc, sp = side(cur), side(prv)
            if sc <= 0:
                if sp > 0:
                    t = sp / (sp - sc) if sp != sc else 0
                    out.append((prv[0] + t * (cur[0] - prv[0]),
                                prv[1] + t * (cur[1] - prv[1])))
                out.append(cur)
            elif sp <= 0:
                t = sp / (sp - sc) if sp != sc else 0
                out.append((prv[0] + t * (cur[0] - prv[0]),
                            prv[1] + t * (cur[1] - prv[1])))
    return out


def iou(a, b):
    """多邊形 IoU。**凹多邊形必須走 shapely。**

    Sutherland–Hodgman（下面的 clip()）只對**凸**的裁剪多邊形正確。而貼合
    曲面的標註幾乎必然是凹的——實測使用者標的 6 點多邊形 31 個裡 31 個都是凹的。
    那些用 clip() 算出來的 IoU 全部不可信。
    shapely 是 PaddleOCR requirements.txt 本來就有的相依，不增加負擔。
    """
    # 外接矩形先篩掉明顯不重疊的，省掉大量幾何運算
    ax = [p[0] for p in a]; ay = [p[1] for p in a]
    bx = [p[0] for p in b]; by = [p[1] for p in b]
    if max(ax) < min(bx) or max(bx) < min(ax) or max(ay) < min(by) or max(by) < min(ay):
        return 0.0
    if _SHAPELY:
        pa, pb = Polygon(a), Polygon(b)
        if not pa.is_valid:
            pa = pa.buffer(0)      # 自交的多邊形（標註時線段交叉）修正成有效幾何
        if not pb.is_valid:
            pb = pb.buffer(0)
        if pa.is_empty or pb.is_empty:
            return 0.0
        inter = pa.intersection(pb).area
        union = pa.area + pb.area - inter
        return inter / union if union > 0 else 0.0
    # 沒有 shapely 時退回裁剪法——只有全部是凸多邊形時才可信
    inter = clip(list(a), list(b))
    if len(inter) < 3:
        return 0.0
    ia = poly_area(inter)
    ua = poly_area(a) + poly_area(b) - ia
    return ia / ua if ua > 0 else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gt', required=True, help='含 Label.txt 的資料夾')
    ap.add_argument('--model', default=None, help='微調後的 det inference 目錄；省略=官方模型')
    ap.add_argument('--iou', type=float, default=0.5)
    ap.add_argument('--side', type=int, default=2048)
    ap.add_argument('--box-thresh', type=float, default=0.4)
    ap.add_argument('--device', default='gpu')
    a = ap.parse_args()

    gtdir = os.path.abspath(a.gt)
    rows = {}
    for line in open(os.path.join(gtdir, 'Label.txt'), encoding='utf-8'):
        if '\t' not in line:
            continue
        k, v = line.rstrip('\n').split('\t', 1)
        # 保留 key 裡的相對路徑：det_gt/ 底下有類別子資料夾
        #（beverage/xxx.jpg），只取檔名會找不到圖。
        # PPOCRLabel 的 key 是「資料夾名/檔名」，開哪一層就以那層為基準。
        rel = k if os.path.exists(os.path.join(gtdir, k)) else k.split('/')[-1]
        rows[rel] = json.loads(v)
    if not rows:
        sys.exit('Label.txt 沒有內容')

    from paddleocr import TextDetection
    kw = dict(model_name='PP-OCRv6_medium_det', device=a.device)
    if a.model:
        kw['model_dir'] = os.path.abspath(a.model)
    det = TextDetection(**kw)

    TP = FP = FN = 0
    per = []
    for fn, shapes in sorted(rows.items()):
        gt = [[tuple(p) for p in s['points']]
              for s in shapes if (s.get('transcription') or '') not in ('###', '*')]
        ign = [[tuple(p) for p in s['points']]
               for s in shapes if (s.get('transcription') or '') in ('###', '*')]
        preds = []
        for r in det.predict(os.path.join(gtdir, fn),
                             limit_side_len=a.side, limit_type='max',
                             thresh=0.2, box_thresh=a.box_thresh):
            d = r.json['res'] if hasattr(r, 'json') else r
            for p in (d.get('dt_polys') or []):
                p = p if isinstance(p, list) else p.tolist()
                preds.append([(float(x), float(y)) for x, y in p])

        used = set()
        tp = 0
        for pi, pr in enumerate(preds):
            best, bj = 0.0, -1
            for j, g in enumerate(gt):
                if j in used:
                    continue
                v = iou(pr, g)
                if v > best:
                    best, bj = v, j
            if best >= a.iou:
                used.add(bj)
                tp += 1
            else:
                # 落在忽略區的預測不算誤報
                if any(iou(pr, g) >= 0.3 for g in ign):
                    preds[pi] = None
        preds = [p for p in preds if p is not None]
        fp = len(preds) - tp
        fn_ = len(gt) - tp
        TP += tp; FP += fp; FN += fn_
        pr_ = tp / max(1, tp + fp)
        rc_ = tp / max(1, tp + fn_)
        per.append((fn, tp, fp, fn_, pr_, rc_))

    P = TP / max(1, TP + FP)
    R = TP / max(1, TP + FN)
    H = 2 * P * R / max(1e-9, P + R)
    print(f'模型：{a.model or "PP-OCRv6_medium_det（官方）"}')
    print(f'設定：limit_side_len={a.side}/max  box_thresh={a.box_thresh}  IoU>={a.iou}\n')
    print(f'{"image":<48}{"TP":>4}{"FP":>5}{"FN":>5}{"P":>7}{"R":>7}')
    for fn, tp, fp, fn_, pr_, rc_ in per:
        print(f"{os.path.basename(fn)[:46]:<48}{tp:>4}{fp:>5}{fn_:>5}{pr_:>7.2f}{rc_:>7.2f}")
    print(f'\n合計  TP {TP}｜FP {FP}｜FN {FN}')
    print(f'precision {P:.4f}｜recall {R:.4f}｜hmean {H:.4f}')


def _selftest():
    """IoU 的健全性檢查。

    寫錯時整批結果會是漂亮的 0，很難察覺——實測繞行方向搞反時，
    62x54 對框的 IoU **全部恰好 0.000**，看起來像「模型完全沒對上」，
    其實是裁剪把所有點都判成外側。所以固定驗一次再跑。
    """
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    cases = [
        ('相同方框', sq, sq, 1.0),
        ('反向繞行', sq, [(0, 0), (0, 10), (10, 10), (10, 0)], 1.0),
        ('半重疊', sq, [(5, 0), (15, 0), (15, 10), (5, 10)], 1 / 3),
        ('不重疊', sq, [(20, 0), (30, 0), (30, 10), (20, 10)], 0.0),
        # 凹多邊形：L 形與自己比對必須是 1.0。裁剪法在這裡會算錯，
        # 而貼合曲面的標註幾乎都是凹的（實測 6 點框 31/31 凹）。
        ('凹多邊形(L形)',
         [(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)],
         [(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)], 1.0),
        # L 形 vs 蓋住它的大方框：交集=L的面積64，聯集=100 → 0.64
        ('凹 vs 外接方框',
         [(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)],
         [(0, 0), (10, 0), (10, 10), (0, 10)], 0.64),
    ]
    ok = True
    for name, a_, b_, want in cases:
        got = iou(a_, b_)
        good = abs(got - want) < 0.01
        ok &= good
        print(f'  {"OK " if good else "FAIL"} {name}: {got:.4f}（應為 {want:.4f}）')
    return ok


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(0 if _selftest() else 1)
    main()
