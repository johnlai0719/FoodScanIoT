#!/usr/bin/env python3
"""本地 2B 文字模型把 OCR 文字整理成結構化欄位（`name`／`manufacturer`）。

**為什麼有這支**：2026-09-07 量到規則抽取器在 `manufacturer` 上只有 74/150，
而 Gemini 結構化是 116/150——差 42 案。原因是 150 案裡只有 79 案有
「製造商：」這種角色標籤，其餘 71 案標示上就只印公司名，規則沒有東西可抓。
但 Gemini 在雲端，所以先試本地能不能補上這個差距。

**這是文字→文字，不是影像→文字。** 輸入是既有的 OCR 行，模型只負責挑與歸類。
這讓它落在 §0（輸出的每個字都要有影像來源）的安全側：輸出理論上是輸入的
子字串，**可以機械檢查**，跟切分模型的安全性質是同一條。實際上小模型仍會
繁簡轉換與改寫，所以 `_meta.grounded` 逐欄記下有沒有佐證，不硬刪。

**模型**：`lmstudio-community/Qwen3.5-2B-GGUF`（Q4_K_M，1.27 GB），
由 llama-server 起在 8179。

    llama-server -m Qwen3.5-2B-Q4_K_M.gguf --alias qwen -c 8192 -ngl 99 --port 8179

⚠ **它是推理模型，一定要關掉思考。** 不關的話 `reasoning_content` 就吃光
`max_tokens`，`content` 回空字串——先導第一輪 24/24 全部「解析失敗」就是這個，
不是模型不會做。關法是 `chat_template_kwargs: {enable_thinking: false}`。
"""
import argparse, base64, io, json, os, re, sys, time
import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '測試', '量化測試')))
import casetool                      # noqa: E402

URL = os.environ.get('LF_URL') or 'http://127.0.0.1:8179/v1/chat/completions'
MODEL = os.environ.get('LF_MODEL') or 'qwen'
OUT = os.path.join(HERE, 'out', os.environ.get('LF_OUT') or 'localfields')
READERS = tuple((os.environ.get('LF_READERS') or 'v6_best,vlcrop_hy_v4').split(','))

# 優先序與正解定義一致（`正解欄位定義.md`）。最後一句是 §0 的提示詞版本——
# 它擋不住模型編造（提示詞從來擋不住），真正的防線是輸出端的佐證檢查。
SYS = ('你是食品標示的欄位整理器。使用者會給你一張包裝照片的 OCR 文字。'
       '只輸出 JSON，格式為 {"name": ..., "manufacturer": ...}。\n'
       'name 是包裝上「品名」欄的內容，逐字照抄，不要補品牌也不要刪。\n'
       'manufacturer 是廠商全名，優先序：製造商 > 委製商 > 負責廠商 > 進口商 > 代理商。'
       '只抄公司名，不含地址、不含電話。\n'
       '**只能使用 OCR 文字裡出現過的字，不可以補上你認為應該有的內容。**'
       '找不到就填 null。')


def ocr_lines(cid):
    out = []
    for pre in READERS:
        p = os.path.join(HERE, 'out', pre, cid + '.json')
        if not os.path.exists(p):
            continue
        d = json.load(io.open(p, encoding='utf-8'))
        out += [l.get('text') or '' for im in d.get('images', [])
                for l in im.get('lines', [])]
    return out


def ask(text, max_tokens=768):
    body = {'model': MODEL, 'temperature': 0, 'max_tokens': max_tokens,
            'chat_template_kwargs': {'enable_thinking': False},
            'messages': [{'role': 'system', 'content': SYS},
                         {'role': 'user', 'content': text[:6000]}]}
    t = time.time()
    r = requests.post(URL, json=body, timeout=600).json()
    return r['choices'][0]['message']['content'], time.time() - t


def parse(s):
    m = re.search(r'\{.*\}', s or '', re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group())
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def run_one(cid):
    """單一案例，供線上推論（reader/pipeline.py）呼叫。

    2026-09-13 從 main() 的迴圈體抽出來——抽出而不是另寫一份，是因為
    「同一份知識放兩個地方」在本專案已經造成過三次分岔（族群詞彙、
    撇號正規化、添加物分母）。main() 現在也走這支。

    回傳 (prediction, 秒數)。沒有 OCR 文字可讀時回 (None, None)——
    與「模型答了但兩欄都空」（({}, 1.2)）意義不同，不可合併。
    """
    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, cid + '.json')
    L = ocr_lines(cid)
    if not L:
        return None, None
    raw, dt = ask('\n'.join(L))
    d = parse(raw)
    json.dump({'case_id': cid, 'prediction': d or {}, 'raw': raw,
               'elapsed_s': round(dt, 2), 'readers': list(READERS)},
              io.open(dst, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    return d, dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', nargs='*')
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    cases = casetool.load_cases()['cases']
    if a.only:
        cases = [c for c in cases if c['case_id'] in a.only]
    if a.limit:
        cases = cases[:a.limit]
    bad = n = 0
    secs = []
    for c in cases:
        cid = c['case_id']
        dst = os.path.join(OUT, cid + '.json')
        if os.path.exists(dst):          # 續跑：已有輸出就跳過
            continue
        d, dt = run_one(cid)
        if dt is None:
            print('缺 OCR 輸出，跳過 %s' % cid)
            continue
        secs.append(dt)
        n += 1
        if d is None:
            bad += 1
        print('%-30s %6.1fs  %s' % (cid[:30], dt, json.dumps(d or {}, ensure_ascii=False)[:80]))
    if secs:
        print('\n%d 案｜中位 %.1f 秒｜JSON 解析失敗 %d' % (n, sorted(secs)[len(secs)//2], bad))


if __name__ == '__main__':
    main()
