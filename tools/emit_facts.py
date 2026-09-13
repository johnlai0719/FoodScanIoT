#!/usr/bin/env python3
"""從程式碼與資料庫抽出「事實基底」，供撰寫正式文件時引用。

為什麼是腳本而不是一份手寫的 md
------------------------------
**行號會在幾小時內過期。** 2026-09-14 當天，`SAFETY_EVENTS_ENABLED` 從第 124
行移到 132、analyze 的三條路由從 829 移到 838——而正式文件裡的行號還停在舊值。
手寫的事實清單一定會鏽掉，唯一能不鏽的是「每次重抽」。

分工
----
這支負責**可驗證的事實**：行號、路由、資料表、欄位、常數、版本。
文字潤飾與脈絡說明交給寫作者（人或模型），但**數字只能從這裡引用**。

⚠ 它不產生任何「結論」或「評價」。量測結果那一節的數字來自實驗腳本的輸出，
   一律附上測量日期與樣本數；沒有來源的數字不會出現在這裡。

用法：
    python tools/emit_facts.py                 # 印到 stdout
    python tools/emit_facts.py --out <路徑>     # 寫檔（會先讀舊檔做 diff）
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def find_line(rel, pattern):
    """回傳 (行號, 該行內容)。找不到回 (None, None)——**不猜**。"""
    try:
        src = read(rel)
    except OSError:
        return None, None
    for n, line in enumerate(src.split("\n"), 1):
        if re.search(pattern, line):
            return n, line.strip()
    return None, None


def git(*args):
    try:
        return subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def routes():
    """server/main.py 的路由。靜態掛載分開算——它們不是 API。"""
    src = read("server/main.py")
    api, mounts = [], []
    lines = src.split("\n")
    for n, line in enumerate(lines, 1):
        m = re.match(r'@app\.(get|post|put|delete|patch)\("([^"]+)"(.*)', line.strip())
        if m:
            # 兩種驗證機制要分開。只看 require_api_key 會把用 JWT 的
            # admin 端點標成「無保護」——那是誤導，不是事實。
            rest = m.group(3)
            # 裝飾器行之後的函式簽章也要看（Depends 常寫在參數上）
            sig = lines[n] if n < len(lines) else ""
            if "require_api_key" in rest:
                guard = "X-API-Key"
            elif "get_current_user" in sig or "get_current_user" in rest:
                guard = "JWT（管理員登入）"
            else:
                guard = "無"
            api.append({"line": n, "method": m.group(1).upper(),
                        "path": m.group(2), "guard": guard})
        m2 = re.search(r'app\.mount\("([^"]+)"', line)
        if m2:
            # 條件掛載（縮排在 if 底下）與固定掛載要分得開
            mounts.append({"line": n, "path": m2.group(1),
                           "conditional": line.startswith(" ")})
    return api, mounts


def db_facts():
    """資料表與關鍵欄位。連不上就回 None——**不填假資料**。"""
    def q(sql):
        r = subprocess.run(
            ["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres",
             "-d", "product_db", "-t", "-A", "-c", sql],
            cwd=ROOT, capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else None

    tables = q("SELECT string_agg(table_name, ',' ORDER BY table_name) "
               "FROM information_schema.tables WHERE table_schema='public';")
    if tables is None:
        return None
    counts = {}
    for t in tables.split(","):
        c = q("SELECT count(*) FROM %s;" % t)
        counts[t] = int(c) if c and c.isdigit() else None
    prod_cols = q("SELECT string_agg(column_name, ',' ORDER BY ordinal_position) "
                  "FROM information_schema.columns WHERE table_name='products';")
    return {"tables": counts, "products_columns": (prod_cols or "").split(",")}


def const(rel, name):
    n, line = find_line(rel, r"^%s\s*=" % re.escape(name))
    return {"file": rel, "line": n, "source": line}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()

    api, mounts = routes()
    db = db_facts()

    out = []
    w = out.append
    w("# 事實基底（自動產生，請勿手改）")
    w("")
    w("> **這份檔案由 `tools/emit_facts.py` 產生。** 手改會在下次重跑時被覆蓋。")
    w("> 產生時間：%s　commit：`%s`　分支：`%s`"
      % (date.today().isoformat(), git("rev-parse", "--short", "HEAD"),
         git("rev-parse", "--abbrev-ref", "HEAD")))
    w("")
    w("## 給撰寫者的規則")
    w("")
    w("1. **數字只能引用本檔。** 行號、路由、欄位、常數都會變——2026-09-14 一天內")
    w("   `SAFETY_EVENTS_ENABLED` 就從 124 行移到 132。要用就重跑這支。")
    w("2. **「後端有」不等於「前端有」。** 本檔把 Cloud 契約與 App 實際渲染分成")
    w("   兩節，因為它們曾被混為一談（Cloud 的 `nutrition_facts` 有六欄，")
    w("   但 App 沒有任何渲染營養數值的程式碼）。")
    w("3. **禁用絕對化字眼**（全面／一律／完整／永遠），除非本檔明文寫出範圍。")
    w("4. **本檔沒寫的就是不知道**，不要補完。找不到的欄位會標成 `（查無）`，")
    w("   那是結論不是缺漏。")
    w("")

    # ── 路由 ──
    w("## API 路由（server/main.py）")
    w("")
    w("共 **%d 條 API 路由**，另有 **%d 個靜態掛載**（靜態掛載不是 API，分開計）。"
      % (len(api), len(mounts)))
    w("")
    w("| 行號 | 方法 | 路徑 | 驗證方式 |")
    w("|---:|---|---|---|")
    for r in api:
        w("| %d | %s | `%s` | %s |"
          % (r["line"], r["method"], r["path"], r["guard"]))
    w("")
    for m in mounts:
        w("- 靜態掛載 `%s`（第 %d 行，%s）"
          % (m["path"], m["line"], "條件掛載" if m["conditional"] else "固定掛載"))
    w("")
    alias = [r for r in api if r["path"] in ("/query", "/analyze", "/api/analyze")]
    if alias:
        w("⚠ `/query`、`/analyze`、`/api/analyze` 是**同一個 `analyze()` 處理器**的")
        w("三個路徑別名。保護狀態：%s"
          % "、".join("`%s`=%s" % (r["path"], r["guard"])
                      for r in alias))
        w("")

    # ── 常數 ──
    w("## 關鍵常數")
    w("")
    w("| 名稱 | 檔案 | 行號 | 內容 |")
    w("|---|---|---:|---|")
    for rel, name in [
        ("server/main.py", "SAFETY_EVENTS_ENABLED"),
        ("server/main.py", "API_KEY_HEADER"),
        ("server/vision_backend.py", "BACKEND"),
        ("server/vision_backend.py", "READER_TIMEOUT"),
        ("server/module_a/ingredient_matching.py", "NEAR_MISS_MIN_LEN"),
        ("server/module_a/ingredient_matching.py", "NEAR_MISS_MAX_RATIO"),
        ("fog/main.py", "CLOUD_READ_TIMEOUT"),
    ]:
        c = const(rel, name)
        w("| `%s` | `%s` | %s | `%s` |"
          % (name, rel, c["line"] or "（查無）", (c["source"] or "（查無）")[:70]))
    n, line = find_line("fog/queryHandler.ts", r"const CLOUD_TIMEOUT")
    w("| `CLOUD_TIMEOUT` | `fog/queryHandler.ts` | %s | `%s` |"
      % (n or "（查無）", (line or "（查無）")[:70]))
    n, line = find_line("APP/src/constants/endpoints.ts", r"ANALYSIS_TIMEOUT_MS")
    w("| `ANALYSIS_TIMEOUT_MS` | `APP/src/constants/endpoints.ts` | %s | `%s` |"
      % (n or "（查無）", (line or "（查無）")[:70]))
    w("")

    # ── 端點設定 ──
    w("## App 打的位址")
    w("")
    for name in ("FOG_URL", "CLOUD_URL"):
        n, line = find_line("APP/src/constants/endpoints.ts",
                            r"export const %s" % name)
        w("- `%s`：第 %s 行　`%s`" % (name, n or "（查無）", (line or "（查無）")))
    w("")

    # ── 回應契約 ──
    w("## Cloud 回應契約")
    w("")
    try:
        t = read("tests/contract/test_cloud_response_contract.py")
        keys = re.search(r"EXPECTED_TOP_LEVEL_KEYS\s*=\s*\{(.*?)\}", t, re.S)
        ks = sorted(set(re.findall(r'"([^"]+)"', keys.group(1)))) if keys else []
        nf = re.search(r"NUTRITION_FIELDS\s*=\s*\{([^}]*)\}", t)
        nfs = sorted(set(re.findall(r'"([^"]+)"', nf.group(1)))) if nf else []
        w("- 頂層欄位 **%d 個**：%s" % (len(ks), "、".join("`%s`" % k for k in ks)))
        w("- `data.nutrition_facts` **%d 欄**：%s"
          % (len(nfs), "、".join("`%s`" % k for k in nfs)))
        w("")
        w("（來源：`tests/contract/test_cloud_response_contract.py`，"
          "該測試以**相等**比對，多一個少一個都會紅。）")
    except (OSError, AttributeError):
        w("（查無——契約測試讀不到）")
    w("")

    # ── App 實際渲染 ──
    w("## App 實際渲染了什麼")
    w("")
    w("⚠ **這一節與上一節刻意分開。** Cloud 回傳了某個欄位，不代表 App 有畫出來。")
    w("")
    try:
        # ⚠ 掃整個 APP/src，不是只掃 HomeScreen——近似提示畫在
        #   components/IngredientsList.tsx 裡，只看 HomeScreen 會得到假陰性
        #   （本工具第一版就這樣報了「沒有」）。
        hs = ""
        for base, _, fs in os.walk(os.path.join(ROOT, "APP", "src")):
            for f in fs:
                if f.endswith((".tsx", ".ts")) and "__tests__" not in base:
                    hs += io.open(os.path.join(base, f), encoding="utf-8").read()
        for label, pat in [
            ("營養數值表格", r"nutrition_facts\.\w+|nutrition\?\.\w+"),
            ("Nutri-Score 圓環", r"<Gauge"),
            ("計算明細（逐項得分）", r"scoreBreakdownList\.map"),
            ("添加物清單", r"<IngredientsList"),
            ("降階部分結果", r"degradedResult &&"),
            ("近似提示", r"nearMiss"),
        ]:
            hits = len(re.findall(pat, hs))
            w("- %s：%s" % (label, "有（%d 處）" % hits if hits else "**沒有**"))
    except OSError:
        w("（查無）")
    w("")

    # ── 資料庫 ──
    w("## 資料庫（實際連線查詢）")
    w("")
    if db is None:
        w("⚠ **連不上資料庫，本節為空。** 不要從別處補這些數字。")
    else:
        w("| 資料表 | 筆數 |")
        w("|---|---:|")
        for t, c in sorted(db["tables"].items()):
            w("| `%s` | %s |" % (t, c if c is not None else "（查無）"))
        w("")
        w("`products` 欄位：%s"
          % "、".join("`%s`" % c for c in db["products_columns"] if c))
        w("")
        w("⚠ 不存在的表不要寫進文件。`raw_materials` 於 2026-08-06 移除查詢路徑，")
        w("現行資料庫中沒有這張表。")
    w("")

    # ── 版本 ──
    w("## 版本")
    w("")
    try:
        pkg = json.loads(read("APP/package.json"))
        dep = pkg.get("dependencies", {})
        for k in ("expo", "react-native", "react"):
            w("- `%s`：%s" % (k, dep.get(k, "（查無）")))
    except (OSError, ValueError):
        w("- （查無——package.json 讀不到）")
    w("")

    text = "\n".join(out) + "\n"

    if a.out:
        old = ""
        if os.path.exists(a.out):
            old = io.open(a.out, encoding="utf-8").read()
        io.open(a.out, "w", encoding="utf-8").write(text)
        print("已寫入 %s" % a.out)
        if old:
            # 只比事實行，忽略產生時間那一行
            def body(s):
                return [l for l in s.split("\n") if not l.startswith("> 產生時間")]
            o, n = body(old), body(text)
            changed = [l for l in n if l not in o]
            gone = [l for l in o if l not in n]
            if changed or gone:
                print("\n與上次相比有變動（文件若引用過這些，要一起更新）：")
                for l in gone[:20]:
                    print("  - %s" % l[:100])
                for l in changed[:20]:
                    print("  + %s" % l[:100])
            else:
                print("與上次相同。")
    else:
        print(text)


if __name__ == "__main__":
    main()
