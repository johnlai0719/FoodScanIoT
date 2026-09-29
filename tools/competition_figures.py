#!/usr/bin/env python3
"""比賽路線（EasyOCR 定位裁切 → Gemini）的圖表實驗組，輸出到正式文件。

**與論文主線刻意分開**：論文用的是凍結版本 eval-freeze-20260929（本地 vlcrop 管線），
這裡是比賽用的 Gemini 路線，評分方式也不同（成分用完全相同的項目、營養只算 18 格、
添加物是 Cloud 比對器的一致率），兩邊的數字不可互相比較。

資料來源（只讀，不重跑任何 API）：
    .artifacts/gemini-ab-177-v1/          A 整張圖、B Gemini 自框裁切重讀（09-15）
    .artifacts/easyocr-union-gemini-177-v1/ C EasyOCR 定位裁切 → Gemini（比賽路線）
    .artifacts/gemini-additive-eval-163-v1/ 三者的添加物一致率（09-16）
三組都成功的 163 案為共同分母；每組的 data.json 記下這些目錄的雜湊。

用法：python tools/competition_figures.py [--out <資料夾>]
"""
import argparse
import csv
import hashlib
import json
import os
import random
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, ".artifacts")
DEFAULT_OUT = r"D:\FoodScanIot\正式文件\競賽圖表_EasyOCR裁切Gemini"
SEED, B = 0, 5000
# dataviz 參考色盤 slot 1／3／2（validate_palette.js 通過；綠色對比偏低，所以每根長條都標數字）。
# 橘色＝比賽路線 C，在每張圖裡固定。
COL = {"A": "#2a78d6", "B": "#1baf7a", "C": "#eb6834"}
INK, MUTED, GRID = "#1f2328", "#5b6470", "#e3e5e8"
LAB = {"A": "Full image → Gemini", "B": "Gemini self-localized crop", "C": "EasyOCR crop → Gemini (competition)"}
LAB_ZH = {"A": "A 整張圖直接給 Gemini", "B": "B Gemini 自己框區域再裁切重讀", "C": "C 比賽路線：EasyOCR 定位裁切 → Gemini"}


# ── 資料 ──────────────────────────────────────────────────────────────────────
def sha_dir(d):
    h = hashlib.sha256()
    for r, _, fs in os.walk(d):
        for f in sorted(fs):
            if f.endswith(".json"):
                p = os.path.join(r, f)
                h.update(os.path.relpath(p, d).replace("\\", "/").encode())
                h.update(hashlib.sha256(open(p, "rb").read()).digest())
    return h.hexdigest()


def load():
    rd = lambda p: json.load(open(p, encoding="utf-8"))
    ab = {f[:-5]: rd(os.path.join(ART, "gemini-ab-177-v1", "cases", f))
          for f in os.listdir(os.path.join(ART, "gemini-ab-177-v1", "cases"))}
    c = {f[:-5]: rd(os.path.join(ART, "easyocr-union-gemini-177-v1", "cases", f))
         for f in os.listdir(os.path.join(ART, "easyocr-union-gemini-177-v1", "cases"))}
    add = {r["case_id"]: r for r in rd(os.path.join(ART, "gemini-additive-eval-163-v1", "per_case.json"))}
    ids = sorted(k for k in ab.keys() & c.keys() if ab[k].get("status") == c[k].get("status") == "completed")
    per = {}
    for i in ids:
        calls = ab[i]["calls"]
        tok = lambda m: (m.get("usage") or {}).get("total_token_count") or 0
        per[i] = {
            "A": {"s": ab[i]["scores"]["A"], "sec": calls["A"]["elapsed_s"], "tok": tok(calls["A"]), "calls": 1},
            "B": {"s": ab[i]["scores"]["B"], "sec": sum(m["elapsed_s"] for m in calls.values()),
                  "tok": sum(tok(m) for m in calls.values()), "calls": len(calls)},
            "C": {"s": c[i]["score"], "sec": c[i]["call"]["elapsed_s"], "tok": tok(c[i]["call"]), "calls": 1},
        }
        if i in add:
            for m in "ABC":
                per[i][m]["add"] = {k: add[i][m][k] for k in ("tp", "fp", "fn")}
    crop = [{"case": i, **{k: im.get(k) for k in ("retained_area", "fallback", "box_count")}}
            for i, r in c.items() if r.get("status") == "completed" for im in r["images"]]
    prov = {"sources": {d: sha_dir(os.path.join(ART, d)) for d in
                        ("gemini-ab-177-v1", "easyocr-union-gemini-177-v1", "gemini-additive-eval-163-v1")},
            "warning": rd(os.path.join(ART, "gemini-ab-177-v1", "manifest.json")).get("warning"),
            "model": "gemini-2.5-flash", "n_common": len(ids),
            "failed": {"AB": sum(r.get("status") != "completed" for r in ab.values()),
                       "C": sum(r.get("status") != "completed" for r in c.values())}}
    return per, crop, prov


