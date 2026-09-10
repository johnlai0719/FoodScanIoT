#!/usr/bin/env python3
# 產生 rec 合成訓練用的文字語料，並檢查它有沒有污染到評估集。
#
# 為什麼需要這支：rec fine-tune 官方建議 ≥5000 行，真實:合成:通用 ≈ 1:1:1。
# 合成那一份是唯一不需要人工標註就能大量產出的，所以先做它——它能在投入
# 200-300 張 det 標註「之前」就回答「fine-tune 到底有沒有用」。
#
# **語料的來源必須與評估集無關。** 這比「不要拿測試集的圖去訓練」更容易被忽略：
# 拿 c58 的 ingredients_raw 去渲染訓練圖，rec 模型（CTC/attention）會學到那串字
# 的序列先驗，之後在 c58 上的分數就被灌水。這與 score_eval.py 廢止 ingredient_types
# 的理由相同——受測對象與正解同源就不叫評估。
#
# 因此本檔的詞彙只來自兩處，兩者都獨立於評估集：
#   1. 公開法規詞彙（食品添加物標準、營養標示應遵行事項的固定用語）
#   2. `additives` 資料表的 name_zh / aliases（--from-db，需 PostgreSQL 起著）
# 而且「乾淨」不靠宣稱，靠 `check` 子命令實際比對評估集驗證。
#
# 用法：
#   python synth_corpus.py build --n=6000 -o corpus/synth_lines.txt
#   python synth_corpus.py build --n=6000 --from-db -o corpus/synth_lines.txt
#   python synth_corpus.py check corpus/synth_lines.txt
import argparse
import json
import os
import random
import sys
import unicodedata

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_ROOT = os.path.abspath(
    os.path.join(HERE, '..', '測試', '量化測試'))

# ─── 種子詞彙 ────────────────────────────────────────────────────────────────
# 公開法規用語。刻意只收「單一品項名稱」，不收任何成分的排列順序——
# 順序才是會洩漏評估集的東西，單一詞彙是產業共用字彙，重疊無可避免也無害。
ADDITIVES = """
維生素E 混合濃縮生育醇 抗壞血酸鈉 L-抗壞血酸 異抗壞血酸鈉 二丁基羥基甲苯
沒食子酸丙酯 檸檬酸 檸檬酸鈉 蘋果酸 DL-蘋果酸 乳酸 乳酸鈣 醋酸鈉 冰醋酸
酒石酸 酒石酸氫鉀 琥珀酸二鈉 反丁烯二酸 葡萄糖酸-δ內酯 磷酸 磷酸鈣 磷酸鈉
多磷酸鈉 偏磷酸鈉 焦磷酸鐵 焦磷酸鈉 碳酸鈉 碳酸鉀 碳酸氫鈉 碳酸氫銨 碳酸鈣
氯化鉀 氯化鈉 硫酸鎂 葡萄糖酸鋅 葡萄糖酸銅 L-麩酸鈉 L-天門冬酸鈉 胺基乙酸
DL-胺基丙酸 甘胺酸 L-精胺酸 DL-丙胺酸 牛磺酸
5'-次黃嘌呤核苷磷酸二鈉 5'-鳥嘌呤核苷磷酸二鈉 核糖核苷酸鈣
醋酸澱粉 氧化澱粉 磷酸二澱粉 羥丙基澱粉 羥丙基磷酸二澱粉 乙醯化己二酸二澱粉
辛烯基丁二酸鈉澱粉 玉米澱粉 樹薯澱粉 馬鈴薯澱粉 米澱粉 修飾澱粉
三仙膠 玉米糖膠 結蘭膠 刺槐豆膠 羅望子膠 關華豆膠 阿拉伯膠 卡德蘭膠
海藻酸鈉 鹿角菜膠 果膠 明膠 洋菜 葡甘露聚糖 刺梧桐膠 纖維素 羧甲基纖維素鈉
羥丙基甲基纖維素 微晶纖維素
脂肪酸甘油酯 脂肪酸蔗糖酯 脂肪酸聚合甘油酯 大豆卵磷脂 卵磷脂 乳酸硬脂酸鈉
單及雙脂肪酸甘油二乙醯酒石酸酯 聚山梨醇酐脂肪酸酯
赤藻糖醇 木糖醇 山梨醇 D-山梨醇液70% 麥芽糖醇 甘露醇 D-木糖
醋磺內酯鉀 蔗糖素 阿斯巴甜 甜菊醣苷 甘草素 紐甜 甘草萃 糖精鈉
焦糖色素 黃梔子色素 紅椒色素 銅葉綠素 銅葉綠素鈉 β-胡蘿蔔素 蝦紅素
食用紅色六號 食用黃色四號 食用藍色一號 植物性碳黑
麥芽糊精 糊精 環狀糊精 麥芽糖 葡萄糖 葡萄糖漿 果糖 蔗糖 砂糖 高果糖糖漿
還原水飴 轉化液糖 海藻糖 異麥芽寡糖 菊糖 乳糖 寡糖
二氧化矽 二氧化鈦 矽樹脂 硬脂酸鎂 滑石粉
苯甲酸鈉 己二烯酸鉀 去水醋酸鈉 對羥苯甲酸乙酯 丙酸鈣 亞硝酸鈉
乙烯二胺四醋酸二鈉鈣 植酸 聚二甲基矽氧烷
""".split()

