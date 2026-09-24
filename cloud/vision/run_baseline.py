#!/usr/bin/env python3
# PPOCR baseline：對量化測試集 v2.1 的照片跑 PP-OCR，把原始行級輸出存下來。
#
# 這支只負責「跑」與「存」，不做任何評分——評分在 score_ocr.py。
# 分開的理由：跑一次要吃 GPU 與時間，評分規則卻會反覆改；混在一起會逼你
# 每改一次指標就重跑推論。
#
# 為什麼要有 baseline：2026-08 之前的結論「PPOCR 效果很差」來自
# 測試/圖片掃描處理/萃取結果報告_PaddleOCR.md，而那份報告的輸入有兩個問題：
#   1. 用的是 254x400 / 290x400 這批早期網路圖，不是 App 實際送出的手機照
#   2. local_ocr_api.py:239 對輸入做 cv2.resize(w*3, h*3, INTER_CUBIC)，
#      在已經很小的圖上放大馬賽克，不會生出資訊
# 再加上 PaddleOCR 自己的 text_det_limit_side_len 預設 960 + limit_type='max'，
# 3024x4032 的照片進偵測前會被縮到 720x960。三件事疊起來，那份報告量到的
# 不是「PPOCR 的能力上限」。所以 fine-tune 之前要先有一條乾淨的基準線。
#
# 用法：
#   python run_baseline.py --preset=v5_default            # 復刻舊設定
#   python run_baseline.py --preset=v5_hires              # 解除偵測端降採樣
#   python run_baseline.py --preset=v5_hires --cases c02_濃豆漿 c24
#   python run_baseline.py --preset=v5_server_hires --limit=10
#
# 輸出：out/<preset>/<case_id>.json
import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_ROOT = os.path.abspath(
    os.path.join(HERE, "..", "測試", "量化測試")
)


