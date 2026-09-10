#!/usr/bin/env python3
"""用 Gemini 擴充 rec 合成語料的詞彙表。**不讀評估集的任何東西。**

## 為什麼要擴充

2026-08-18 的 rec 微調結論是：補詞彙有效但有上限。
字次涵蓋率 92.7%→97.9%、「劑」字 0→973 次，訓練曲線從遞減翻成遞增
（舊語料 582→567→535，新語料 589→587→597→602），成分完全命中 583→602。

**那個增益是純視覺的**——PP-OCR 的辨識頭在推論時只有 CTC、沒有語言模型
（`MultiHead.forward()` 在 eval 只回傳 `ctc_out`，有語言模型的 `NRTRHead`
訓練後即移除），時間步之間條件獨立。所以詞彙進到的是「這個字形看過幾次」，
不是「這個字後面常接什麼」。

視覺敏感度也是敏感度。既有詞彙表只有 `ADDITIVES 144 + FOODS 238 + FUNCTIONAL 31`，
台灣市售標示的用字遠不止這些——擴充詞彙表就是擴充字形曝光。

## 為什麼用 Gemini 而不用評估集

**紅線：不可以拿 `ground_truth/` 的成分當語料。** rec 是序列模型，
拿 c58 的 `ingredients_raw` 去渲染訓練圖，模型會學到那串字的序列先驗，
之後在 c58 上的分數就被灌水——與 `score_eval.py` 廢止 ingredient_types
的理由相同：受測對象與正解同源就不叫評估。

Gemini 產的詞彙來自它的預訓練語料（公開的食品標示、法規、商品資訊），
與這 58 案沒有關係。這和既有詞彙表的來源性質一樣（法規用語 ＋ 公開添加物資料庫）。

⚠ **但「無關」不靠宣稱，靠檢查。** 產完一定要跑
    python synth_corpus.py check corpus/<檔名>
它會查兩件事：語料裡有沒有出現評估集某案「連續 3 個成分」的相同順序（順序洩漏），
以及有沒有超過 25 字的完全相同片段。單一成分重疊無所謂——「水」「食鹽」是
產業共用字彙，順序才是洩漏。

用法：
    python gen_vocab.py --n=400                 # 產詞彙，附既有詞表的去重
    python gen_vocab.py --n=400 --model=gemini-3.6-flash
"""
import argparse
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import synth_corpus as SC                       # noqa: E402

OUT = os.path.join(HERE, 'data', 'vocab_gemini.json')
DEFAULT_MODEL = 'gemini-3.6-flash'

PROMPT = """列出台灣市售包裝食品的成分標示裡**實際會出現的詞彙**。

要求：
- 只回傳詞彙本身，不要說明、不要編號、不要造句
- 用台灣的繁體中文寫法與慣用譯名（例如「麥芽糊精」不是「麦芽糊精」，
  「L-麩酸鈉」不是「谷氨酸钠」）
- 涵蓋這幾類，每類都要有：
  1. 原料與食材（穀物、油脂、乳製品、肉類、蔬果、調味料）
  2. 食品添加物的正式名稱（含編號前綴如 5'-、DL-、L- 的那種）
  3. 功能類別詞（防腐劑、抗氧化劑、乳化劑、品質改良用劑…）
  4. 複合原料的常見寫法（奶精、酵母抽出物、水解植物蛋白…）
  5. 加工型態的修飾語（脫水、濃縮、精製、氫化、發酵…）
- 偏好**多字的化學名與複合詞**，那是辨識最容易出錯的地方
- 不要重複

請產生 %d 個詞彙。"""


def _key():
    """從 .env 讀金鑰。環境變數 `GOOGLE_API_KEY` 是 Google Vision 那把，
    對 Generative Language API 會回 API_KEY_INVALID，不能用。"""
    if os.environ.get('GEMINI_API_KEY'):
        return os.environ['GEMINI_API_KEY']
    for p in (os.path.join(HERE, '..', '.env'),
              os.path.join(HERE, '..', 'server', '.env')):
        p = os.path.abspath(p)
        if not os.path.exists(p):
            continue
        for ln in io.open(p, encoding='utf-8', errors='replace'):
            m = re.match(r'\s*GEMINI_API_KEY\s*=\s*(.*)', ln)
            if m:
                return m.group(1).strip().strip('"\'')
    sys.exit('找不到 GEMINI_API_KEY')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=400)
    ap.add_argument('--model', default=DEFAULT_MODEL)
    ap.add_argument('--rounds', type=int, default=1,
                    help='跑幾輪再取聯集。同一個 prompt 多跑幾輪能拿到更多不重複的詞')
    a = ap.parse_args()

    from google import genai
    from google.genai import types
    from pydantic import BaseModel

    class Vocab(BaseModel):
        terms: list[str]

    client = genai.Client(api_key=_key())
    cfg = types.GenerateContentConfig(
        response_mime_type='application/json', response_schema=Vocab,
        temperature=1.0)          # 要多樣性，不要每輪都給同一批

    got = set()
    for r in range(1, a.rounds + 1):
        resp = client.models.generate_content(
            model=a.model, contents=PROMPT % a.n, config=cfg)
        terms = [t.strip() for t in (resp.parsed.terms if resp.parsed else [])]
        new = {t for t in terms if 2 <= len(t) <= 24 and re.search(r'[一-鿿]', t)}
        print('第 %d 輪：回傳 %d 個，過濾後 %d 個，累計 %d'
              % (r, len(terms), len(new), len(got | new)))
        got |= new

    # 與既有詞表比對，看真正新增多少
    old = set(SC.ADDITIVES) | set(SC.FOODS) | set(SC.FUNCTIONAL)
    fresh = sorted(got - old)
    print()
    print('既有詞表 %d 個（ADDITIVES %d ＋ FOODS %d ＋ FUNCTIONAL %d）'
          % (len(old), len(SC.ADDITIVES), len(SC.FOODS), len(SC.FUNCTIONAL)))
    print('Gemini 產出 %d 個，其中 %d 個是新的（重複 %d）'
          % (len(got), len(fresh), len(got) - len(fresh)))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(sorted(got), io.open(OUT, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('寫出 →', OUT)
    print()
    print('新詞取樣：', '、'.join(fresh[:25]))
    print()
    print('⚠ 下一步必須先驗污染：把它併進語料後跑')
    print('    python synth_corpus.py check corpus/<檔名>')


if __name__ == '__main__':
    main()