FOODS = """
水 食鹽 鹽 碘鹽 砂糖 糖 醬油 醬油粉 味醂 米醋 醋 米酒 酒精 甘油 丙二醇
麵粉 小麥蛋白 小麥纖維 米 糯米 玉米 大豆 黃豆 非基因改造黃豆 大豆蛋白
脫脂奶粉 全脂奶粉 乳粉 乳清蛋白 鮮乳 生乳 奶油 人造奶油 乾酪素鈉 起司粉
棕櫚油 精製棕櫚油 棕櫚仁油 氫化棕櫚仁油 大豆油 精製大豆油 芥花油 精製芥花油
葵花油 葵花籽油 菜籽油 油菜籽油 橄欖油 芝麻油 麻油 豬油 雞油 中鏈三酸甘油酯
食用烤酥油 脂肪抹醬
酵母 酵母抽出物 酵母抽出粉 蛋白質水解物 水解大豆蛋白 魚水解蛋白 大豆蛋白水解物
豬肉 雞肉 牛肉 豬骨 雞骨 豬抽出物 豬肉抽出物 雞肉抽出物 柴魚抽出物 香菇抽出物
魚漿 鱈魚 蝦 全蝦 蛋 雞蛋 蛋黃液 乾燥蛋白 蛋白粉
洋蔥 洋蔥粉 洋蔥抽出粉 蒜 蒜粉 蒜泥 薑 薑母粉 蔥 青蔥 脫水青蔥 紅蔥
胡椒 胡椒粉 黑胡椒 白胡椒 辣椒 辣椒粉 紅辣椒 花椒 青花椒 八角 月桂葉 肉豆蔻
奧勒岡草 羅勒 迷迭香 百里香 鼠尾草 馬郁蘭 洋香菜 五香 咖哩粉 匈牙利甜椒粉
七味唐辛子 芥末 香料 複方香料 天然香料
高麗菜 脫水高麗菜 胡蘿蔔 脫水胡蘿蔔 紅蘿蔔 玉米粒 甜玉米粒 青豆仁 青花菜
青刀豆 洋菇 洋菇片 木耳 脫水木耳 海帶芽 脫水海帶芽 菠菜 脫水菠菜 酸白菜
南瓜 豆腐 豆漿 番茄 蕃茄糊 檸檬 柳丁原汁 蘋果汁 葡萄 紅葡萄果汁 芒果 芭樂
茉莉綠茶 紅茶 綠茶 烏龍茶 茶抽出物 咖啡 可可粉 奶精 椰漿粉
花生 花生醬 杏仁 杏仁醬 芝麻 白芝麻 黑芝麻 堅果 燕麥粉 海苔 海苔細片
乳酸菌 活性乳酸菌 膳食纖維 貝殼鈣 甘蔗多酚萃取物 甘草抽出物 辣椒抽出物
蔬菜 綜合蔬菜 芹菜 西芹 豌豆 豌豆仁 四季豆 火腿 培根 香腸 熱狗 貢丸 魚丸
豆瓣醬 陳年豆瓣醬 沙茶醬 沙拉醬 蕃茄醬 甜麵醬 味噌 豆豉 辣豆瓣醬
柑橘 柑橘果膠 柳橙 柳橙汁 橙皮 檸檬汁 葡萄柚 鳳梨 芒果汁 蘋果 水蜜桃
高湯 雞高湯 豬骨高湯 昆布高湯 柴魚片 小蘇打 碳酸氫鈉 泡打粉
炊飯油 鬆餅粉 起酥油 酥油 白油 墨西哥椒 墨西哥紅醬 帕瑪森起司 摩佐起司
糙米 胚芽米 黑糯米 珍珠大麥 燕麥 薏仁 紅豆 綠豆 花豆 鷹嘴豆
""".split()


