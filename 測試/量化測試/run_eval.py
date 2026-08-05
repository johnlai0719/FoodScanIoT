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
#
# 環境變數 EVAL_IMAGE_ROOT 可改讀別處的圖片（壓縮實驗用），預設為 ./images。
import os, sys, json, base64, asyncio, time, statistics
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.abspath(os.path.join(HERE, '..', '..', 'server'))
IMAGE_ROOT = os.environ.get('EVAL_IMAGE_ROOT') or HERE


def load_env(path):
    if not os.path.exists(path):
        return
    for line in open(path):
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

    def wrapper(*a, **kw):
        resp = inner(*a, **kw)
        u = getattr(resp, 'usage_metadata', None)
        _usage.append({
            "prompt": getattr(u, 'prompt_token_count', None),
            "output": getattr(u, 'candidates_token_count', None),
            "total": getattr(u, 'total_token_count', None),
            "model": kw.get('model') or (a[0] if a else None),
        } if u else {})
        return resp

    client.models.generate_content = wrapper


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
    only = set()
    for a in args:
        if a.startswith('--repeat='):
            repeat = max(1, int(a.split('=', 1)[1]))
        elif a.startswith('--tag='):
            tag = a.split('=', 1)[1]
        elif a.startswith('--only-version='):
            only_version = a.split('=', 1)[1]
        else:
            only.add(a)

    cases = json.load(open(os.path.join(HERE, 'cases.json'), encoding='utf-8'))['cases']
    pred_dir = os.path.join(HERE, 'predictions')
    res_dir = os.path.join(HERE, 'results')
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
        imgs = [os.path.join(IMAGE_ROOT, p) for p in c['images']]
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
                "set_version": c.get('set_version'), "category": c.get('category'),
                "difficulty": c.get('difficulty') or [],
                "n_images": len(imgs), "payload_raw_bytes": payload_raw,
                "payload_b64_bytes": payload_b64,
                "tokens_prompt": u.get('prompt'), "tokens_output": u.get('output'),
                "tokens_total": u.get('total'), "model": u.get('model'),
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
    with open(os.path.join(res_dir, 'samples.jsonl'), 'w', encoding='utf-8') as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + '\n')

    lat = [s['ms'] for s in samples]
    toks = [s['tokens_total'] for s in samples if s['tokens_total'] is not None]
    outs = [s['tokens_output'] for s in samples if s['tokens_output'] is not None]
    by_ver, by_cat = {}, {}
    for s in samples:
        by_ver.setdefault(s['set_version'] or 'unknown', []).append(s['ms'])
        by_cat.setdefault(s['category'] or 'unknown', []).append(s['ms'])

    summary = {
        "run_tag": tag,
        "started_at": started,
        "image_root": IMAGE_ROOT,
        "n_cases": done,
        "n_samples": len(samples),
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
                  "就分不出變慢是因為圖變大還是因為欄位變多。"),
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
