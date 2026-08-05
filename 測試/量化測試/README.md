# 影像辨識層量化測試集

回答一個問題：**辨識層從照片上讀到的東西，跟照片上實際有的東西差多少。**

正解（`ground_truth/`）只涵蓋「照片上看得到什麼」——那是人工唯一不可取代的職責。
成分是否為添加物不在此評估，那是查表問題，另於 `eval_official_name_coverage.py`
與 `audit_additive_matches.py` 以官方清單為正解評估。

---

## 目錄

```
測試/量化測試/
├── cases.json                     案例定義（set_version / category / difficulty）
├── manifest.json                  原圖的 SHA256 清單
├── images/                        原圖，依類別分資料夾
│   ├── beverage/       c01_柳橙綠茶/  c02_濃豆漿/  …      18 案
│   ├── prepared_meal/  c08_椰香綠咖哩嫩雞飯/  …           17 案
│   ├── snack/                                             8 案
│   ├── supplement_food/                                   2 案
│   ├── canned_food/  instant_noodle/  non_food/         各 1 案
│   └── （新增類別時直接開資料夾）
├── ground_truth/                  人工正解，同樣依類別分
│   ├── beverage/       c01_柳橙綠茶.json  …
│   ├── …
│   └── _bak_2026-07/              舊版正解備份（底線開頭＝歸檔區，不參與評分）
├── predictions/                   辨識結果，扁平放（機器產物，不需人工瀏覽）
└── results/                       每次執行的產物
    └── _baseline_2026-07-30/      0730 與 0806 報告的歷史數字
```

| 路徑 | 進版控 | 內容 |
|---|---|---|
| `cases.json` | ✅ | 案例定義 |
| `ground_truth/` | ✅ | 48 份人工正解。**唯一不可由程式重生的資產** |
| `predictions/` | ✅ | 已存的辨識結果。離線回測吃這批，不必重打 API |
| `manifest.json` | ✅ | 原圖的 SHA256 清單 |
| `images/` | ❌ | 原圖 98 張、167 MB。由 manifest 保證身分 |
| `results/` | ❌ | 每次執行的產物 |

**為何依類別分資料夾**：人工審核時看的是資料夾，不是打開 JSON 一筆筆找。
分好之後「哪一類幾件、非食品有沒有補夠」用檔案總管即可看出，
補案例時也直接看得到自己在補哪一類。

**代價與防護**：類別因此同時存在於兩處——`cases.json` 的欄位與資料夾位置。
兩者若不同步，會出現「JSON 說是零食、檔案卻在飲料資料夾」的狀態，
人工審核與程式評分看到的分類不同。`casetool.py check` 對此有專門檢查，
改分類時務必兩邊一起改（或用工具搬）。

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

版號格式 **`v<主>.<次>`**，記於 `cases.json` 每一案的 `set_version`。

| 位 | 什麼時候 +1 | 對數字的意義 |
|---|---|---|
| **主版本** | 新增或移除案例 | **不可與其他主版本相比**——母體不同，比的不是同一件事 |
| **次版本** | 同一批案例，但輸入照片或人工正解有修訂 | 案例可一一對應，惟數字仍受影響，引用時須註明修訂內容 |

**已發行的版本不再更動。** 任何改動一律進新版本，舊版數字保留不刪。

理由：測試集一改，前後期數字就不是同一把尺量的。20 → 48 案時已發生過一次，
以致 2026-07-30 報告的延遲（15.9s / 29.1s）與上期（4.8s / 8.4s）不可直接相比——
案例數與擷取欄位同時變動，退步幅度無法歸因。版本化就是為了不再發生第二次。

跨期比較引用 `summary.json` 的 `slices.by_set_version.<版號>`。

### 版本沿革

**每次調版都必須在此記錄改了什麼、為什麼改。** 只有版號而沒有說明，
日後無從判斷兩組數字差在哪。

| 版本 | 日期 | 案例數 | 圖片 | 變更 |
|---|---|---|---|---|
| （未編號） | 2026-07 以前 | 20 | — | 最初的測試集。無版號紀錄，數字僅存於當期報告 |
| **v2.0** | 2026-07-23 | 48 | 98 張 / 167 MB | 擴充至 48 案（47 食品 + 1 非食品）。2026-07-30 報告所用。當時稱 `core48` |
| **v2.1** | 2026-08-06 | 48 | 54 張 / 87.5 MB | 案例不變。移除無實質內容的正面照與 HEIC 重複檔；c35 更換為含成分欄的照片。當時稱 `core48r2` |
| v3.0 | 待補 | 73 | — | 預計新增 15 件非食品與 10 件拍攝條件不佳者，見下方「待補案例清單」 |

`results/_baseline_*/` 內的歷史快照仍使用調版前的舊稱（`core48`、`core48r2`），
對應關係如上表。日後一律使用 `vX.Y`。

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

### 待補案例清單

case_id 已預先指定，拍完照片放進對應資料夾即可照下方指令建檔。
「內容」欄只是舉例，同性質的替代品都可以——重點是**版面結構**，不是特定商品。

#### 非食品 15 張

