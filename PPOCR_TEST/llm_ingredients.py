#!/usr/bin/env python3
"""以本地語言模型從 OCR 文字直接抽取成分清單（[[14-以語言模型從OCR文字抽成分清單]]）。

與 `local_fields.py` 同一個模型、同一批輸入文字，只是換一個任務。
輸出寫 `out/llming/`，**不進線上組態**，先看數字。

⚠ 安全判準是**佐證率**不是 F1：規則抽取只會從文字裡切，佐證率恆為 1.0；
換成生成式模型必然低於 1.0，事前訂的否決線是 0.95。
"""
import io
import json
import os
import re
import statistics
import sys
import time

import requests

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get('LI_URL') or 'http://127.0.0.1:8179/v1/chat/completions'
MODEL = os.environ.get('LI_MODEL') or 'qwen'
OUT = os.path.join(HERE, 'out', os.environ.get('LI_OUT') or 'llming')
READERS = tuple((os.environ.get('LI_READERS') or 'v6_best,vlcrop_hy_v4').split(','))

SYS = ('你是食品標示的成分清單抽取器。使用者會給你一張包裝照片的 OCR 文字。\n'
       '只輸出 JSON，格式為 {"ingredients": ["...", "..."]}。\n'
       '規則：\n'
       '1. 只抄成分／原料／配料欄底下的項目，逐字照抄。\n'
       '2. 巢狀複方保留完整，例如「調味劑(食鹽、L-麩酸鈉)」算**一項**。\n'
       '3. 多段標示（麵條／調味包／油包）**每一段都要抄**。\n'
       '4. 不要抄營養標示、地址、保存期限、注意事項。\n'
       '5. **只能使用 OCR 文字裡出現過的字，不可以補上你認為應該有的內容。**\n'
       '找不到就回 {"ingredients": []}。')


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


def ask(text, max_tokens=2048):
    body = {'model': MODEL, 'temperature': 0, 'max_tokens': max_tokens,
            'chat_template_kwargs': {'enable_thinking': False},
            'messages': [{'role': 'system', 'content': SYS},
                         {'role': 'user', 'content': text}]}
    t = time.time()
    r = requests.post(URL, json=body, timeout=300).json()
    return r['choices'][0]['message']['content'], time.time() - t


def main():
    os.makedirs(OUT, exist_ok=True)
    cases = json.load(io.open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                              encoding='utf-8'))['cases']
    bad, secs = 0, []
    for i, c in enumerate(cases, 1):
        cid = c['case_id']
        dst = os.path.join(OUT, cid + '.json')
        if os.path.exists(dst):
            continue
        L = ocr_lines(cid)
        if not L:
            continue
        txt = '\n'.join(L)[:12000]
        try:
            raw, dt = ask(txt)
        except Exception as e:
            raw, dt = '', 0.0
        secs.append(dt)
        items = []
        m = re.search(r'\{.*\}', raw, re.S)
        if m:
            try:
                items = (json.loads(m.group(0)) or {}).get('ingredients') or []
            except Exception:
                bad += 1
        else:
            bad += 1
        items = [x for x in items if isinstance(x, str) and x.strip()]
        json.dump({'case_id': cid, 'ingredients': items, 'raw': raw,
                   'elapsed_s': round(dt, 2), 'readers': list(READERS)},
                  io.open(dst, 'w', encoding='utf-8'), ensure_ascii=False)
        print('[%d/%d] %-28s %5.1fs  %2d 項' % (i, len(cases), cid, dt, len(items)))
    print('\n完成｜中位 %.1f 秒｜JSON 解析失敗 %d'
          % (statistics.median(secs) if secs else 0, bad))


if __name__ == '__main__':
    main()
