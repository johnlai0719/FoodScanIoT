# 影像辨識層量化測試集

回答一個問題：**辨識層從照片上讀到的東西，跟照片上實際有的東西差多少。**

正解（`ground_truth/`）只涵蓋「照片上看得到什麼」——那是人工唯一不可取代的職責。
成分是否為添加物不在此評估，那是查表問題，另於 `eval_official_name_coverage.py`
與 `audit_additive_matches.py` 以官方清單為正解評估。

---

## 目錄

| 路徑 | 進版控 | 內容 |
|---|---|---|
| `cases.json` | ✅ | 案例定義。含 `set_version` / `category` / `difficulty` |
| `ground_truth/` | ✅ | 48 份人工正解。**唯一不可由程式重生的資產** |
| `predictions/` | ✅ | 已存的辨識結果。離線回測吃這批，不必重打 API |
| `manifest.json` | ✅ | 原圖的 SHA256 清單 |
| `images/` | ❌ | 原圖 98 張、167 MB。由 manifest 保證身分 |
| `results/` | ❌ | 每次執行的產物 |

原圖不進版控，但**測試集的可重現性不因此打折**：`manifest.json` 記下每張圖的
SHA256，圖片本體另行傳遞（GitHub Release 附件或校內雲端硬碟），取得後放回
`images/` 跑一次 `verify` 即可確認與歷次執行用的是同一批照片。

---

## 怎麼跑

```bash
cd 測試/量化測試

# 0. 先確認測試集完整（不符就別比數字）
python manifest_tool.py verify

# 1. 跑辨識（會打 Gemini API，要錢）
../../server/venv/bin/python run_eval.py                      # 全部
../../server/venv/bin/python run_eval.py --only-version=core48 # 只跑凍結子集
../../server/venv/bin/python run_eval.py --repeat=3            # 每案 3 次，量穩定度
../../server/venv/bin/python run_eval.py --tag=compressed_1280 # 為本次執行命名

# 2. 評分（離線，不花錢）
../../server/venv/bin/python score_eval.py
```

`EVAL_IMAGE_ROOT` 環境變數可改讀別處的圖片——影像壓縮實驗就是用這個切換壓縮前後，
不必另寫一套 harness。

**產物**：`results/summary.json`（含切片）、`results/samples.jsonl`（逐次原始樣本）、
`results/per_case.csv`、`results/mismatches.csv`、`results/latency_vision.json`。

---

## 測試集版本

**`core48` 永久凍結**：不增、不減、不換圖。跨期比較一律只引用
`summary.json` 的 `slices.by_set_version.core48`。

理由：測試集一改，前後期數字就不再是同一把尺量的。20 → 48 案時已發生過一次，
以致 2026-07-30 報告的延遲（15.9s / 29.1s）與上期（4.8s / 8.4s）實際上不可直接相比——
案例數與欄位數同時變動，退步幅度無法歸因。凍結子集就是為了不再發生第二次。

新增案例一律標 `v3` 以後的版本，與 core48 分開報告。

---

## 目前的組成與已知缺口

依 `category` 切片（2026-08-05）：

| 類別 | 案數 |
|---|---|
| beverage | 18 |
| prepared_meal | 17 |
| snack | 8 |
| supplement_food | 2 |
| canned_food / instant_noodle / **non_food** | 各 1 |

**最急迫的缺口是非食品只有 1 案。** 「食品與非食品判斷全對」目前建立在
n=1 的負例上，統計上說不了什麼——相關性閘門的 precision 實際上未被檢驗過。

`difficulty` 目前 48 案全為空陣列。**這是誠實的未知，不是「無困難」**：
這批照片拍攝時未記錄拍攝條件，事後補標需要有人逐張看過原圖。標註完成前，
`slices.by_difficulty` 不會有內容。

---

## v3 擴充目標

### 非食品 15 張

簡單負例（風景、收據）測不出東西，真正的風險在**看起來像食品標示的非食品**。
比例刻意偏向硬負例：

| 難度 | 張數 | 類型 | 為何選它 |
|---|---|---|---|
| 硬 | 8 | 保健食品／藥品外盒、化妝品全成分表、寵物食品、清潔劑標示 | 有營養標示或成分清單的版面結構，閘門最可能誤放。誤放後整條管線會把它當食品分析下去 |
| 中 | 4 | 藥袋、菜單、收據、營養補充品廣告 | 有文字有數字，但無標示結構 |
| 簡單 | 3 | 風景、人物、3C 產品 | 基準線 |

硬負例與現有的 `supplement_food`（是食品、但以保健食品形態呈現，如膠囊飲、
礦物質果凍）恰好是一線之隔的兩側，兩者併看才知道閘門的邊界畫在哪。

### 困難食品 10 張

逐張標 `difficulty`：`glare` 反光／`curved` 曲面／`crease` 摺痕／
`low_light` 低光／`angled` 斜拍／`small_text` 小字密集／`occluded` 遮擋。

優先補反光鋁袋、曲面罐身、摺痕軟包裝、小字密集的複合調理食品——
前三者是實際使用時最常見的拍攝情境，最後一者是目前 F1 最低的類別
（`prepared_meal` 0.41 / `instant_noodle` 0.23）。

### 拍攝規範

1. **以原始解析度拍攝並保留原圖。** 壓縮由 harness 用 `EVAL_IMAGE_ROOT` 做，
   這樣同一批照片能同時服務辨識評估與壓縮實驗，不必拍兩次。
2. 一案一資料夾：`images/<case_id>/`，檔名描述內容（如 `nutrition_zh.jpg`）。
3. 拍攝當下就記下 `difficulty` 標籤——事後回想不準，而這個標籤是
   「difficult images 表現如何」唯一的依據。
4. 非食品案例的正解只需 `{"is_food_label": false}`。

### 新增案例的流程

```bash
# 1. 照片放進 images/<case_id>/
# 2. cases.json 加一筆，set_version 標 "v3"
# 3. ground_truth/<case_id>.json 人工建立
# 4. 更新 manifest
python manifest_tool.py generate

# 5. 只跑新案例，確認能跑通
../../server/venv/bin/python run_eval.py <case_id>
```

---

## 相關腳本

| 腳本 | 用途 | 花錢 |
|---|---|---|
| `run_eval.py` | 跑辨識，記錄延遲／token／payload 大小 | ✅ Gemini API |
| `score_eval.py` | 對正解評分，輸出整體與切片 | ❌ |
| `manifest_tool.py` | 圖片完整性清單的產生與驗證 | ❌ |
| `eval_ingredient_parse.py` | 「模型整理的清單」vs「自行解析原文」回測 | ❌ 需本機 DB |
| `eval_official_name_coverage.py` | 官方品名出現在標示上時是否被認出 | ❌ 需本機 DB |
| `audit_additive_matches.py` | 全量判定稽核＋相似度漏配偵測 | ❌ 需本機 DB |