# 食品添加物的**功能類別詞**。來源是衛福部《食品添加物使用範圍及限量暨規格標準》
# 定義的 17 類，是公開法規、與評估集無關。
#
# 為什麼要單獨一份：第一版的詞表只收物質名（混合濃縮生育醇、多磷酸鈉），
# 把整類功能詞漏光了。實測「劑」在評估集的成分文字裡出現 99 次、語料 0 次——
# 而真實標示的巢狀寫法幾乎都是「功能類別詞(物質1、物質2…)」的形狀，
# 少了它等於少了成分表最常見的句法骨架。
FUNCTIONAL = """
防腐劑 殺菌劑 抗氧化劑 漂白劑 保色劑 膨脹劑 營養添加劑 著色劑 調味劑 黏稠劑
粘稠劑 結著劑 乳化劑 溶劑 香料 糊料 酸味劑 甜味劑 品質改良劑 品質改良用劑
釀造用劑 食品製造用劑 複方抗氧化劑 複方乳化劑 複方調味劑 複方香料 複方品質改良劑
複方甜味劑 食品用複方粉 維生素 礦物質
""".split()

# 包裝上高頻的**構詞骨架**。這些不是單詞而是型態，缺了它們語料就只有孤立詞彙、
# 沒有真實標示的組合方式。{} 由 gen_ingredient_line 填入。
PATTERNS = ['{}風味粉', '{}風味油', '{}抽出物', '{}抽出粉', '{}萃取物',
            '精緻{}', '精製{}', '脫水{}', '乾燥{}', '濃縮{}', '水解{}',
            '{}粉', '{}醬', '{}油', '{}汁']

# 營養標示與包裝上的固定句式（食品標示應遵行事項的用語）
NUTRI_TERMS = """
營養標示 每一份量 本包裝含 每份 每100公克 每100毫升 熱量 蛋白質 脂肪
飽和脂肪 反式脂肪 碳水化合物 糖 膳食纖維 鈉 膽固醇 乳糖 大卡 公克 毫克 公升 毫升
""".split()
BOILERPLATE = [
    '品名：{name}', '原料：{ing}', '成分：{ing}', '成份：{ing}',
    '內容量：{n}公克', '淨重：{n}公克', '容量：{n}毫升',
    '有效日期標示於封口處', '有效日期：標示於包裝上（西元年月日）',
    '保存期限：{n}個月', '保存期限{n}天（係指未開封前可保存天數）',
    '保存方法：請置於陰涼乾燥處，避免陽光直射',
    '開封後請儘速食用完畢', '開封後須冷藏於7℃以下並儘速飲用完畢',
    '原產地：臺灣', '製造地點：臺灣', 'Made In Taiwan',
    '過敏原資訊：本產品含{a}，對其過敏者不宜食用',
    '本產品含{a}及其製品', '本產線亦生產含{a}之產品',
    '消費者服務專線：0800-{d}-{d}', '製造廠：{name}股份有限公司',
    '本產品符合國家標準CNS{d}', '本廠通過HACCP認證',
    '每一份量{n}公克　本包裝含{k}份',
    '熱量 {n}大卡　蛋白質 {f}公克　脂肪 {f}公克',
    '碳水化合物 {f}公克　糖 {f}公克　鈉 {n}毫克',
]
ALLERGENS = ['牛奶', '大豆', '花生', '堅果類', '蛋', '魚類', '甲殼類',
             '含麩質之穀物', '芝麻', '螺貝類', '芒果']
BRANDS = ['統一', '光泉', '味全', '義美', '愛之味', '黑松', '維力', '維他露',
          '泰山', '南僑', '聯華', '大成', '福壽', '桂格']


