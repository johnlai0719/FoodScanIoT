#!/usr/bin/env python3
# 跑預測：對每個 case 的照片呼叫「正式管線同一支」Gemini 函式，
# 把原始輸出存到 predictions/<case_id>.json。不寫 DB、不存圖。
#
# 直接 import server/main.py 的 analyze_image_with_gemini，確保 prompt 與模型
# 設定與線上完全相同，量到的才是真實行為。
#
# 記錄的東西不只延遲（2026-08-05 起，因應 07-30 回饋要求「同案例量測 p50/p95」）：
#   - token 用量：延遲從 4.8s 升到 15.9s 時，歸因「因為抓的欄位變多」需要證據，
#     輸出 token 數就是那個證據。沒有它只能用講的。
#   - 送出位元組數：影像壓縮實驗（同批回饋第 5 點）要比較壓縮前後，
#     用同一支 harness、同一批案例，只換 EVAL_IMAGE_ROOT 即可，不必另寫腳本。
#   - 逐次樣本存 results/samples.jsonl：只存彙總的話，事後想改看 p99、
#     想只看某類別的分布、想比較兩次執行，都得重跑一次（要花 API 錢）。
#
# token 用量以包裝 client 的方式取得，**不修改 server/main.py**：量測工具不應
# 為了自己方便去動被量測的對象，否則線上行為與量到的行為就不再是同一件事。
#
# 用法：
#   cd 測試/量化測試 && ../../server/venv/bin/python run_eval.py            # 全部
#   ../../server/venv/bin/python run_eval.py c01_柳橙綠茶 c07_綠奶茶        # 指定案例
#   ../../server/venv/bin/python run_eval.py --repeat=3                     # 每案跑 3 次量穩定度
#   ../../server/venv/bin/python run_eval.py --only-version=core48          # 只跑凍結子集
#   ../../server/venv/bin/python run_eval.py --tag=compressed_1280          # 為本次執行命名
#   ../../server/venv/bin/python run_eval.py --out=predictions_struct       # 寫到別的資料夾
#
# 實驗開關（皆為環境變數，一律在包裝層覆寫，不改 server/main.py）：
#   EVAL_MODEL / EVAL_THINKING_BUDGET / EVAL_MAX_OUTPUT_TOKENS
#   EVAL_STRUCTURED=1     改用 response_schema 約束解碼（見 gemini_schema.py）
#   EVAL_TEMPERATURE=0.1  線上沒設，2.5-flash 預設 1.0
#
# 環境變數 EVAL_IMAGE_ROOT 可改讀別處的圖片（壓縮實驗用），預設為 ./images。
import os, sys, json, base64, asyncio, time, statistics
from datetime import datetime

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「塩」「菓」…）。
# 沒有這兩行的後果不是印出亂碼，而是**整批任務在中途拋 UnicodeEncodeError 死掉**
# ——2026-09-07 這支跑到第 10 案就炸在 `塩`，intake.py 同一天也炸在 `菓`。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.abspath(os.path.join(HERE, '..', '..', 'server'))
IMAGE_ROOT = os.environ.get('EVAL_IMAGE_ROOT') or HERE


def load_env(path):
    # 明確指定 utf-8：.env 與 .env.example 都含中文註解，而 Windows 的
    # open() 預設是 cp950，不指定會在載入設定這一步就 UnicodeDecodeError。
    if not os.path.exists(path):
        return
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(os.path.join(SERVER, '.env'))
sys.path.insert(0, SERVER)
import main as pipeline  # noqa: E402
from main import analyze_image_with_gemini  # noqa: E402


# ─── token 用量側錄 ───────────────────────────────────────────────────────────
# 包住 client 的 generate_content，把每次呼叫的 usage_metadata 收下來。
# 正式程式碼只回傳解析後的 JSON，拿不到 token 數；改正式程式碼會讓量測對象與
# 線上不同，故在此攔截。
_usage = []