# ─── 設定組 ──────────────────────────────────────────────────────────────────
# init  = PaddleOCR() 的建構參數；predict = .predict() 的每次呼叫參數。
# 分開是因為換 predict 參數不必重載模型，之後要做參數掃描會用到。
#
# 三組的差異只有「偵測端拿到多大的圖」與「用 mobile 還是 server 模型」。
# 刻意不加影像前處理（銳化、二值化、upscale）——先確定原生能力在哪裡，
# 否則之後 fine-tune 的增益會跟前處理的增益混在一起，歸因不了。
PRESETS = {
    # 復刻 2026-08 舊報告的條件：PP-OCRv5 + chinese_cht + 全預設。
    # limit_side_len 未指定 → 沿用預設 960/max，高解析照片會被縮小。
    "v5_default": {
        "init": {
            "lang": "chinese_cht",
            "ocr_version": "PP-OCRv5",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        },
        "predict": {},
    },
    # 只改一件事：讓偵測端看到接近原尺寸的圖。
    # 與 v5_default 的差異可直接歸因於解析度。
    "v5_hires": {
        "init": {
            "lang": "chinese_cht",
            "ocr_version": "PP-OCRv5",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        },
        "predict": {
            "text_det_limit_side_len": 2048,
            "text_det_limit_type": "max",
        },
    },
    # PP-OCRv6（需要 paddleocr >= 3.6；3.5.0 只到 v5）。
    # lang=chinese_cht 在 v6 一樣是空操作——ch / chinese_cht / japan 全部指到
    # PP-OCRv6_medium_det + PP-OCRv6_medium_rec（見 _pipelines/ocr.py 的
    # _PPOCRV6_LANGS 分支）。v6 沒有 server 級，medium 是最大的一檔。
    # 其餘設定與 v5_hires 對齊，差異才能歸因於模型版本。
    "v6_hires": {
        "init": {
            "lang": "chinese_cht",
            "ocr_version": "PP-OCRv6",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        },
        "predict": {
            "text_det_limit_side_len": 2048,
            "text_det_limit_type": "max",
        },
    },
    # **目前最佳設定**（2026-08-15 參數掃描結果，見 sweep_params.py）。
    # 與 v6_hires 只差 text_det_box_thresh 從預設 0.6 降到 0.4：
    #   沒讀到 82→65、營養數值漏 70→61、完全命中 623→630，而且更快（0.71→0.67s）
    # 小字的偵測回應本來就弱，門檻放寬才收得到。0.3 與 0.4 主要指標相同，
    # 取 0.4 是因為 0.3 多出 126 個框（誤判較多）而沒有換到更好的分數。
    #
    # 掃描中被推翻的兩個直覺，記在這裡以免有人重試：
    #   - use_doc_unwarping=True 反而災難（沒讀到 82→144，慢 5 倍）
    #   - 解析度拉更高更糟（side3072 86、side4096 95，都不如 2048 的 82）
    "v6_best": {
        "init": {
            "lang": "chinese_cht",
            "ocr_version": "PP-OCRv6",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        },
        "predict": {
            "text_det_limit_side_len": 2048,
            "text_det_limit_type": "max",
            "text_det_box_thresh": 0.4,
        },
    },
    # 與 v6_best 只差 use_doc_orientation_classify=True（整張圖 0/90/180/270 轉正）。
    #
    # 為什麼要單獨測：一直和 use_doc_unwarping 被混為一談，但兩者不同——
    # unwarping 是攤平扭曲（已實測災難：沒讀到 82→144、慢 5 倍），
    # orientation 只是把整張圖轉正。而方向這件事在 PPOCR 裡沒有其他補救管道：
    # 每個框轉不轉 90 度是 crop_image_regions.py:206 寫死的
    # `if h / w >= 1.5: rot90`，逐框獨立、純幾何、學不到也調不了。
    # 近正方形的短格子（營養表的「熱量」「0」「克」）幾何上就不帶方向資訊，
    # 唯一能給它方向的就是把整張圖先轉正。
    "v6_docori": {
        "init": {
            "lang": "chinese_cht",
            "ocr_version": "PP-OCRv6",
            "use_doc_orientation_classify": True,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        },
        "predict": {
            "text_det_limit_side_len": 2048,
            "text_det_limit_type": "max",
            "text_det_box_thresh": 0.4,
        },
    },
    # 只換 rec 到 PP-OCRv4_server_rec_doc，det 維持 v6_medium。
    #
    # 為什麼值得一試：官方說明是「以更多中文文件資料訓練，支援 15000+ 字元，
    # 強化繁體中文、日文與特殊字元」。而 v6 只有 tiny/small/medium 三檔、沒有
    # server 級，medium 是最大的。所以這一組是拿「舊世代但更大、且文件資料更多」
    # 去換「新世代但較小」。
    #
    # **實測結果：明顯變差，不要用**（2026-08-16，58 案 789 個成分）：
    #     v6_medium（現用）  完全命中 630｜近似 94｜沒讀到 65
    #     v4_server_rec_doc  完全命中 549｜近似 151｜沒讀到 89
    # 少 81 項完全命中、多 24 項沒讀到。世代差距蓋過了「字元集更大、
    # 文件資料更多」的優勢——跟 v5→v6 升級一次就贏過整個 rec 微調是同一個道理。
    # 保留這一組是為了留下記錄：「換一個號稱強化繁體的舊模型」已經試過了。
    #
    # 附帶推翻的假設：繁體輸出不是字元集問題。三個候選模型的字元集都含
    # 麵臺藥灣（v6_medium 18708 字、v4_server_rec_doc 15629、cht_v3_mobile 8421），
    # v6 能輸出繁體，只是訓練資料偏簡體所以傾向輸出簡體。而簡繁差異對下游
    # 添加物判定的影響實測為 0（見 sim_match.py）。
    "v6det_v4recdoc": {
        "init": {
            "lang": "chinese_cht",
            "ocr_version": "PP-OCRv6",
            "text_recognition_model_name": "PP-OCRv4_server_rec_doc",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        },
        "predict": {
            "text_det_limit_side_len": 2048,
            "text_det_limit_type": "max",
            "text_det_box_thresh": 0.4,
        },
    },
    # 2026-08-15 det 微調（7 張自標圖，可行性測試）。**只換 det**，rec 維持 v6_best，
    # 這樣差異可歸因於 det 訓練。過擬合檢查顯示：訓練集 hmean 0.607→0.637（學到了）、
    # 驗證集 0.580→0.563（沒泛化）——典型的資料量不足，不是方法錯。
    # 這一組是拿來確認「有沒有把原本好的地方弄壞」，不是期待它變好。
    "v6_det_ft": {
        "init": {
            "lang": "chinese_cht",
            "ocr_version": "PP-OCRv6",
            "text_detection_model_name": "PP-OCRv6_medium_det",
            "text_detection_model_dir": os.path.join(
                HERE, "output", "det_foodlabel_ft", "inference"),
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        },
        "predict": {
            "text_det_limit_side_len": 2048,
            "text_det_limit_type": "max",
            "text_det_box_thresh": 0.4,
        },
    },
    # fine-tune 後的 rec ＋ 原本的 det。**只換 rec**，det 保持與 v5_hires 相同，
    # 這樣 score_ocr.py --compare v5_hires v5_ft 量到的差異就只來自 rec 訓練。
    # 需要先把訓練產物轉成推論格式：
    #   cd ../PaddleOCR && python tools/export_model.py \
    #       -c ../PPOCR_TEST/configs/rec_foodlabel_ft.yml \
    #       -o Global.pretrained_model=../PPOCR_TEST/output/rec_foodlabel_ft/best_accuracy \
    #          Global.save_inference_dir=../PPOCR_TEST/output/rec_foodlabel_ft/inference
    "v5_ft": {
        "init": {
            "lang": "chinese_cht",
            "ocr_version": "PP-OCRv5",
            "text_recognition_model_name": "PP-OCRv5_server_rec",
            "text_recognition_model_dir": os.path.join(
                HERE, "output", "rec_foodlabel_ft", "inference"),
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        },
        "predict": {
            "text_det_limit_side_len": 2048,
            "text_det_limit_type": "max",
        },
    },
    # server 模型（較大、較準、較慢）＋ 高解析。這組是「不 fine-tune 的天花板」，
    # 用來回答「還有多少差距真的需要靠訓練來補」。
    "v5_server_hires": {
        "init": {
            "lang": "chinese_cht",
            "ocr_version": "PP-OCRv5",
            "text_detection_model_name": "PP-OCRv5_server_det",
            "text_recognition_model_name": "PP-OCRv5_server_rec",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        },
        "predict": {
            "text_det_limit_side_len": 2048,
            "text_det_limit_type": "max",
        },
    },
}