def _sep(rng):
    """包裝上的分隔符不統一，訓練資料要跟著混，否則模型只學會其中一種。"""
    return rng.choice(['、', '、', '、', '，', '·', ', '])


# 目標長度分布，取自 out/v5_hires 的 3931 條真實偵測行：
#   p50=5、p75=11、p90=20、p95=26、p99=34、max=47，>25 字者僅 5.4%
# 第一版沒對這件事，語料 median 21 / p90 49，比真實長一倍以上，而且
# 43% 超過 config 的 max_text_length=25——那些樣本會在訓練時被整筆丟棄。
# rec 拿到的是「det 切好的單行」，不是整段成分表；成分表在圖上本來就被
# 拆成很多短行。分布不對，等於一半的算力花在不存在的樣本型態上。
LEN_BUCKETS = [(1, 4, 26), (5, 8, 24), (9, 14, 21),
               (15, 20, 15), (21, 26, 9), (27, 34, 4), (35, 47, 1)]


def sample_target_len(rng):
    lo, hi, _ = rng.choices(LEN_BUCKETS, weights=[b[2] for b in LEN_BUCKETS])[0]
    return rng.randint(lo, hi)


def _trim_to(line, target, seps='、，,·'):
    """截到不超過 target，且切在分隔符邊界——切在詞中間會產生不存在的半截詞。"""
    if len(line) <= target:
        return line
    cut = -1
    for i, ch in enumerate(line[:target + 1]):
        if ch in seps:
            cut = i
    return line[:cut] if cut > 0 else line[:target]


def gen_ingredient_line(rng, vocab):
    """組一段像成分表的字串。

    順序是隨機抽的，刻意不模仿任何真實商品的配方順序——那正是會洩漏評估集的東西。
    """
    n = rng.choices([2, 3, 4, 5, 6, 8, 10, 14], weights=[3, 5, 6, 6, 5, 4, 3, 2])[0]
    parts = []
    for _ in range(n):
        r = rng.random()
        if r < 0.12:
            # 構詞骨架：豬肉風味粉、精緻棕櫚油、脫水高麗菜。
            # 真實標示大量使用這種組合，只有孤立詞彙練不到。
            w = rng.choice(PATTERNS).format(rng.choice(vocab))
        else:
            w = rng.choice(vocab)
        # 包裝上常見的巢狀寫法：括號展開。
        # **頭部優先用功能類別詞**——真實標示是「抗氧化劑(混合濃縮生育醇)」，
        # 不是「棕櫚油(混合濃縮生育醇)」。第一版頭部只從物質名抽，
        # 所以「劑」在語料裡一次都沒出現過。
        if rng.random() < 0.18:
            inner = _sep(rng).join(rng.sample(vocab, k=rng.randint(2, 4)))
            br = rng.choice([('(', ')'), ('（', '）'), ('[', ']'), ('{', '}')])
            head = rng.choice(FUNCTIONAL) if rng.random() < 0.7 else w
            w = f"{head}{br[0]}{inner}{br[1]}"
        parts.append(w)
    line = _sep(rng).join(parts)
    if rng.random() < 0.25:
        line = rng.choice(['成分：', '原料：', '成份：', '麵：', '調味粉包：',
                           '調味油包：', '其他成分：']) + line
    return line


def gen_boilerplate(rng, vocab):
    t = rng.choice(BOILERPLATE)
    return (t.replace('{name}', rng.choice(BRANDS))
             .replace('{ing}', _sep(rng).join(rng.sample(vocab, k=rng.randint(2, 5))))
             .replace('{a}', _sep(rng).join(rng.sample(ALLERGENS, k=rng.randint(1, 4))))
             .replace('{n}', str(rng.randint(1, 999)))
             .replace('{k}', str(rng.randint(1, 6)))
             .replace('{f}', f"{rng.uniform(0, 99):.1f}")
             .replace('{d}', str(rng.randint(100, 999))))



