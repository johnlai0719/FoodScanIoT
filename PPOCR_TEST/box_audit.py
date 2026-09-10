#!/usr/bin/env python3
"""裁切框的品質指標——**不需要正解**。

為什麼要有：`region_crop` 的 `found` 只回答「有沒有切出一個框」，
**框錯位置也算成功**。已知 5 案框到別的地方卻回報 `found=True`
（`c143` 成分被裁掉 8/8）。現行指標數不出這一類。

本檔提供兩條，兩條都只用 PP-OCR 的框與文字，不碰 ground_truth：

  **框內錨點**   成分錨點（成分／原料／配料／內容物／材料）的那一行
                 有沒有落在裁切框裡。沒有 → 框在別的地方。

  **段落保留率** 以 PP-OCR 自己的文字找出成分段（HEAD…TAIL），
                 該段的字元有多少比例落在裁切框內。
                 1.0 = 整段都在框裡；0.4 = 六成被裁掉。

用法：
    python box_audit.py                 # 全測試集報告
    python box_audit.py --dump out/_box_audit.json
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import region_crop as RC   # noqa: E402
import score_ocr as S      # noqa: E402
import bench_ingredients as BI   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BOXES = os.environ.get('PPOCR_BOXES') or 'v6_best'


def _inside(line, box):
    b = RC.bbox(line['box'])
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def audit_image(lines, box):
    """回傳 (框內有沒有錨點, 段落保留率, 段落字元數)。"""
    if box is None:
        return False, None, 0
    anchor = False
    for l in lines:
        if not l.get('box'):
            continue
        n = S.normalize(l['text'], fold_variants=True)
        if any(S.normalize(a, True) in n for a in RC.ING_ANCHORS) and _inside(l, box):
            anchor = True
            break
    # ⚠ 兩個失敗的定義，記錄在此以免重犯：
    #   v1 用「PP-OCR 自己選出的最佳成分段」當分母 → **指標繞回自己**，
    #      那一段就是 region_crop 用來建框的那一段，保留率恆為 1.0。
    #      實測有損失組中位 99%、無損失組 100%，分不開。
    #   v2 用成分字典命中的行當證據 → **太稀疏**，176 案只有 45 案有證據。
    #
    # v3 直接量那件事本身：把**同一個抽取器**分別跑在「整張圖的文字」與
    # 「框內的文字」上，兩者的差集就是被框裁掉的成分項。不需要正解。
    full = chr(10).join(l['text'] for l in lines if l.get('box'))
    inn = chr(10).join(l['text'] for l in lines if l.get('box') and _inside(l, box))
    a_all = [x for x in BI.split_top(full) if BI._strict_item(x)]
    a_in = [x for x in BI.split_top(inn) if BI._strict_item(x)]
    lost = [x for x in a_all if not any(BI._match(x, y) for y in a_in)]
    tot = len(a_all)
    return anchor, (1 - len(lost) / tot if tot else None), tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dump', default=None)
    ap.add_argument('--worst', type=int, default=16)
    a = ap.parse_args()
    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    rows, out = [], {}
    for c in cases:
        cid = c['case_id']
        p = os.path.join(HERE, 'out', BOXES, cid + '.json')
        gt = S.load_gt(cid, c.get('category') or '')
        if not os.path.exists(p) or not gt or not gt.get('is_food_label', True):
            continue
        rec = json.load(open(p, encoding='utf-8'))
        anc, keeps, tots = False, [], 0
        for im in rec['images']:
            lines = [l for l in (im.get('lines') or []) if l.get('box')]
            if not lines:
                continue
            r = RC.find_regions(lines)
            k, kp, n = audit_image(lines, r['ingredients'])
            anc = anc or k
            if kp is not None and n >= 20:
                keeps.append((kp, n))
                tots += n
        keep = (sum(kp * n for kp, n in keeps) / tots) if tots else None
        rows.append({'case_id': cid, 'anchor_in_box': anc, 'keep': keep, 'chars': tots})
        out[cid] = rows[-1]
    n = len(rows)
    na = sum(1 for r in rows if not r['anchor_in_box'])
    kk = [r for r in rows if r['keep'] is not None]
    low = [r for r in kk if r['keep'] < 0.9]
    print('案例 %d' % n)
    print('  框內**沒有**成分錨點：      %d 案' % na)
    print('  段落保留率 < 90%%：         %d 案' % len(low))
    print('  段落保留率 < 60%%：         %d 案' % sum(1 for r in kk if r['keep'] < 0.6))
    print('\n最差的 %d 案：' % a.worst)
    print('  %-32s %8s %8s %8s' % ('case', '錨點在框', '保留率', '段字數'))
    for r in sorted(kk, key=lambda r: r['keep'])[:a.worst]:
        print('  %-32s %8s %7.0f%% %8d'
              % (r['case_id'], '有' if r['anchor_in_box'] else '**無**',
                 100 * r['keep'], r['chars']))
    if a.dump:
        json.dump(out, open(a.dump, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('\n→', a.dump)


if __name__ == '__main__':
    main()