def boot(keys, stat):
    rng = random.Random(SEED)
    ks = sorted(keys)
    v = sorted(stat([ks[rng.randrange(len(ks))] for _ in ks]) for _ in range(B))
    return {"lo": v[int(B * .025)], "hi": v[int(B * .975)]}


def ing(per, m, s):
    h = sum(per[i][m]["s"]["ingredient_exact_hit"] for i in s)
    p = sum(per[i][m]["s"]["ingredient_predicted"] for i in s)
    g = sum(per[i][m]["s"]["ingredient_gt"] for i in s)
    return {"precision": h / p, "recall": h / g, "f1": 2 * h / (p + g), "hit": h, "pred": p, "gt": g}


def num(per, m, s):
    t = {k: sum(per[i][m]["s"][k] for i in s) for k in ("numeric_correct", "numeric_wrong", "numeric_missing", "numeric_hardfill")}
    t["accuracy"] = t["numeric_correct"] / (t["numeric_correct"] + t["numeric_wrong"] + t["numeric_missing"])
    return t


def adds(per, m, s):
    tp = sum(per[i][m]["add"]["tp"] for i in s); fp = sum(per[i][m]["add"]["fp"] for i in s)
    fn = sum(per[i][m]["add"]["fn"] for i in s)
    return {"precision": tp / (tp + fp), "recall": tp / (tp + fn), "f1": 2 * tp / (2 * tp + fp + fn), "tp": tp, "fp": fp, "fn": fn}


# ── 繪圖與輸出 ────────────────────────────────────────────────────────────────
def plt_style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "serif", "font.serif": ["Times New Roman", "DejaVu Serif"], "font.size": 10,
        "axes.titlesize": 11, "axes.titleweight": "bold", "axes.edgecolor": MUTED, "axes.labelcolor": INK,
        "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "axes.axisbelow": True,
        "legend.frameon": False, "savefig.dpi": 300, "savefig.bbox": "tight"})
    return plt


def legend(fig, plt, y=-.1):
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(facecolor=COL[m], label=LAB[m]) for m in "ABC"],
               loc="lower center", ncol=3, bbox_to_anchor=(.5, y), fontsize=9)


def bars(ax, groups, getter, ylim, fmt="{:.1f}"):
    w = .26
    for j, m in enumerate("ABC"):
        vals = [getter(m, g) for g in groups]
        xs = [i + (j - 1) * w for i in range(len(groups))]
        ax.bar(xs, vals, w - .03, color=COL[m], zorder=2)
        for x, v in zip(xs, vals):
            ax.text(x, v + (ylim[1] - ylim[0]) * .012, fmt.format(v), ha="center", fontsize=7.8, color=INK)
    ax.set_ylim(*ylim)