def _install_usage_probe():
    client = getattr(pipeline, '_genai_client', None)
    if client is None:
        print("[WARN] 無 Gemini client（GEMINI_API_KEY 未設定？），不會有 token 統計")
        return
    inner = client.models.generate_content

    # thinking 的實驗開關。正式程式碼寫死 thinking_budget=0，此處在同一個
    # 包裝內覆寫，**不改 server/main.py**——量測工具不該為自己方便去動被量測
    # 的對象，否則線上行為與量到的行為就不再是同一件事。
    #   EVAL_THINKING_BUDGET   0 停用（線上現值）／-1 自動／正整數為 token 上限
    #   EVAL_MAX_OUTPUT_TOKENS 開 thinking 時務必一併設定，見下方說明
    # 模型覆寫。同一批照片、同一份人工正解、同一支 harness，只換模型——
    # 「換更強的模型是不是就解決了」這個問題才答得出數字而非印象。
    # 正式管線寫死於 server/main.py，此處僅在包裝層覆寫，理由同 thinking 開關。
    #   EVAL_MODEL=<model id>
    # 產物請用 --tag 命名，否則兩次執行的 predictions/ 會互相覆蓋。
    model_override = os.environ.get('EVAL_MODEL')
    if model_override:
        print(f"[實驗] 模型覆寫為 {model_override}（正式管線仍為 server/main.py 所寫死者）")

    # 結構化輸出的實驗開關。線上是「prompt 裡用散文寫 JSON 形狀 ＋ 正則挖出來」，
    # 這裡改成 response_schema 約束解碼，並把固定的任務描述搬進 system_instruction。
    # 目的是量三件事：輸入 token 降多少、延遲降多少、判定品質有沒有動。
    #   EVAL_STRUCTURED=1   啟用（契約見 gemini_schema.py）
    #   EVAL_TEMPERATURE    線上沒設，2.5-flash 預設 1.0；0.1 用來壓格式漂移與幻覺
    structured = os.environ.get('EVAL_STRUCTURED') == '1'
    temperature = os.environ.get('EVAL_TEMPERATURE')
    schema = None
    if structured:
        import gemini_schema as GS
        schema = GS
        print("[實驗] 結構化輸出：response_schema=LabelResult"
              "、任務描述改由 system_instruction 承載")
    if temperature is not None:
        print(f"[實驗] temperature={temperature}")

    budget = os.environ.get('EVAL_THINKING_BUDGET')
    max_out = os.environ.get('EVAL_MAX_OUTPUT_TOKENS')
    if budget is not None:
        print(f"[實驗] thinking_budget 覆寫為 {budget}"
              + (f"、max_output_tokens={max_out}" if max_out else ""))
        if not max_out:
            # response.text 會排除 thought 部分；thinking 若把 output 額度用完，
            # response.text 為 None，main.py 的 re.search 會丟 TypeError 而被
            # 外層 except 接住 → 該案記為辨識失敗。看起來像模型變差，
            # 實際是額度被思考吃掉了。
            print("[WARN] 未設 EVAL_MAX_OUTPUT_TOKENS。thinking 佔用 output 額度，"
                  "額度不足時回應會是空的，該案會被記為失敗而非低分。")

    def wrapper(*a, **kw):
        if model_override:
            # main.py 以關鍵字傳 model=；位置參數形式一併處理以免默默沒生效
            if a:
                a = (model_override,) + a[1:]
            else:
                kw['model'] = model_override
        if budget is not None:
            cfg = dict(kw.get('config') or {})
            cfg['thinking_config'] = {'thinking_budget': int(budget)}
            if max_out:
                cfg['max_output_tokens'] = int(max_out)
            kw['config'] = cfg
        if schema is not None:
            cfg = dict(kw.get('config') or {})
            cfg['system_instruction'] = schema.SYSTEM_INSTRUCTION
            cfg['response_mime_type'] = 'application/json'
            cfg['response_schema'] = schema.LabelResult
            kw['config'] = cfg
            # contents 是 main.py 組的 [prompt(str)] + [PIL.Image...]。
            # 只換第一個字串，圖片原封不動——換掉的是「每次重講一遍的任務描述」。
            c = kw.get('contents')
            if isinstance(c, list) and c and isinstance(c[0], str):
                kw['contents'] = [schema.USER_PROMPT] + list(c[1:])
        if temperature is not None:
            cfg = dict(kw.get('config') or {})
            cfg['temperature'] = float(temperature)
            kw['config'] = cfg
        resp = inner(*a, **kw)
        u = getattr(resp, 'usage_metadata', None)
        fr = None
        try:
            fr = str(resp.candidates[0].finish_reason)
        except Exception:
            pass
        _usage.append({
            "prompt": getattr(u, 'prompt_token_count', None),
            "output": getattr(u, 'candidates_token_count', None),
            # thinking 實際用掉多少——效果與成本的對照要靠這個欄位
            "thoughts": getattr(u, 'thoughts_token_count', None),
            "total": getattr(u, 'total_token_count', None),
            "model": kw.get('model') or (a[0] if a else None),
            "finish_reason": fr,
        } if u else {})
        return resp

    client.models.generate_content = wrapper


def resolve(rel):
    """把 cases.json 的相對路徑解到 IMAGE_ROOT 底下，副檔名不符時回退到 .jpg。

    壓縮後的圖一律是 JPEG（見 compress_images.py），但原圖有 3 張是 .webp、
    1 張是 .jpeg。不回退的話這幾案在壓縮組會被 SKIP，兩組的案例母體就不同，
    比出來的分數不是同一批案例的分數。
    """
    p = os.path.join(IMAGE_ROOT, rel)
    if os.path.exists(p):
        return p
    alt = os.path.splitext(p)[0] + '.jpg'
    return alt if os.path.exists(alt) else p