# ── 營養標示：版面規格與真實數值 ─────────────────────────────────────────────
# **規格來源**：github.com/c711cat/nutrition_label_tool（Vue，依 TFDA 規定做的
# 台灣營養標示產生器）。抄的是**版面規則**不是圖——rec 吃的是 det 切好的一行，
# 整張表的圖對它沒用；而該工具是 DOM 渲染，截圖只會得到乾淨的螢幕畫面，
# 沒有印刷網點／反光／曲面，那正是 synth_render.py 花力氣加的東西。
#
# 從 `CustomNutritionLabel.vue` 與 `ProductList.vue` 抄到的規則：
#   表頭順序   營養標示 → 每一份量 X 公克 → 本包裝含 N 份 → （空白）每份 每100公克
#   欄位順序   熱量／蛋白質／脂肪／[縮排]飽和脂肪／[縮排]反式脂肪／
#              碳水化合物／[縮排]糖／鈉
#   縮排       飽和脂肪、反式脂肪、糖要縮排在上一層底下（`indent: true`）
#   小數       值為 0 印 "0"（toFixed(0)），否則一位小數（toFixed(1)）
#   每份換算   每份 = 每100公克 ÷ 100 × 每一份量
#
# **數值來源**：衛福部食品營養成分資料庫 2023（該工具內附的 fooddata2023.json，
# 2171 筆，每 100g 基準），抽成 `data/fooddata2023_per100g.json`。
# 用真實食品的數值而不是亂數——真實營養值有相關性（高脂通常高熱量、蔬菜低鈉），
# 舊版 `gen_nutri_fragment` 產的 `蛋白質 878.4公克`、`熱量 0.3大卡` 在標示上不存在。
#
# **與評估集無關**：這份是公開的政府資料庫，不是 ground_truth。

NUTRI_ROWS = [
    ('熱量', '大卡', False, 'calories'),
    ('蛋白質', '公克', False, 'protein'),
    ('脂肪', '公克', False, 'fat'),
    ('飽和脂肪', '公克', True, 'saturated_fat'),
    ('反式脂肪', '公克', True, 'trans_fat'),
    ('碳水化合物', '公克', False, 'carbohydrates'),
    ('糖', '公克', True, 'sugar'),
    ('鈉', '毫克', False, 'sodium'),
]
_FOODDATA = None


def fooddata():
    global _FOODDATA
    if _FOODDATA is None:
        p = os.path.join(HERE, 'data', 'fooddata2023_per100g.json')
        _FOODDATA = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else []
    return _FOODDATA


def _fmt(v):
    """TFDA 的印法：0 印 "0"，否則一位小數（抄自 ProductList.vue 的 toFixed）。"""
    if v is None:
        return None
    return '0' if abs(v) < 0.05 else '%.1f' % v


def gen_nutri_table(rng):
    """產一整張營養標示，回傳**逐行**的字串清單。

    det 會把表格切成一行一行，所以這裡也逐行產出，與 rec 拿到的形狀一致。
    每一列是「欄位名 ＋ 每份 ＋ 每100公克」——**舊版完全沒有這種型態**，
    它只會產「熱量」或「56.1公克」單獨一行，而兩欄並排正是 bench_parse
    序列對齊最容易出錯的地方。
    """
    data = fooddata()
    if not data:
        return [rng.choice(NUTRI_TERMS)]
    f = rng.choice(data)
    # ⚠ 份量必須依食品類別挑，不能獨立隨機。第一版兩者各自亂數，
    # 配出「每一份量275毫升／熱量1151.5大卡／碳水236.9公克」——
    # 275 毫升的東西不可能有那個熱量。錯的數量關係會教模型錯的先驗。
    cat = f.get('cat') or ''
    if any(k in cat for k in ('乳品', '飲料', '油脂')):
        unit = '毫升' if rng.random() < 0.8 else '公克'
        per = rng.choice([200, 250, 275, 300, 330, 350, 400, 450, 500, 650])
    elif any(k in cat for k in ('糕餅', '點心', '糖果', '堅果', '穀物')):
        unit = '公克'
        per = rng.choice([20, 25, 25.5, 30, 35, 40, 50, 59.5, 60])
    elif any(k in cat for k in ('加工調理', '肉類', '魚貝', '蛋類')):
        unit = '公克'
        per = rng.choice([80, 100, 107, 110, 120, 150, 200, 250])
    else:
        unit = '公克'
        per = rng.choice([30, 50, 60, 100, 120, 150, 200])
    qty = rng.choice([1, 1, 1, 2, 2, 3, 4, 5, 6])
    # 反式脂肪資料庫沒有，真實標示上絕大多數是 0
    trans = 0.0 if rng.random() < 0.9 else rng.uniform(0.1, 0.6)

    out = ['營養標示',
           '每一份量%g%s' % (per, unit),
           '本包裝含%d份' % qty,
           rng.choice(['每份　每100%s' % unit, '每份 每100%s' % unit,
                       '　　每份　每100%s' % unit])]
    for label, u, indent, key in NUTRI_ROWS:
        v100 = trans if key == 'trans_fat' else f.get(key)
        if v100 is None:
            continue
        a, b = _fmt(v100 / 100 * per), _fmt(v100)
        pad = '　' if indent else ''
        # 三種寫法：兩欄並排（最常見）／只有每份／只有每100，真實標示三種都有
        r = rng.random()
        if r < 0.7:
            out.append('%s%s %s%s %s%s' % (pad, label, a, u, b, u))
        elif r < 0.85:
            out.append('%s%s %s%s' % (pad, label, a, u))
        else:
            out.append('%s%s %s%s' % (pad, label, b, u))
    return out


