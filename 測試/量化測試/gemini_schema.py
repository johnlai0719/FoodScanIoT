#!/usr/bin/env python3
# Gemini 結構化輸出的契約：把 server/main.py 那段 prompt 裡「用散文寫的 JSON 形狀」
# 換成 Pydantic 模型，交給 response_schema 做約束解碼。
#
# 動機（量出來的）：那段 prompt 共 3207 字元／78 行，其中**1590 字元／38 行純粹是
# JSON 形狀描述**，佔一半。它每次呼叫都要重送、重新被理解一次
# （tokens_prompt 中位 1818，其中六到七成是這段固定文字）。
#
# ⚠ **這解決的是格式與成本，不是幻覺。** 2026-08-19 的量測裡 48 案 0 案格式失敗，
# 格式從來不是失效點；c30 那 23 個編造出來的添加物每一個都是 schema-valid 的字串。
# 約束解碼約束語法樹，不約束事實。真正可能有關的是 temperature 與 thinking。
#
# 但 schema 有一個**規格**上的真價值：`Field(description=...)` 是放
# 「巢狀配方整團保留成一項」這條慣例的地方。Gemini 多判的 122 個添加物裡有 57 個
# 只是它把巢狀拆開造成的粒度差異——那不是辨識錯誤，是規格沒講清楚。
#
# ⚠ **一處與線上契約不相容**：`nutrition_other` 線上是「營養素名當鍵」的動態字典，
# 而 Gemini 的 response_schema 走 OpenAPI 3.0 subset，**表達不了任意鍵的 map**。
# 這裡改成物件陣列。要正式採用的話，main.py:936 讀 nutrition_other 那段要跟著改，
# 或在這層轉回字典。評估腳本不讀這欄，不影響本次比較。
from typing import Optional

from pydantic import BaseModel, Field

# 台灣營養標示的八個強制項目 ＋ 自願標示的膳食纖維。
#
# ⚠ **schema 不計入 prompt token**（2026-08-20 實測：tokens_prompt 1818 → 659，
# 而 schema 序列化後有 1810 token）。先前用字元數推估「schema 也要送所以更貴」是錯的。
# 但 description 仍要節制：它是模型真的會讀的規格，冗長會稀釋重點。共通規則
# （null vs 0）寫在 SYSTEM_INSTRUCTION 裡一次就好，這裡每個欄位只留單位
# ——寫成 `description` 會被複製 18 份
# （nutrition 與 nutrition_per_serving 各展開一次，每次九欄）。


class Nutrition(BaseModel):
    calories: Optional[float] = Field(None, description="熱量，大卡")
    protein: Optional[float] = Field(None, description="蛋白質，公克")
    fat: Optional[float] = Field(None, description="脂肪，公克")
    saturated_fat: Optional[float] = Field(None, description="飽和脂肪，公克")
    trans_fat: Optional[float] = Field(None, description="反式脂肪，公克")
    carbohydrates: Optional[float] = Field(None, description="碳水化合物，公克")
    sugar: Optional[float] = Field(None, description="糖，公克")
    fiber: Optional[float] = Field(
        None, description="膳食纖維，公克。自願標示，多數標示沒有這一列")
    sodium: Optional[float] = Field(None, description="鈉，毫克")


class OtherNutrient(BaseModel):
    """九項以外、但出現在營養標示表格裡的營養素。"""
    name: str = Field(description="營養素名稱，照標示原文，如 鈣／鐵／維生素C／膽固醇")
    per_100: Optional[float] = Field(None, description="每100公克/毫升那一欄的數字")
    per_serving: Optional[float] = Field(None, description="每份那一欄的數字")


class LabelResult(BaseModel):
    is_food_label: bool = Field(
        description="這張照片是否為食品包裝或其成分／營養標示")
    reject_reason: str = Field(
        default="",
        description="非食品標籤時簡述原因（非食品照片／無成分標示／過於模糊），否則空字串")

    name: Optional[str] = Field(
        None, description="產品完整名稱。不明確時結合品牌與產品類型，如 XX牌草莓夾心餅乾")
    brand: Optional[str] = Field(None, description="品牌")

    ingredients_raw: str = Field(
        default="",
        description="成分欄位的整段原文，**逐字照抄**，保留標點、括號與排列順序，"
                    "不要拆解、改寫或省略。無法辨識時填空字串")
    ingredients_list: list[str] = Field(
        default_factory=list,
        description="把 ingredients_raw 拆成個別成分。"
                    "**巢狀配方整團保留成一項**：`調味劑(L-麩酸鈉、5'-次黃嘌呤核苷磷酸二鈉)` "
                    "是一項，不要拆成三項；括號內容連同括號一起留在該項裡面。"
                    "只回傳成分名稱，是否為添加物由後端依食藥署正面表列判定，不需分類")

    nutrition: Nutrition = Field(
        description="**每100公克／毫升**那一欄。照抄標示，不要由每份換算")
    nutrition_per_serving: Nutrition = Field(
        description="**每份（每一份量）**那一欄。照抄標示，不要由每100公克換算。"
                    "標示只列一欄時，另一欄整組填 null")
    nutrition_other: list[OtherNutrient] = Field(
        default_factory=list,
        description="上面九項以外、出現在營養標示表格內的營養素（鈣、鐵、鉀、維生素、"
                    "膽固醇、單元／多元不飽和脂肪、糖醇、乳糖等）")
    nutrition_raw: str = Field(
        default="",
        description="營養標示整個表格的逐字原文，含標題列與每份／每100g兩欄所有數值，照抄不整理")

    serving_size: Optional[float] = Field(
        None, description="每一份量的公克或毫升數字，如「每份30公克」填 30。無標示填 null")
    servings_per_container: Optional[float] = Field(
        None, description="本包裝含幾份，如「本包裝含2份」填 2。無標示填 null")
    serving_description: Optional[str] = Field(
        None, description="每一份量欄位的原始文字，如「每份30公克(約10片)」")

    manufacturer: Optional[str] = Field(None, description="製造商全名，依包裝標示")
    allergy_warning: Optional[str] = Field(None, description="過敏原注意事項文字")
    certification_marks: list[str] = Field(
        default_factory=list,
        description="標章名稱，如 TQF／CAS／TAP／健康食品／有機農產品。"
                    "同一款標章出現多個編號仍只回一個")


