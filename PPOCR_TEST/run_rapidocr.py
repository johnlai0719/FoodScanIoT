#!/usr/bin/env python3
"""用 RapidOCR（ONNXRuntime）跑整頁 OCR，輸出與 `run_baseline.py` 相同的格式。

**為什麼要試它**：`region_crop` 吃的是「文字行 ＋ 框」，不在乎那些框是誰產的。
而 PP-OCR 在 CPU 上光載入就 819 MB、單張數十秒，是 Fog 分層的主要障礙。
RapidOCR 是同一批 PaddleOCR 模型的 ONNXRuntime 移植，**不需要整包
PaddlePaddle**，理論上記憶體與 ARM 相容性都好很多。

所以要問的是：**換掉定位器，裁切品質守不守得住？**

⚠ 這支跑在獨立的 `.venv_rapid`（onnxruntime 與 paddle 同環境有風險，
   沿用本專案 `.venv_torch` 的作法）。它**不 import** `score_ocr`，
   自己讀 `cases.json`，這樣虛擬環境可以維持最小。

用法：
    .venv_rapid/Scripts/python.exe run_rapidocr.py --out=rapid_v1
    .venv_rapid/Scripts/python.exe run_rapidocr.py --limit=10   # 先導
"""
import argparse
import json
import os
import statistics
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_ROOT = os.environ.get('EVAL_ROOT') or os.path.normpath(
    os.path.join(HERE, '..', '測試', '量化測試'))


def rss():
    try:
        import psutil
        return psutil.Process().memory_info().rss / 2**20
    except Exception:
        return 0.0


