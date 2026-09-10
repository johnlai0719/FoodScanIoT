#!/usr/bin/env python3
"""做「先分群、再分層」的切分，寫成 `split.json` 給訓練與評估共用。

**三件事一定要一起做，少一件量出來的泛化就是錯的：**

**一、同系列整群進同一邊。** 177 案裡有 24 群「同系列不同口味」、涵蓋 59 案
（滿漢大餐四種口味、品客三種、多力多滋三種…）。它們不是同一件商品，但**包裝版型
幾乎一樣**——同字體、同欄位排列、同印刷方式。`品客披薩` 進訓練、`品客起司` 進測試，
測出來的分數是虛的。這也是教授建議 13「同商品所有影像須在同一 split」的精神，
只是這裡的單位是「系列」而不是「商品」（`product_id` 還沒建，見決策單 #10）。

**二、依 category 分層。** 不分層的話會像第一版那樣：直接取前 20 筆，結果
**餅乾與醬料兩整類 0 案進訓練**，而它們佔測試側的 27%。那會把「資料不夠」
和「這一類從沒見過」混在一起，量出來的泛化被低估而且不知道低估多少。

**三、評估集固定，訓練集巢狀。** 第一版讓 `held = rows[n:]`，n 一變評估集也跟著變
——20 案時在 114 案上測、40 案時在 94 案上測，**那兩個數字不可比**，畫出來的不是
學習曲線。改成先切出一組固定的評估集，訓練集從剩下的池子裡巢狀抽
（20 ⊂ 40 ⊂ 60 ⊂ 80），各點才落在同一把尺上。

**為什麼要寫成檔案**：訓練與評估分兩支程式跑，切分若各算各的，改了一邊忘了另一邊
會得到錯的數字**而且不會報錯**。同一天已經因為「寫死的預設」踩過十次同型的坑。

⚠ 汙染區專用。這裡產生的任何數字都不得寫進報告，見 `build_ft_pairs.py` 檔頭。
"""
import argparse, collections, difflib, io, json, os, random, re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
PAIRS = os.path.join(HERE, 'out', '_contaminated_ft_pairs')
SIM = 0.62      # 群的相似度門檻，與 2026-09-08 盤點 24 群時用的一致


def key(cid):
    return re.sub(r'[_\s\-（）()0-9]', '', cid.split('_', 1)[-1])


def group(rows):
    """名稱高度相似的併成一群。單獨的自成一群。"""
    out = []
    for r in rows:
        n = key(r['case_id'])
        hit = None
        for g in out:
            if any(difflib.SequenceMatcher(None, n, key(x['case_id'])).ratio() >= SIM
                   for x in g):
                hit = g
                break
        if hit is None:
            out.append([r])
        else:
            hit.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sizes', type=int, nargs='+', default=[20, 40, 60, 80],
                    help='學習曲線的各個訓練規模（巢狀）')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--out', default=os.path.join(PAIRS, 'split.json'))
    a = ap.parse_args()

    rows = [json.loads(l) for l in io.open(os.path.join(PAIRS, 'pairs.jsonl'),
                                           encoding='utf-8')]
    groups = group(rows)
    print('%d 案 -> %d 群（最大的群 %d 案）'
          % (len(rows), len(groups), max(len(g) for g in groups)))

    rnd = random.Random(a.seed)
    bycat = collections.defaultdict(list)
    for g in groups:
        cat = collections.Counter(r['category'] for r in g).most_common(1)[0][0]
        bycat[cat].append(g)
    for v in bycat.values():
        rnd.shuffle(v)
        v.sort(key=len)              # 先拿小群，配額才好收斂

    nmax = max(a.sizes)
    pool, evals = collections.defaultdict(list), []
    for cat, gs in bycat.items():
        quota = round(nmax * sum(len(g) for g in gs) / len(rows))
        for g in gs:
            if quota > 0:
                pool[cat].append(g)
                quota -= len(g)
            else:
                evals.append(g)
    ev = [r for g in evals for r in g]

    subsets, chosen = {}, {c: [] for c in pool}
    for n in sorted(a.sizes):
        for cat, gs in pool.items():
            quota = round(n * sum(1 for r in rows if r['category'] == cat) / len(rows))
            have = sum(len(g) for g in chosen[cat])
            for g in gs:
                if g in chosen[cat]:
                    continue
                if have >= quota:
                    break
                chosen[cat].append(g)
                have += len(g)
        ids = [r['case_id'] for c in chosen for g in chosen[c] for r in g]
        subsets[n] = sorted(ids)

    A = collections.Counter(r['category'] for r in rows)
    E = collections.Counter(r['category'] for r in ev)
    print('')
    print('評估集固定 %d 案｜訓練池 %d 案'
          % (len(ev), sum(len(g) for gs in pool.values() for g in gs)))
    print('%-16s %6s %6s' % ('category', '全部', '評估集'))
    for c in sorted(A):
        print('%-16s %6d %6d' % (c, A[c], E[c]))
    print('')
    for n in sorted(subsets):
        d = collections.Counter(
            r['category'] for r in rows if r['case_id'] in set(subsets[n]))
        print('訓練 %2d -> 實得 %2d 案   %s'
              % (n, len(subsets[n]),
                 '、'.join('%s %d' % (k, v) for k, v in sorted(d.items()))))

    nested = all(set(subsets[a1]) <= set(subsets[b1])
                 for a1, b1 in zip(sorted(subsets), sorted(subsets)[1:]))
    overlap = set(subsets[max(subsets)]) & {r['case_id'] for r in ev}
    print('')
    print('巢狀檢查 %s｜訓練與評估重疊 %d 案（必須是 0）'
          % ('通過' if nested else '**失敗**', len(overlap)))
    assert not overlap, '訓練集與評估集重疊，切分是錯的'

    json.dump({'seed': a.seed, 'sim_threshold': SIM,
               'held': sorted(r['case_id'] for r in ev),
               'train_sets': {str(k): v for k, v in sorted(subsets.items())},
               'train': subsets[min(subsets)],
               'note': ('評估集固定、訓練集巢狀；同系列整群不跨邊；依 category 分層。'
                        '汙染區專用，數字不得寫進報告。')},
              io.open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('-> %s' % a.out)


if __name__ == '__main__':
    main()