# ── 拆成「每次都一樣的任務」與「這次要做的事」──────────────────────────────
# 固定的那半放 system_instruction：欄位形狀已經由 schema 表達，這裡只留
# schema 表達不了的規則（判斷順序、禁止捏造、標章清單）。
SYSTEM_INSTRUCTION = """你是食品標示辨識引擎，只輸出符合 schema 的結構化資料。

【相關性判斷 - 最優先】
先判斷照片是否為食品包裝或其成分／營養標示。若不是（人物、自拍、風景、動物、
無關物品，或完全看不到成分與營養標示），is_food_label 設為 false、reject_reason
填原因，其餘欄位一律留空或空陣列。**切勿為非食品照片捏造成分、營養數值或產品名稱。**

【只寫看得見的東西】
每一個值都必須是這張照片上讀得到的。讀不到就填 null 或空字串，
**不要依商品類型推測「應該會有」的成分或添加物**。這條比填滿欄位重要。

【營養標示】
台灣營養標示通常同時或擇一列出「每份」與「每100公克/毫升」兩欄，分別填入
nutrition_per_serving 與 nutrition。兩欄都照抄原始數字，**不要自行換算或互相推導**；
只有一欄時另一欄整組填 null。強制標示項目為熱量、蛋白質、脂肪、飽和脂肪、
反式脂肪、碳水化合物、糖、鈉；膳食纖維是自願標示，經常沒有。
**標示上沒有的欄位填 null，不要填 0，也不要推估**——0 是「標示為零」，
null 是「標示上沒這一項」，兩者意義完全不同。

【標章】
主動辨識 TQF（盾牌形金色 logo）、CAS、TAP、健康食品（小綠人）、有機農產品。"""

# 每次呼叫只送這一句——形狀與規則都已固定在 schema 與 system_instruction 裡。
USER_PROMPT = "解析這些食品包裝照片，以繁體中文填寫欄位。"


def _report():
    """比較新舊兩版每次呼叫要送的量。有金鑰時用 count_tokens 量真的 token 數。

    ⚠ **字元數與 token 數會給出相反的答案，以 token 為準。**
    schema 序列化 4994 字元／1810 token，比它取代掉的散文（3207 字元／1560 token）
    還大，看起來像變貴；但實測 tokens_prompt 是 1818 → 659，schema 根本不計費。
    要判斷成本一律看 `<out>_results/samples.jsonl` 的 tokens_prompt，不要看這裡的字元數。
    """
    import json
    import os
    import re
    import sys

    sys.stdout.reconfigure(encoding='utf-8')
    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(here, '..', '..', 'server', 'main.py'),
               encoding='utf-8').read()
    i = src.find('def analyze_image_with_gemini')
    j = src.find('content = [prompt]', i)
    old = re.search(r'prompt\s*=\s*"""(.*?)"""', src[i:j], re.S).group(1)

    from google.genai import _transformers as _t
    schema = json.dumps(_t.t_schema(None, LabelResult).model_dump(exclude_none=True),
                        ensure_ascii=False, default=str)
    new = SYSTEM_INSTRUCTION + USER_PROMPT

    def cjk(t):
        return sum(1 for c in t if '一' <= c <= '鿿')

    print('每次呼叫要送的固定部分（不含圖片）：')
    for lab, t in (('原版 prompt', old), ('新版 system+user', new),
                   ('新版 schema', schema)):
        print('  %-18s %5d 字元（中文 %4d／其餘 %4d）'
              % (lab, len(t), cjk(t), len(t) - cjk(t)))

    key = os.environ.get('GEMINI_API_KEY')
    if not key:
        print('\n沒有 GEMINI_API_KEY，只能給字元數。'
              '設好之後再跑一次會用 count_tokens 量真的 token。')
        return
    from google import genai
    client = genai.Client(api_key=key)
    for lab, t in (('原版 prompt', old), ('新版 system+user', new),
                   ('新版 schema', schema)):
        n = client.models.count_tokens(model='gemini-2.5-flash', contents=t)
        print('  %-18s %5d tokens' % (lab, n.total_tokens))


if __name__ == '__main__':
    _report()