def to_box(pts):
    """RapidOCR 回四點多邊形；轉成 PP-OCR 那份 JSON 的整數四點格式。"""
    return [[int(round(float(x))), int(round(float(y)))] for x, y in pts]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='rapid_v1')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--cfg', default='v6', choices=('v6', 'v6s', 'cht'))
    ap.add_argument('--gpu', action='store_true', help='用 CUDA 跑（只影響速度）')
    ap.add_argument('--threads', type=int, default=0, help='限制執行緒數（Pi 5 為 4）')
    ap.add_argument('--side', type=int, default=2048,
                    help='偵測端限制邊長，對齊 v6_best 的 text_det_limit_side_len')
    a = ap.parse_args()

    print('起始 RSS  %.0f MB' % rss())
    t0 = time.time()
    from rapidocr import RapidOCR, ModelType, OCRVersion
    print('import    %.0f MB   %.1fs' % (rss(), time.time() - t0))
    # ⚠ `ocr_version`／`model_type` 一定要傳 Enum（`parse_parameters.update_batch`
    #    的 enum_params 會擋），但 `lang_type` 只能傳字串——LangDet 列舉裡
    #    根本沒有 chinese_cht，而解析器接受字串。兩者規則相反，很容易寫錯。
    V4, V6 = OCRVersion.PPOCRV4, OCRVersion.PPOCRV6
    MOBILE, MEDIUM, SMALL = ModelType.MOBILE, ModelType.MEDIUM, ModelType.SMALL

    # ⚠ 繁體那顆非常重要——`trad-decode-finding` 記錄過簡體模型會讓繁簡混雜，
    #    而且「指標沒動」不等於「沒傷害」。所以兩組組態都指定 chinese_cht。
    #
    # ⚠ RapidOCR 的 PP-OCRv6 只有 `multi_PP-OCRv6_*` 一顆多語模型，
    #    `lang_type` 在那一版只是**合法性檢查**，不會換到另一顆權重
    #    （見 `utils/model_resolver.py` 的 model_key_template）。
    #    真正的繁體專用權重只存在於 v4 那一組（rec 是 v3 的，10.6 MB）。
    CFGS = {
        # 最接近本專案 production（PP-OCRv6 ＋ 2048 邊長 ＋ box_thresh 0.4）
        # Fog 降階候選：v6 small。比 medium 小、快，準確度代價待量。
        'v6s': {'Det.ocr_version': V6, 'Det.model_type': SMALL,
                'Det.lang_type': 'chinese_cht',
                'Rec.ocr_version': V6, 'Rec.model_type': SMALL,
                'Rec.lang_type': 'chinese_cht'},
        'v6': {'Det.ocr_version': V6, 'Det.model_type': MEDIUM,
               'Det.lang_type': 'chinese_cht',
               'Rec.ocr_version': V6, 'Rec.model_type': MEDIUM,
               'Rec.lang_type': 'chinese_cht'},
        # 繁體專用權重，模型最小——Pi 這條線真正該看的那一組
        'cht': {'Det.ocr_version': V4, 'Det.model_type': MOBILE,
                'Det.lang_type': 'ch',
                'Rec.ocr_version': V4, 'Rec.model_type': MOBILE,
                'Rec.lang_type': 'chinese_cht'},
    }
    params = dict(CFGS[a.cfg])
    params.update({'Det.limit_side_len': a.side, 'Det.limit_type': 'max',
                   'Det.box_thresh': 0.4})
    # GPU 只影響速度，不影響輸出——要看準確度就用 GPU 跑，
    # 要回答「Pi 跑不跑得動」才需要 --cpu 的那組數字。
    if a.gpu:
        params['EngineConfig.onnxruntime.use_cuda'] = True
    # ⚠ 比較延遲時**兩邊的執行緒數必須一樣**。Pi 5 是四核，所以 --threads=4
    #    才是那條線該引用的數字；不限制的話 RapidOCR 會吃滿全部核心，
    #    跟限 4 執行緒的 PP-OCR 比就不公平（2026-09-10 第一版就犯了這個錯）。
    if a.threads:
        params['EngineConfig.onnxruntime.intra_op_num_threads'] = a.threads
        params['EngineConfig.onnxruntime.inter_op_num_threads'] = a.threads
    t0 = time.time()
    ocr = RapidOCR(params=params)
    load_s = time.time() - t0
    print('組態 %s  模型載入  %.0f MB   %.1fs' % (a.cfg, rss(), load_s))

    cases = json.load(open(os.path.join(EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    outdir = os.path.join(HERE, 'out', a.out)
    os.makedirs(outdir, exist_ok=True)
    peak = rss()
    times, nl = [], []
    done = 0
    for c in cases:
        cid = c['case_id']
        rec = {'case_id': cid, 'preset': a.out,
               'set_version': c.get('set_version'),
               'category': c.get('category'), 'images': []}
        for rel in (c.get('images') or []):
            f = os.path.join(EVAL_ROOT, rel.replace('/', os.sep))
            if not os.path.exists(f):
                alt = os.path.splitext(f)[0] + '.jpg'
                if not os.path.exists(alt):
                    continue
                f = alt
            t0 = time.time()
            try:
                r = ocr(f)
            except Exception as e:
                print('  ! %s %s' % (cid, str(e)[:70]))
                continue
            dt = time.time() - t0
            lines = []
            if r is not None and getattr(r, 'txts', None):
                for box, txt, sc in zip(r.boxes, r.txts, r.scores):
                    lines.append({'text': txt,
                                  'score': round(float(sc), 4),
                                  'box': to_box(box)})
            rec['images'].append({'path': rel, 'elapsed_s': round(dt, 2),
                                  'n_lines': len(lines), 'lines': lines})
            times.append(dt)
            nl.append(len(lines))
            peak = max(peak, rss())
        with open(os.path.join(outdir, cid + '.json'), 'w', encoding='utf-8') as fh:
            json.dump(rec, fh, ensure_ascii=False)
        done += 1
        if done % 20 == 0:
            print('  %3d 案　累計 %d 張　中位 %.1fs' % (done, len(times),
                                                    statistics.median(times)))
        if a.limit and done >= a.limit:
            break
    print('\n寫出 %d 案 -> %s' % (done, outdir))
    if times:
        times_s = sorted(times)
        print('峰值 RSS  %.0f MB   （PP-OCR 同機為 819 MB）' % peak)
        print('單張耗時  中位 %.2fs  p95 %.2fs  最慢 %.2fs'
              % (statistics.median(times_s),
                 times_s[int(.95 * (len(times_s) - 1))], times_s[-1]))
        print('每張行數  中位 %.0f' % statistics.median(nl))


if __name__ == '__main__':
    main()