def write_set(out, folder, files, readme_zh, summary_md, data, table_rows):
    d = os.path.join(out, folder)
    os.makedirs(d, exist_ok=True)
    for name, fig in files.items():
        fig.savefig(os.path.join(d, name + ".png")); fig.savefig(os.path.join(d, name + ".pdf"))
    json.dump(data, open(os.path.join(d, "data.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if table_rows:
        with open(os.path.join(d, "數據.csv"), "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(table_rows)
    open(os.path.join(d, "技術細節.md"), "w", encoding="utf-8").write(readme_zh)
    # 說明.md 只在第一次產生：之後使用者可能會自己改寫，不覆蓋
    p = os.path.join(d, "說明.md")
    if not os.path.exists(p):
        open(p, "w", encoding="utf-8").write(summary_md)


def pct(v):
    return f"{v * 100:.1f}"


def ci(c):
    return f"{c['lo'] * 100:+.1f}～{c['hi'] * 100:+.1f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    out = a.out
    os.makedirs(out, exist_ok=True)
    per, crop, prov = load()
    ids = sorted(per)
    n = len(ids)
    plt = plt_style()
    common_note = (f"比賽專用。與論文主線（凍結版本 eval-freeze-20260929）的評分方式不同，數字不可互相比較。"
                   f"模型 {prov['model']}；共同分母 {n} 案（A／B 失敗 {prov['failed']['AB']} 案、C 失敗 {prov['failed']['C']} 案）。"
                   f"原始紀錄標註：{prov['warning']}。")

    # K1 成分與營養
    I = {m: ing(per, m, ids) for m in "ABC"}
    N = {m: num(per, m, ids) for m in "ABC"}
    dI = {f"{x}-{y}": boot(ids, lambda s, x=x, y=y: ing(per, x, s)["f1"] - ing(per, y, s)["f1"]) for x, y in (("C", "A"), ("C", "B"))}
    dN = {f"{x}-{y}": boot(ids, lambda s, x=x, y=y: num(per, x, s)["accuracy"] - num(per, y, s)["accuracy"]) for x, y in (("C", "A"), ("C", "B"))}
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.9), gridspec_kw={"wspace": .28, "width_ratios": [1.5, 1]})
    bars(ax[0], ["precision", "recall", "f1"], lambda m, g: I[m][g] * 100, (40, 90))
    ax[0].set_xticks(range(3), ["Precision", "Recall", "F1"]); ax[0].set_ylabel("Ingredient items (%)")
    ax[0].set_title(f"(a) Ingredient list (exact item match, n = {n})")
    x = range(3)
    ax[1].bar(x, [N[m]["accuracy"] * 100 for m in "ABC"], .55, color=[COL[m] for m in "ABC"], zorder=2)
    for i, m in enumerate("ABC"):
        ax[1].text(i, N[m]["accuracy"] * 100 + .3, f"{N[m]['accuracy'] * 100:.1f}\nwrong {N[m]['numeric_wrong']} / miss {N[m]['numeric_missing']}",
                   ha="center", fontsize=7.8, color=INK)
    ax[1].set_ylim(85, 100); ax[1].set_xticks(list(x), ["A", "B", "C"]); ax[1].set_ylabel("Nutrition cells correct (%)")
    ax[1].set_title("(b) Nutrition (18 cells per case)")
    legend(fig, plt)
    rows = [["方法", "成分 P", "成分 R", "成分 F1", "營養正確率", "營養錯值", "營養缺值", "營養硬填"]] + [
        [LAB_ZH[m], pct(I[m]["precision"]), pct(I[m]["recall"]), pct(I[m]["f1"]), pct(N[m]["accuracy"]),
         N[m]["numeric_wrong"], N[m]["numeric_missing"], N[m]["numeric_hardfill"]] for m in "ABC"]
    tech = f"""# K1 技術細節：成分與營養

- {common_note}
- 成分：預測清單與正解清單做**完全相同**的項目比對（多重集合），micro 加總。
- 營養：每案 18 格（每份與每 100g 各 9 欄），正確率＝正確 ÷（正確＋錯值＋缺值）；硬填＝正解為空卻給了數值。
- 配對 bootstrap：對案例重抽 {B} 次，seed {SEED}。

| 比較 | 成分 F1 差 95% CI | 營養正確率差 95% CI |
|---|---|---|
| C − A | {ci(dI['C-A'])} | {ci(dN['C-A'])} |
| C − B | {ci(dI['C-B'])} | {ci(dN['C-B'])} |
"""
    sig = lambda c: c["lo"] > 0 or c["hi"] < 0
    summ = f"""# K1 成分與營養：三種 Gemini 用法

> 比賽專用｜{n} 案｜Gemini 2.5 Flash

![](fig_k1.png)

**可填入的描述**
在同一份 177 案食品標示測試集上，比較三種使用 Gemini 2.5 Flash 的方式：整張圖直接輸入（A）、由 Gemini 自行框出目標區域後裁切重讀（B），以及本競賽採用的路線——以 EasyOCR 偵測全部文字框、取其外接範圍裁切後再交給 Gemini（C）。在三者皆成功的 {n} 案上，C 的成分清單 F1 為 {pct(I['C']['f1'])}%，較 A 的 {pct(I['A']['f1'])}% 高（95% CI {ci(dI['C-A'])}）；營養數值正確率為 {pct(N['C']['accuracy'])}%，{'高於' if sig(dN['C-A']) and dN['C-A']['lo'] > 0 else '與'} A 的 {pct(N['A']['accuracy'])}%{'（95% CI ' + ci(dN['C-A']) + '）' if sig(dN['C-A']) else '無顯著差異'}。

**論述重點**
- 以 EasyOCR 先框出文字範圍，只移除照片中沒有文字的部分，不需要辨識內容，也不需要額外呼叫一次 Gemini。
- 相較 B（需要兩次 Gemini 呼叫），C 的成分 F1 {'無顯著差異' if not sig(dI['C-B']) else ('較高' if dI['C-B']['lo'] > 0 else '較低')}（95% CI {ci(dI['C-B'])}），營養缺值則少很多（{N['C']['numeric_missing']} 對 {N['B']['numeric_missing']} 格）。

**限制**
- 此資料集在開發過程中已被使用，屬探索性結果，並非獨立驗收。
- Gemini 每次執行的結果會有差異，本表為單次執行。
- 評分方式與論文主線不同，不可與主線數字比較。
"""
    write_set(out, "K1_成分與營養（三種Gemini用法）", {"fig_k1": fig}, tech, summ,
              {"provenance": prov, "ingredient": I, "nutrition": N, "delta_ci": {"ingredient_f1": dI, "nutrition_acc": dN}}, rows)

    # K2 添加物一致率
    ida = [i for i in ids if "add" in per[i]["A"]]
    Ad = {m: adds(per, m, ida) for m in "ABC"}
    dA = {f"{x}-{y}": boot(ida, lambda s, x=x, y=y: adds(per, x, s)["f1"] - adds(per, y, s)["f1"]) for x, y in (("C", "A"), ("C", "B"))}
    fig, ax = plt.subplots(figsize=(7, 3.9))
    bars(ax, ["precision", "recall", "f1"], lambda m, g: Ad[m][g] * 100, (70, 100))
    ax.set_xticks(range(3), ["Precision", "Recall", "F1"]); ax.set_ylabel("Additive agreement (%)")
    ax.set_title(f"Additive agreement with ground truth (n = {len(ida)})")
    legend(fig, plt, -.16)
    rows = [["方法", "精確率", "召回率", "F1", "TP", "FP", "FN"]] + [
        [LAB_ZH[m], pct(Ad[m]["precision"]), pct(Ad[m]["recall"]), pct(Ad[m]["f1"]), Ad[m]["tp"], Ad[m]["fp"], Ad[m]["fn"]] for m in "ABC"]
    tech = f"""# K2 技術細節：添加物一致率

- {common_note}
- 計算方式（`tools/evaluate_gemini_additives.py`，09-16）：把正解與預測的成分都用 Cloud 的添加物比對器（`module_a.ingredient_matching`，804 筆知識庫）轉成添加物編號，再比對集合。
- **這是「一致率」不是獨立標註的準確率**：正解沒有另外標註添加物，比對器配不到的正解成分直接排除（清單見 `gemini-additive-eval-163-v1/gt_unmatched_review.csv`）。
- 配對 bootstrap：{B} 次，seed {SEED}。C − A 的 F1 差 95% CI {ci(dA['C-A'])}；C − B {ci(dA['C-B'])}。
"""
    summ = f"""# K2 添加物一致率

> 比賽專用｜{len(ida)} 案｜以 Cloud 添加物比對器計算

![](fig_k2.png)

**可填入的描述**
以系統實際使用的添加物比對器，分別處理正解與各方法的成分清單後比對。C 的添加物 F1 為 {pct(Ad['C']['f1'])}%（精確率 {pct(Ad['C']['precision'])}%、召回率 {pct(Ad['C']['recall'])}%），A 為 {pct(Ad['A']['f1'])}%，B 為 {pct(Ad['B']['f1'])}%。C 相對 A 的 F1 差 95% CI 為 {ci(dA['C-A'])}，相對 B 為 {ci(dA['C-B'])}。

**論述重點**
- 整張圖輸入（A）的誤判（FP {Ad['A']['fp']} 個）明顯多於裁切的兩種方法（B {Ad['B']['fp']}、C {Ad['C']['fp']}），顯示裁切能減少非目標文字造成的誤判。

**限制**
- 正解未另外標註添加物，此為「比對器一致率」而非獨立準確率。
- 與論文主線的添加物 F1（評分方式不同）不可互相比較。
"""
    write_set(out, "K2_添加物一致率", {"fig_k2": fig}, tech, summ, {"provenance": prov, "additive": Ad, "delta_ci": dA}, rows)

    # K3 速度與成本
    secs = {m: [per[i][m]["sec"] for i in ids] for m in "ABC"}
    toks = {m: [per[i][m]["tok"] for i in ids] for m in "ABC"}
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.9), gridspec_kw={"wspace": .3})
    bp = ax[0].boxplot([secs[m] for m in "ABC"], widths=.5, patch_artist=True, showfliers=True,
                       medianprops={"color": INK}, flierprops={"markersize": 3, "markeredgecolor": MUTED})
    for patch, m in zip(bp["boxes"], "ABC"):
        patch.set_facecolor(COL[m]); patch.set_alpha(.85); patch.set_edgecolor("white")
    for i, m in enumerate("ABC", 1):
        ax[0].text(i + .3, st.median(secs[m]), f"{st.median(secs[m]):.1f}s", va="center", fontsize=8, color=INK)
    ax[0].set_xticks([1, 2, 3], ["A", "B", "C"]); ax[0].set_ylabel("Seconds per case (API calls)")
    ax[0].set_title("(a) Latency per case")
    vals = [st.mean(toks[m]) for m in "ABC"]
    ax[1].bar(range(3), vals, .55, color=[COL[m] for m in "ABC"], zorder=2)
    for i, v in enumerate(vals):
        ax[1].text(i, v + 40, f"{v:.0f}", ha="center", fontsize=8.5, color=INK)
    ax[1].set_xticks(range(3), ["A", "B (2 calls)", "C"]); ax[1].set_ylabel("Tokens per case (mean)")
    ax[1].set_title("(b) Token usage")
    legend(fig, plt)
    rows = [["方法", "Gemini 呼叫次數／案", "耗時中位數（秒）", "耗時平均（秒）", "token 平均／案"]] + [
        [LAB_ZH[m], per[ids[0]][m]["calls"], f"{st.median(secs[m]):.1f}", f"{st.mean(secs[m]):.1f}", f"{st.mean(toks[m]):.0f}"] for m in "ABC"]
    tech = f"""# K3 技術細節：速度與成本

- {common_note}
- 耗時是**Gemini API 呼叫本身**的時間（不含 EasyOCR 偵測、影像傳輸與 App／Fog 的處理），B 為兩次呼叫的合計。
- A、B 於 2026-09-15 量測，C 於另一時段量測，受當下 API 負載影響，只宜看量級。
- token 取 API 回傳的 `total_token_count`。
"""
    summ = f"""# K3 速度與成本

> 比賽專用｜{n} 案｜只計 Gemini API 呼叫

![](fig_k3.png)

**可填入的描述**
C 每案只需一次 Gemini 呼叫，耗時中位數 {st.median(secs['C']):.1f} 秒、平均 {st.mean(toks['C']):.0f} 個 token，與整張圖輸入（A：{st.median(secs['A']):.1f} 秒、{st.mean(toks['A']):.0f} token）相當；B 需要兩次呼叫（先定位、再裁切重讀），耗時中位數 {st.median(secs['B']):.1f} 秒、token {st.mean(toks['B']):.0f}，約為 C 的 {st.median(secs['B']) / st.median(secs['C']):.1f} 倍與 {st.mean(toks['B']) / st.mean(toks['C']):.1f} 倍。

**論述重點**
- 以本地 EasyOCR 取代 Gemini 做定位，保留了裁切的效果，卻不增加雲端呼叫次數與費用。

**限制**
- 耗時只含 API 呼叫，不含 EasyOCR 偵測與網路傳輸；各方法量測時段不同。
"""
    write_set(out, "K3_速度與成本", {"fig_k3": fig}, tech, summ,
              {"provenance": prov, "seconds": {m: {"median": st.median(secs[m]), "mean": st.mean(secs[m])} for m in "ABC"},
               "tokens_mean": {m: st.mean(toks[m]) for m in "ABC"}}, rows)

    # K4 裁切幅度
    ra = [x["retained_area"] for x in crop if x.get("retained_area") is not None and not x.get("fallback")]
    fb = [x for x in crop if x.get("fallback")]
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ax.hist([v * 100 for v in ra], bins=range(0, 101, 5), color=COL["C"], edgecolor="white", zorder=2)
    ax.axvline(st.median(ra) * 100, color=INK, lw=1, ls="--")
    ax.text(st.median(ra) * 100 + 1, ax.get_ylim()[1] * .92, f"median {st.median(ra) * 100:.0f}%", fontsize=8.5, color=INK)
    ax.set_xlabel("Area kept after crop (% of original photo)"); ax.set_ylabel("Photos")
    ax.set_title(f"EasyOCR crop size (n = {len(ra)} cropped photos; {len(fb)} fell back to full image)")
    rows = [["項目", "數值"], ["裁切的照片數", len(ra)], ["退回整張的照片數", len(fb)],
            ["保留面積中位數（%）", f"{st.median(ra) * 100:.1f}"],
            ["保留面積四分位（%）", f"{st.quantiles(ra, n=4)[0] * 100:.1f}～{st.quantiles(ra, n=4)[2] * 100:.1f}"]]
    tech = f"""# K4 技術細節：裁切幅度

- {common_note}
- 做法（`tools/easyocr_union_gemini.py`）：EasyOCR 只做偵測、不做辨識，把一張照片所有文字框取外接矩形，外擴 4%（至少 16 px）。
- 退回整張的條件：外接範圍佔原圖 90% 以上，或邊長過小（裁了等於沒裁、反而多一層失真）。
- 本圖的分母是 C 成功的全部照片（不限 163 案）。
"""
    summ = f"""# K4 裁切幅度

> 比賽專用｜EasyOCR 定位裁切

![](fig_k4.png)

**可填入的描述**
比賽路線以 EasyOCR 偵測照片中的所有文字框，取外接範圍並外擴 4% 後裁切，不做文字辨識。{len(ra)} 張被裁切的照片中，保留面積中位數為原圖的 {st.median(ra) * 100:.0f}%；另有 {len(fb)} 張因文字分布幾乎佔滿整張圖而退回使用原圖。

**論述重點**
- 裁切移除的主要是照片中沒有文字的背景，平均去掉約三分之一的面積，讓 Gemini 的影像解析度集中在文字區域。

**限制**
- 只以「有沒有文字」決定範圍，不區分成分、營養或行銷文字；文字散布整張包裝時裁切效果有限。
"""
    write_set(out, "K4_裁切幅度", {"fig_k4": fig}, tech, summ,
              {"provenance": prov, "retained_area": ra, "fallback": fb}, rows)

    # K0 路線說明（無圖）
    p = os.path.join(out, "K0_路線說明", "說明.md")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not os.path.exists(p):
        open(p, "w", encoding="utf-8").write(f"""# K0 比賽路線說明

> 比賽專用。論文主線請見 `正式文件/研究圖表_eval-freeze-20260929/`。

**流程**：照片 → EasyOCR 偵測所有文字框（不辨識）→ 取外接範圍、外擴 4% 裁切 → Gemini 2.5 Flash 以結構化輸出讀出成分、營養、過敏原、品名、廠商 → Cloud 做添加物比對與健康評分。

**與論文主線的差別**

| | 比賽路線 | 論文主線 |
|---|---|---|
| 讀取 | Gemini 2.5 Flash（雲端 API） | PP-OCR ＋ HunyuanOCR ＋ Qwen（本地 GPU） |
| 定位 | EasyOCR 文字框外接範圍 | PP-OCR 找成分區、營養區 |
| 成本 | 每案一次 API 呼叫 | 無 API 費用，需要 GPU |
| 風險 | 模型看得到影像，可能補上圖上沒有的內容（見論文 B7） | 輸出限定取自 OCR 文字 |

**對照組**：A 整張圖直接給 Gemini；B 由 Gemini 自己框出區域後裁切重讀（兩次呼叫）。

**程式**：`adi-multimodal-compliance` 分支的 `tools/easyocr_union_gemini.py`（管線）、`tools/gemini_compare.py`（A/B）、`tools/evaluate_gemini_additives.py`（添加物）、`tools/competition_figures.py`（本資料夾的圖表）。
""")

    # 索引
    rows = []
    for dname in sorted(x for x in os.listdir(out) if os.path.isdir(os.path.join(out, x))):
        fs = os.listdir(os.path.join(out, dname))
        has = "、".join(k for k, ok in (("圖", any(f.endswith(".png") for f in fs)), ("數據", "數據.csv" in fs), ("說明", "說明.md" in fs)) if ok)
        rows.append(f"| {dname} | {has} |")
    open(os.path.join(out, "README.md"), "w", encoding="utf-8").write(f"""# 競賽圖表：EasyOCR 定位裁切 → Gemini

**比賽專用，與論文分開。** 論文主線的圖表在 `正式文件/研究圖表_eval-freeze-20260929/`，兩邊的評分方式不同，數字不可互相比較。

{common_note}

| 資料夾 | 內容 |
|---|---|
{chr(10).join(rows)}

每個資料夾：**說明.md**（可直接填入文件的描述，不會被覆蓋，可自行修改）、**數據.csv**（Excel 可直接開）、**fig_*.png／pdf**、**技術細節.md／data.json**（計算方式與來源雜湊）。

重新產生：在 `adi-multimodal-compliance` worktree 執行 `python tools/competition_figures.py`（只讀取既有結果，不呼叫 API）。本檔自動產生。
""")
    print("→", out)
    for m in "ABC":
        print(m, "ingF1", pct(I[m]["f1"]), "num", pct(N[m]["accuracy"]), "addF1", pct(Ad[m]["f1"]))
    print("CI ing C-A", ci(dI["C-A"]), "C-B", ci(dI["C-B"]), "| num C-A", ci(dN["C-A"]), "C-B", ci(dN["C-B"]), "| add C-A", ci(dA["C-A"]), "C-B", ci(dA["C-B"]))


if __name__ == "__main__":
    main()