def load_cases(selectors, limit):
    with open(os.path.join(EVAL_ROOT, "cases.json"), encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    if selectors:
        # 前綴比對，讓 --cases c02 就能選到 c02_濃豆漿
        cases = [c for c in cases if any(c["case_id"].startswith(s) for s in selectors)]
    if limit:
        cases = cases[:limit]
    return cases


def extract_lines(res):
    """把 PaddleOCR 3.x 的結果物件攤平成 [{text, score, box}]。

    3.x 的 res 是 dict-like，欄位為 rec_texts / rec_scores / rec_polys。
    舊版 2.x 的巢狀 list 格式已不適用，若之後降版本這裡要改。
    """
    d = res.json["res"] if hasattr(res, "json") else res
    texts = d.get("rec_texts", [])
    scores = d.get("rec_scores", [])
    polys = d.get("rec_polys") or d.get("dt_polys") or []
    lines = []
    for i, t in enumerate(texts):
        box = polys[i] if i < len(polys) else None
        if box is not None and not isinstance(box, list):
            box = box.tolist()
        lines.append(
            {
                "text": t,
                "score": round(float(scores[i]), 4) if i < len(scores) else None,
                "box": box,
            }
        )
    return lines


# 影像根目錄。壓縮實驗用 `EVAL_IMAGE_ROOT=images_1280q80` 切換。
# ⚠ 它是**前綴**不是替換：`compress_images.py` 產出的是
# `images_1280q80/images/beverage/...`，把 `images/` 換掉會找不到檔案。
# 與 `測試/量化測試/run_eval.py`（`os.path.join(IMAGE_ROOT, rel)`）的慣例一致。
# 2026-09-09 第一版寫成替換，PP-OCR 在 177 案上讀出 0 行、**exit 0 不報錯**。
IMAGE_ROOT = os.environ.get("EVAL_IMAGE_ROOT") or ""


def _img(rel):
    """把 cases.json 的相對路徑解到 IMAGE_ROOT 底下。

    ⚠ **副檔名不符時回退到 .jpg。** `compress_images.py` 一律存成 JPEG，
    而測試集裡有 3 張 .webp 與 1 張 .jpeg（c03／c06／c08／c17），
    壓縮後變成 .jpg。沿用 `run_eval.py` 的同一條回退規則。
    2026-09-09 沒有這條的後果：run_baseline 把那 4 張標成 `missing` 後
    **繼續跑完並回報成功**，run_vlcrop 則直接崩在 FileNotFoundError。
    """
    if not IMAGE_ROOT:
        return rel
    p = os.path.join(IMAGE_ROOT, rel)
    if not os.path.exists(os.path.join(EVAL_ROOT, p.replace("/", os.sep))):
        alt = os.path.splitext(p)[0] + ".jpg"
        if os.path.exists(os.path.join(EVAL_ROOT, alt.replace("/", os.sep))):
            return alt
    return p

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="v5_hires", choices=list(PRESETS))
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--device", default="gpu")
    # 檢查點掃描用：合成資料上的 val acc 會飽和到 ~1.0，選不出好 checkpoint，
    # 只能拿真實 58 案逐個試。這兩個參數讓同一支腳本能跑不同 checkpoint 的匯出。
    ap.add_argument("--rec-dir", default=None, help="覆寫 rec 推論模型目錄")
    ap.add_argument("--out-name", default=None, help="覆寫輸出目錄名（預設同 preset）")
    args = ap.parse_args()

    cfg = PRESETS[args.preset]
    if args.rec_dir:
        cfg = {"init": dict(cfg["init"]), "predict": dict(cfg["predict"])}
        cfg["init"]["text_recognition_model_dir"] = os.path.abspath(args.rec_dir)
    cases = load_cases(args.cases, args.limit)
    outdir = os.path.join(HERE, "out", args.out_name or args.preset)
    os.makedirs(outdir, exist_ok=True)

    print(f"preset={args.preset}  cases={len(cases)}  device={args.device}")
    print(f"init   : {cfg['init']}")
    print(f"predict: {cfg['predict'] or '(defaults)'}")

    from paddleocr import PaddleOCR

    t0 = time.time()
    ocr = PaddleOCR(device=args.device, **cfg["init"])
    print(f"模型載入 {time.time() - t0:.1f}s\n")

    total_lines = 0
    for n, case in enumerate(cases, 1):
        cid = case["case_id"]
        record = {
            "case_id": cid,
            "preset": args.preset,
            "set_version": case.get("set_version"),
            "category": case.get("category"),
            "images": [],
        }
        for rel in case["images"]:
            path = os.path.join(EVAL_ROOT, _img(rel))
            if not os.path.exists(path):
                record["images"].append({"path": rel, "error": "missing"})
                continue
            t = time.time()
            try:
                results = ocr.predict(path, **cfg["predict"])
            except Exception as e:  # 單張失敗不該中斷整批
                record["images"].append({"path": rel, "error": repr(e)})
                continue
            lines = []
            for res in results:
                lines.extend(extract_lines(res))
            total_lines += len(lines)
            record["images"].append(
                {
                    "path": rel,
                    "elapsed_s": round(time.time() - t, 2),
                    "n_lines": len(lines),
                    "lines": lines,
                }
            )
        with open(os.path.join(outdir, f"{cid}.json"), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=1)
        got = sum(i.get("n_lines", 0) for i in record["images"])
        print(f"[{n}/{len(cases)}] {cid}  {got} 行")

    print(f"\n共 {total_lines} 行 → {outdir}")


if __name__ == "__main__":
    main()