def img_to_b64(path):
    with open(path, 'rb') as f:
        return base64.b64encode(f.read()).decode()


def pct(data, q):
    if not data:
        return 0.0
    s = sorted(data)
    return s[max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))]


def stats(vals):
    if not vals:
        return None
    return {"n": len(vals), "min": round(min(vals), 1), "avg": round(statistics.mean(vals), 1),
            "p50": round(pct(vals, 0.50), 1), "p95": round(pct(vals, 0.95), 1),
            "max": round(max(vals), 1)}


def main():
    args = sys.argv[1:]
    repeat, tag, only_version = 1, None, None
    # 產物資料夾。預設仍是 predictions/，但實驗組請務必用 --out 指到別處——
    # 上面那句「用 --tag 命名」其實擋不住覆蓋（tag 只寫進 summary，不換資料夾），
    # 重跑一次就會蓋掉當基準用的那批預測，而那批要花 API 錢才生得回來。
    out_name = 'predictions'
    only = set()
    for a in args:
        if a.startswith('--repeat='):
            repeat = max(1, int(a.split('=', 1)[1]))
        elif a.startswith('--tag='):
            tag = a.split('=', 1)[1]
        elif a.startswith('--out='):
            out_name = a.split('=', 1)[1]
        elif a.startswith('--only-version='):
            only_version = a.split('=', 1)[1]
        else:
            only.add(a)

    cases = json.load(open(os.path.join(HERE, 'cases.json'), encoding='utf-8'))['cases']
    pred_dir = os.path.join(HERE, out_name)
    res_dir = os.path.join(HERE, 'results' if out_name == 'predictions'
                           else out_name + '_results')
    os.makedirs(pred_dir, exist_ok=True)
    os.makedirs(res_dir, exist_ok=True)

    _install_usage_probe()
    started = datetime.now().isoformat(timespec='seconds')
    samples, done, skipped = [], 0, []

    for c in cases:
        cid = c['case_id']
        if only and cid not in only:
            continue
        if only_version and c.get('set_version') != only_version:
            continue
        imgs = [resolve(p) for p in c['images']]
        missing = [p for p in imgs if not os.path.exists(p)]
        if missing:
            print(f"[SKIP] {cid}: 找不到圖片 {missing}")
            skipped.append(cid)
            continue
        b64 = [img_to_b64(p) for p in imgs]
        # 送出的實際位元組數（base64 編碼後）與原圖大小，供壓縮實驗前後對照
        payload_b64 = sum(len(x) for x in b64)
        payload_raw = sum(os.path.getsize(p) for p in imgs)

        last = None
        for r in range(repeat):
            mark = len(_usage)
            t0 = time.time()
            try:
                out = asyncio.run(analyze_image_with_gemini(b64, barcode="TEST"))
                err = None
            except Exception as e:
                err = str(e)[:200]
                print(f"[ERR ] {cid}: {err[:120]}")
                out = None
            ms = (time.time() - t0) * 1000
            last = out
            # 本次呼叫產生的 usage（正常為 1 筆；失敗時可能為 0）
            u = _usage[mark] if len(_usage) > mark else {}
            samples.append({
                "case_id": cid, "run": r + 1, "ms": round(ms, 1),
                # 逐筆記錄來源：合併保留舊案例後，同一份 samples.jsonl 可能混有
                # 不同次執行的資料，沒有這兩欄就分不出哪筆是什麼時候量的
                "run_tag": tag, "run_at": started,
                "set_version": c.get('set_version'), "category": c.get('category'),
                "difficulty": c.get('difficulty') or [],
                "n_images": len(imgs), "payload_raw_bytes": payload_raw,
                "payload_b64_bytes": payload_b64,
                "tokens_prompt": u.get('prompt'), "tokens_output": u.get('output'),
                "tokens_thoughts": u.get('thoughts'), "tokens_total": u.get('total'),
                "model": u.get('model'), "finish_reason": u.get('finish_reason'),
                "thinking_budget": os.environ.get('EVAL_THINKING_BUDGET'),
                "is_food_label": (out or {}).get('is_food_label'),
                "ok": out is not None, "error": err,
            })
            tok = u.get('total')
            print(f"  {cid[:28]:<30} {ms:>7.0f}ms  "
                  f"tok={tok if tok is not None else '?':>6}  "
                  f"{payload_raw/1024:>6.0f}KB  is_food_label={(out or {}).get('is_food_label')}"
                  + (f"  (run {r+1}/{repeat})" if repeat > 1 else ""))

        with open(os.path.join(pred_dir, f'{cid}.json'), 'w', encoding='utf-8') as f:
            json.dump({"case_id": cid, "prediction": last}, f, ensure_ascii=False, indent=2)
        done += 1

    if not samples:
        print("沒有跑到任何案例。")
        return

    # 逐次樣本落地：事後要改算分位數、依類別切、比對兩次執行，都不必重打 API
    #
    # 只跑部分案例時**保留其餘案例的舊樣本**，僅取代本次跑到的那幾案。
    # 直接覆寫的話，補跑一案就會讓整批延遲數據消失——修 c35 的照片後補跑該案，
    # 前一次 48 案的 p50／p95 當場就沒了（幸好已先快照）。這與 predictions/
    # 的行為一致：那裡本來就是一案一檔、後跑的取代先跑的，其餘案例不受影響。
    sample_path = os.path.join(res_dir, 'samples.jsonl')
    ran_ids = {s['case_id'] for s in samples}
    kept = []
    if os.path.exists(sample_path):
        with open(sample_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    old = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if old.get('case_id') not in ran_ids:
                    kept.append(old)
    merged = kept + samples
    with open(sample_path, 'w', encoding='utf-8') as f:
        for s in merged:
            f.write(json.dumps(s, ensure_ascii=False) + '\n')

    if kept:
        print(f"\n[合併] 本次跑了 {len(ran_ids)} 案，保留其餘 "
              f"{len({s['case_id'] for s in kept})} 案的既有樣本。")

    # 彙總一律以合併後的全部樣本計算——這樣「跑一案」不會讓彙總只剩一案，
    # 而是反映「目前手上這批案例最新一次量到的結果」，與 score_eval 讀
    # predictions/ 的語義相同。
    samples = merged
    lat = [s['ms'] for s in samples]
    toks = [s['tokens_total'] for s in samples if s['tokens_total'] is not None]
    outs = [s['tokens_output'] for s in samples if s['tokens_output'] is not None]
    by_ver, by_cat = {}, {}
    for s in samples:
        by_ver.setdefault(s['set_version'] or 'unknown', []).append(s['ms'])
        by_cat.setdefault(s['category'] or 'unknown', []).append(s['ms'])

    # 樣本可能來自多次執行（補跑單案時保留了其餘案例）。混合來源不標示會誤導：
    # 讀者會以為整批是同一時間、同一批設定下量出來的。
    provenance = {}
    for s in samples:
        key = f"{s.get('run_tag') or '(未命名)'} @ {s.get('run_at') or '(未知時間)'}"
        provenance[key] = provenance.get(key, 0) + 1

    summary = {
        "run_tag": tag,
        "started_at": started,
        "image_root": IMAGE_ROOT,
        "n_cases_this_run": done,
        "n_cases": len({s['case_id'] for s in samples}),
        "n_samples": len(samples),
        "sample_provenance": provenance,
        "n_failed": sum(1 for s in samples if not s['ok']),
        "skipped_cases": skipped,
        "latency_ms": stats(lat),
        "latency_by_set_version": {k: stats(v) for k, v in sorted(by_ver.items())},
        "latency_by_category": {k: stats(v) for k, v in sorted(by_cat.items())},
        "tokens_total": stats(toks),
        "tokens_output": stats(outs),
        "payload_raw_bytes": stats([s['payload_raw_bytes'] for s in samples]),
        "_說明": ("latency 為 Gemini 視覺推理單段耗時，不含 App→Fog→Cloud 傳輸。"
                  "比較壓縮前後時，傳輸段需另行量測——把兩段混在同一個 p95 裡，"
                  "就分不出變慢是因為圖變大還是因為欄位變多。"
                  "sample_provenance 列出樣本的來源執行；若不只一項，代表本批"
                  "混有不同次執行的資料（補跑部分案例所致），引用延遲數字時須說明。"),
    }
    with open(os.path.join(res_dir, 'latency_vision.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n=== Gemini Vision 推理延遲（ms, n={len(lat)}）===")
    print(f"  min={min(lat):.0f}  avg={statistics.mean(lat):.0f}  "
          f"p50={pct(lat,0.50):.0f}  p95={pct(lat,0.95):.0f}  max={max(lat):.0f}")
    if toks:
        print(f"=== token（total, n={len(toks)}）===")
        print(f"  avg={statistics.mean(toks):.0f}  p50={pct(toks,0.50):.0f}  "
              f"p95={pct(toks,0.95):.0f}  max={max(toks):.0f}")
    if summary['n_failed']:
        print(f"\n[WARN] {summary['n_failed']} 次呼叫失敗，見 results/samples.jsonl 的 error 欄位")
    print(f"\n完成 {done} 個案例。逐次樣本：results/samples.jsonl")
    print("接著跑 score_eval.py 評分。")


if __name__ == '__main__':
    main()