def gen_nutri_fragment(rng):
    """營養表的欄位名與數值——這是版面重建之外，rec 也要讀準的部分。"""
    if rng.random() < 0.5:
        return rng.choice(NUTRI_TERMS)
    v = f"{rng.uniform(0, 999):.1f}" if rng.random() < 0.7 else str(rng.randint(0, 2500))
    return v + rng.choice(['大卡', '公克', '毫克', '毫升', '公升', '%'])


def load_db_vocab():
    """從 additives 表補充詞彙。DB 沒起來就跳過，不讓它擋住整條管線。"""
    try:
        import psycopg2
    except ImportError:
        print('[略過 DB] 未安裝 psycopg2')
        return []
    env = {}
    p = os.path.join(HERE, '..', 'server', '.env')
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    try:
        conn = psycopg2.connect(
            host=env.get('DB_HOST', 'localhost'), port=env.get('DB_PORT', 5432),
            dbname=env.get('DB_NAME', 'product_db'), user=env.get('DB_USER', 'postgres'),
            password=env.get('DB_PASSWORD', ''), connect_timeout=3)
    except Exception as e:
        print(f'[略過 DB] 連不上：{str(e).strip().splitlines()[0]}')
        return []
    out = []
    with conn, conn.cursor() as cur:
        cur.execute('SELECT name_zh, aliases FROM additives')
        for name, aliases in cur.fetchall():
            if name:
                out.append(name.strip())
            if aliases:
                items = aliases if isinstance(aliases, list) else str(aliases).split(',')
                out += [a.strip() for a in items if a and a.strip()]
    conn.close()
    print(f'[DB] 取得 {len(out)} 個詞彙')
    return out


# ─── 污染檢查 ────────────────────────────────────────────────────────────────
def _norm(s):
    return ''.join(c for c in unicodedata.normalize('NFKC', s or '') if c.isalnum())


def load_eval_reference():
    """把評估集的正解字串收集起來，作為「不可重現」的對照。"""
    raws, seqs = [], []
    gt_dir = os.path.join(EVAL_ROOT, 'ground_truth')
    for cat in sorted(os.listdir(gt_dir)):
        d = os.path.join(gt_dir, cat)
        if not os.path.isdir(d) or cat.startswith('_'):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.json'):
                continue
            g = json.load(open(os.path.join(d, fn), encoding='utf-8'))
            if g.get('ingredients_raw'):
                raws.append((fn[:-5], _norm(g['ingredients_raw'])))
            lst = [_norm(x) for x in (g.get('ingredients_list') or []) if _norm(x)]
            if len(lst) >= 3:
                seqs.append((fn[:-5], lst))
    return raws, seqs


def _kgrams(s, k):
    return {s[i:i + k] for i in range(len(s) - k + 1)} if len(s) >= k else set()


