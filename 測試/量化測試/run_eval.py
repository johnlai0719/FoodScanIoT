#!/usr/bin/env python3
# 跑預測：對每個 case 的照片呼叫「正式管線同一支」Gemini 函式，
# 把原始輸出存到 predictions/<case_id>.json。不寫 DB、不存圖。
#
# 直接 import server/main.py 的 analyze_image_with_gemini，確保 prompt 與模型
# 設定（gemini-2.5-flash, thinking_budget=0）與線上完全相同，量到的才是真實行為。
#
# 用法：
#   cd 測試/量化測試 && ../../server/venv/bin/python run_eval.py            # 全部
#   ../../server/venv/bin/python run_eval.py c01_柳橙綠茶 c07_綠奶茶        # 指定案例
#   ../../server/venv/bin/python run_eval.py --repeat=3                     # 每案跑 3 次量穩定度
import os, sys, json, base64, asyncio, time, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.abspath(os.path.join(HERE, '..', '..', 'server'))


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
from main import analyze_image_with_gemini  # noqa: E402


def img_to_b64(path):
    with open(path, 'rb') as f:
        return base64.b64encode(f.read()).decode()


def pct(data, q):
    if not data:
        return 0.0
    s = sorted(data)
    return s[max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))]


def main():
    args = sys.argv[1:]
    repeat = 1
    only = set()
    for a in args:
        if a.startswith('--repeat='):
            repeat = max(1, int(a.split('=', 1)[1]))
        else:
            only.add(a)

    cases = json.load(open(os.path.join(HERE, 'cases.json'), encoding='utf-8'))['cases']
    pred_dir = os.path.join(HERE, 'predictions')
    res_dir = os.path.join(HERE, 'results')
    os.makedirs(pred_dir, exist_ok=True)
    os.makedirs(res_dir, exist_ok=True)

    lat = []
    done = 0
    for c in cases:
        cid = c['case_id']
        if only and cid not in only:
            continue
        imgs = [os.path.join(HERE, p) for p in c['images']]
        missing = [p for p in imgs if not os.path.exists(p)]
        if missing:
            print(f"[SKIP] {cid}: 找不到圖片 {missing}")
            continue
        b64 = [img_to_b64(p) for p in imgs]

        last = None
        for r in range(repeat):
            t0 = time.time()
            try:
                out = asyncio.run(analyze_image_with_gemini(b64, barcode="TEST"))
            except Exception as e:
                print(f"[ERR ] {cid}: {str(e)[:120]}")
                out = None
            ms = (time.time() - t0) * 1000
            lat.append(ms)
            last = out
            flag = (out or {}).get('is_food_label')
            print(f"  {cid[:30]:<32} {ms:>7.0f}ms  is_food_label={flag}"
                  + (f"  (run {r+1}/{repeat})" if repeat > 1 else ""))

        with open(os.path.join(pred_dir, f'{cid}.json'), 'w', encoding='utf-8') as f:
            json.dump({"case_id": cid, "prediction": last}, f, ensure_ascii=False, indent=2)
        done += 1

    if lat:
        print(f"\n=== Gemini Vision 推理延遲（ms, n={len(lat)}）===")
        print(f"  min={min(lat):.0f}  avg={statistics.mean(lat):.0f}  "
              f"median={statistics.median(lat):.0f}  p95={pct(lat,0.95):.0f}  max={max(lat):.0f}")
        json.dump({"n": len(lat), "min": min(lat), "avg": statistics.mean(lat),
                   "median": statistics.median(lat), "p95": pct(lat, 0.95), "max": max(lat)},
                  open(os.path.join(res_dir, 'latency_vision.json'), 'w'), indent=2)
    print(f"\n完成 {done} 個案例。接著跑 score_eval.py 評分。")


if __name__ == '__main__':
    main()