| # | case_id | 內容 | 難度 |
|---|---|---|---|
| ☐ | `c50_保健食品外盒` | 葉黃素／魚油等膠囊外盒，含營養標示式表格 | 硬 |
| ☐ | `c51_維他命瓶身` | 綜合維他命瓶，含每份含量表 | 硬 |
| ☐ | `c52_成藥外盒` | 感冒藥／胃藥外盒，含成分含量 | 硬 |
| ☐ | `c53_化妝品全成分` | 保養品背標全成分表 | 硬 |
| ☐ | `c54_洗髮精成分` | 洗沐用品成分列表 | 硬 |
| ☐ | `c55_寵物飼料標示` | 飼料袋的營養成分保證分析 | 硬 |
| ☐ | `c56_清潔劑標示` | 洗衣精／清潔劑成分與警語 | 硬 |
| ☐ | `c57_酒精飲料標示` | 啤酒／調酒罐身（食品法規外，但版面極像） | 硬 |
| ☐ | `c58_藥袋` | 醫院藥袋，有藥名與劑量 | 中 |
| ☐ | `c59_餐廳菜單` | 有品名與價格，無標示結構 | 中 |
| ☐ | `c60_發票收據` | 超商收據 | 中 |
| ☐ | `c61_營養品廣告` | 廣告文宣，有數字無標示 | 中 |
| ☐ | `c62_風景照` | 任意風景 | 簡單 |
| ☐ | `c63_人物照` | 任意人物 | 簡單 |
| ☐ | `c64_3C產品` | 手機／筆電外觀 | 簡單 |

硬負例是重點：**它們都有「成分清單」或「含量表格」的版面結構**，
閘門若靠版面判斷就會誤放。誤放後整條管線會把它當食品分析下去。

#### 困難食品 10 張

| # | case_id | 內容 | difficulty |
|---|---|---|---|
| ☐ | `c70_反光鋁袋零食` | 鋁箔包裝零食，正面反光 | `glare` |
| ☐ | `c71_曲面罐身飲料` | 鋁罐營養標示，字沿曲面繞 | `curved` |
| ☐ | `c72_摺痕軟包裝` | 軟袋摺到標示 | `crease` |
| ☐ | `c73_低光便當` | 室內昏暗光線拍便當標示 | `low_light` |
| ☐ | `c74_斜拍飲料` | 約 45 度角拍攝 | `angled` |
| ☐ | `c75_小字複合調理` | 成分密集的調理食品 | `small_text` |
| ☐ | `c76_部分遮擋` | 標示被價格貼紙蓋住一角 | `occluded` |
| ☐ | `c77_反光曲面罐` | 罐身同時反光與彎曲 | `glare,curved` |
| ☐ | `c78_低光小字` | 昏暗光線下的密集成分 | `low_light,small_text` |
| ☐ | `c79_摺痕斜拍` | 軟包裝摺痕加斜角 | `crease,angled` |

最後三張是**複合難度**——實際使用時很少只有單一問題，
單因子案例測得出弱點，複合案例才測得出實用邊界。

### 新增案例的流程

```bash
# 1. 照片放進 images/<category>/<case_id>/（原始解析度，不要先壓縮）
#    從手機匯入時懶得先建類別資料夾的話，暫放 images/<case_id>/ 也可以，
#    下一步的工具會依 --category 代為歸位。

# 2. 建檔。非食品一行完成，正解也一併寫好：
python casetool.py new c50_保健食品外盒 --non-food --desc="某某葉黃素膠囊外盒" \
    --set-version=v3.0

#    食品案例要指定類別與困難度，正解另行填寫：
python casetool.py new c70_反光鋁袋零食 --category=snack \
    --desc="某某洋芋片" --difficulty=glare --set-version=v3.0

#    新增案例改變了案例組成,故屬新的主版本。補完全部 25 件後,
#    既有 48 案的 set_version 也要一併改為 v3.0——它們是同一個母體。

# 3. 食品案例：填 ground_truth/<category>/<case_id>.json
#    照著照片上看得到的填，看不到的留 null——不可填 0，
#    「沒有標示」與「含量為零」是兩件事。

# 4. 更新指紋清單並檢查四處一致
python manifest_tool.py generate
python casetool.py check

# 5. 只跑新案例，確認能跑通
../../server/venv/bin/python run_eval.py c50_保健食品外盒
```

`casetool.py check` 會抓出所有「不會報錯但會靜默算錯」的狀況：
案例有登記卻缺正解（該案被略過，不計入任何指標）、照片路徑對不上、
**資料夾位置與 category 欄位不一致**、category 與正解的 is_food_label
自相矛盾、manifest 過期、difficulty 標籤拼錯。**每次新增完都跑一次。**

### 改某案的分類

三處要一起動：`cases.json` 的 `category`、`images/` 資料夾、`ground_truth/` 資料夾。

```bash
# 例：把 c23_in果凍 從 supplement_food 改成 snack
mv images/supplement_food/c23_in果凍 images/snack/
mv ground_truth/supplement_food/c23_in果凍.json ground_truth/snack/
# 再編輯 cases.json 的 category 與 images 路徑
python manifest_tool.py generate && python casetool.py check
```

漏掉任一處，`check` 會直接報「分類不一致」並指出實際位置。

### 補標既有 48 案的困難度

新案例邊拍邊標，既有 48 案則需逐張看過原圖後補：
直接編輯 `cases.json` 的 `difficulty` 陣列，改完跑 `casetool.py check` 驗證標籤合法。
標完 `by_difficulty` 切片才會有內容，回饋要求的「difficult images 表現」才答得出來。

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