def _tokens(s):
    """把一行語料切成成分項。分隔符與 gen_ingredient_line 用的那組一致。"""
    out, buf, depth = [], [], 0
    for ch in s:
        if ch in '([{（［｛':
            depth += 1
        elif ch in ')]}）］｝':
            depth = max(0, depth - 1)
        if depth == 0 and ch in '、，,·':
            if buf:
                out.append(''.join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        out.append(''.join(buf))
    return [t for t in (_norm(x) for x in out) if t]


def check(path, char_thresh=25, seq_len=3):
    """兩道檢查：

    1. **連續成分序列**——語料裡若出現評估集某案「連續 3 個成分」的相同順序，
       那就是把配方順序抄過來了。單一成分重疊無所謂（產業共用字彙），
       順序才是洩漏。
    2. **長字元重疊**——超過 char_thresh 個字完全相同，不論是否成序列都可疑。
       門檻設 25 是因為單一長成分名（如「抗氧化劑混合濃縮生育醇(含精製芥花油)」）
       本身就有近 20 字，設太低會被自身合法重疊洗版。
    """
    raws, seqs = load_eval_reference()
    lines = [l.rstrip('\n') for l in open(path, encoding='utf-8') if l.strip()]
    print(f'語料 {len(lines)} 行，對照評估集 {len(raws)} 案\n')

    # 兩張指紋表。用 k-gram 集合而非兩兩比對：
    # 逐對算最長共同子字串是 O(語料行數 × 案例數 × 行長 × 正解長)，6000 行時
    # 是 10^10 量級跑不完。「存在長度 ≥ k 的共同子字串」等價於「兩者的 k-gram
    # 集合有交集」，改成雜湊查表後是 10^5 量級。
    seq_fp = {}                      # 連續 seq_len 個成分（順序洩漏）
    for cid, lst in seqs:
        for i in range(len(lst) - seq_len + 1):
            seq_fp.setdefault(tuple(lst[i:i + seq_len]), cid)
    char_fp = {}                     # 長度 char_thresh 的字元 k-gram
    for cid, raw in raws:
        for g in _kgrams(raw, char_thresh):
            char_fp.setdefault(g, cid)

    seq_hits, char_hits = [], []
    for n, line in enumerate(lines, 1):
        toks = _tokens(line)
        for i in range(len(toks) - seq_len + 1):
            cid = seq_fp.get(tuple(toks[i:i + seq_len]))
            if cid:
                seq_hits.append((n, cid, line[:60]))
                break
        nl = _norm(line)
        for g in _kgrams(nl, char_thresh):
            cid = char_fp.get(g)
            if cid:
                char_hits.append((n, cid, line[:60]))
                break

    print(f'① 連續 {seq_len} 個成分的順序重疊：{len(seq_hits)} 行')
    for n, cid, s in seq_hits[:10]:
        print(f'     行{n} ← {cid}：{s}')
    print(f'② 連續 {char_thresh} 字以上重疊：{len(char_hits)} 行')
    for n, cid, s in char_hits[:10]:
        print(f'     行{n} ← {cid}：{s}')

    bad = len(set(x[0] for x in seq_hits + char_hits))
    if bad:
        print(f'\n[警告] {bad} 行可能污染評估集，建議剔除後再訓練。')
        return 1
    print('\n[OK] 未偵測到與評估集的序列或長字串重疊。')
    return 0


def load_gemini_vocab():
    """`gen_vocab.py` 用 Gemini 產的詞彙（`data/vocab_gemini.json`）。

    **為什麼加這一個來源**：既有詞表只有 ADDITIVES 144 ＋ FOODS 238，
    而 2026-08-18 的微調結論是「補詞彙有效但有上限」——字次涵蓋率
    92.7%→97.9% 之後邊際效益就小了。要再往上只能擴充詞彙的**種類**，
    尤其是多字化學名（`L-抗壞血酸棕櫚酸酯`、`DL-α-生育醇` 那種），
    那正是 rec 最容易讀錯的地方。

    **來源與評估集無關**：Gemini 的預訓練語料是公開的食品標示與法規，
    這一支不讀 `ground_truth/` 的任何欄位。但「無關」不靠宣稱——
    產完語料一定要跑 `check` 子命令實際比對（順序洩漏 ＋ 長片段重疊）。
    """
    p = os.path.join(HERE, 'data', 'vocab_gemini.json')
    if not os.path.exists(p):
        return []
    return [t for t in json.load(open(p, encoding='utf-8'))
            if 2 <= len(t) <= 24]


def build(n, out, from_db, seed, with_gemini=True, nutri_frac=0.22):
    rng = random.Random(seed)
    vocab = ADDITIVES + FOODS
    if from_db:
        vocab = sorted(set(vocab + load_db_vocab()))
    if with_gemini:
        g = load_gemini_vocab()
        if g:
            before = len(set(vocab))
            vocab = sorted(set(vocab) | set(g))
            print(f'併入 Gemini 詞彙 {len(g)} 個（新增 {len(vocab) - before}）')
    print(f'詞彙 {len(vocab)} 個，產生 {n} 行')

    lines, seen = [], set()
    guard = 0
    # 營養標示以**整張表**為單位產出（gen_nutri_table 回逐行），因為欄位名與
    # 兩欄數值的對應關係只有在整張表裡才成立。
    # 比例：舊語料裡「含欄位名的營養列」只有 2%，而 09-02 的微調實測
    # 唯一變差的就是營養（漏 27→30 格）——模型 80% 的訓練量在成分。
    # 目標拉到 ~22%，成分仍是主體。
    # ⚠ **表頭行不去重。** 第一版沿用了 seen 集合，結果「營養標示」在 8000 行
    # 語料裡只剩 **1 行**、「本包裝含N份」只剩 7 行——它們每張表都字面相同，
    # 去重只留第一次。而那幾行正是 bench_parse 用來定位表格起點與欄位對應的，
    # 模型幾乎沒看過。營養素列因為數值各異所以沒事（1806 行）。
    NO_DEDUP = ('營養標示', '每一份量', '本包裝含', '每份')
    n_nutri = int(n * nutri_frac)
    while len(lines) < n_nutri and guard < n * 60:
        guard += 1
        for s_ in gen_nutri_table(rng):
            s_ = s_.strip()
            if not (1 <= len(s_) <= 34):
                continue
            if any(s_.startswith(k) or s_.lstrip('　').startswith(k)
                   for k in NO_DEDUP):
                lines.append(s_)               # 表頭：允許重複
            elif s_ not in seen:
                seen.add(s_)
                lines.append(s_)
    print(f'營養標示 {len(lines)} 行（{100 * len(lines) / n:.0f}%）')

    while len(lines) < n and guard < n * 60:
        guard += 1
        target = sample_target_len(rng)
        r = rng.random()
        if target <= 8:
            # 短行在真實資料佔一半：數值、單位、單一欄位名、單一成分
            s = gen_nutri_fragment(rng) if r < 0.55 else rng.choice(vocab)
        elif r < 0.62:
            s = _trim_to(gen_ingredient_line(rng, vocab), target)
        elif r < 0.85:
            s = _trim_to(gen_boilerplate(rng, vocab), target)
        else:
            s = _trim_to(gen_ingredient_line(rng, vocab), target)
        s = s.strip().strip('、，,·')
        # 上限對齊 config 的 max_text_length=25 再放寬一點：超過的樣本
        # CTCLabelEncode 會回 None 而整筆跳過，留著只是製造無效樣本
        if not (1 <= len(s) <= 34) or s in seen:
            continue
        seen.add(s)
        lines.append(s)

    os.makedirs(os.path.dirname(os.path.abspath(out)) or '.', exist_ok=True)
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'已寫入 {out}（{len(lines)} 行）')
    print(f'長度分布：min {min(map(len, lines))} / '
          f'median {sorted(map(len, lines))[len(lines) // 2]} / max {max(map(len, lines))}')
    print('\n範例：')
    for s in lines[:8]:
        print('  ', s)
    print(f'\n下一步：python synth_corpus.py check {out}')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--n', type=int, default=6000)
    b.add_argument('-o', '--out', default=os.path.join(HERE, 'corpus', 'synth_lines.txt'))
    b.add_argument('--from-db', action='store_true')
    b.add_argument('--nutri-frac', type=float, default=0.22,
                   help='營養標示行數佔比')
    b.add_argument('--no-gemini', action='store_true',
                   help='不併入 data/vocab_gemini.json')
    b.add_argument('--seed', type=int, default=20260815)
    c = sub.add_parser('check')
    c.add_argument('path')
    c.add_argument('--char-thresh', type=int, default=25)
    c.add_argument('--seq-len', type=int, default=3)
    a = ap.parse_args()
    if a.cmd == 'build':
        build(a.n, a.out, a.from_db, a.seed, not a.no_gemini, a.nutri_frac)
    else:
        sys.exit(check(a.path, a.char_thresh, a.seq_len))


if __name__ == '__main__':
    main()
