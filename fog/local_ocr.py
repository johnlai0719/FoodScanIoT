"""Cloud 連不上時，Fog 以本機 OCR 產出部分結果（降階）。

流程：label_images（base64）→ RapidOCR PP-OCRv6 small → 成分段定位與切分
（PPOCR_TEST/bench_ingredients.p_boxsep）→ 添加物比對
（server/module_a.match_ingredients，與 Cloud 同一份規則，資料來自 seed_db）。
回應格式見 transforms.build_degraded_local_response()。

**重量級依賴（rapidocr、numpy、PPOCR_TEST、module_a）全部延遲到 warm_up() 才載入。**
tests/contract/test_health_endpoints.py 會在 CI 上實際載入 fog/main.py，而 CI 不裝這些。

**依賴 PPOCR_TEST/，而它目前只存在於 feat/vlcrop-pipeline 分支。** 部署腳本會把 Pi
重設成 origin/main；那時 warm_up() 找不到它就停用降階，行為退回原本的 504，服務照常啟動。

模型檔在 RapidOCR 第一次初始化時才從網路下載，所以必須在啟動時暖機——
降階正是在連不上外部時才會用到，不能等到那時才下載。
"""
import base64
import sys
import threading
import time
from pathlib import Path

from transforms import build_degraded_local_response

REPO = Path(__file__).resolve().parents[1]
PPOCR_DIR = REPO / "cloud" / "vision"
SERVER_DIR = REPO / "cloud"

ENGINE = {
    "ocr": "RapidOCR PP-OCRv6 small (onnxruntime CPU)",
    "extractor": "cloud/vision/bench_ingredients.p_boxsep",
    "matcher": "cloud/module_a/ingredient_matching.match_ingredients",
    "additive_db": "cloud/seed_data/reference_seed.sql",
}

_status = {"state": "not_started", "detail": ""}
# 一次只辨識一個請求：Pi 5 連續跑 OCR 會燒到約 85°C 並降頻，併發只會讓每一筆都變慢。
_lock = threading.Lock()
_ocr = _extract = _match = _cursor_factory = _db = None


def status() -> dict:
    return dict(_status)


def is_ready() -> bool:
    return _status["state"] == "ready"


def warm_up():
    """載入模型與比對資料。失敗只停用降階，不拋出——Fog 的其他功能不該因此受影響。"""
    global _ocr, _extract, _match, _cursor_factory, _db
    _status.update(state="loading", detail="")
    t0 = time.time()
    try:
        if not (PPOCR_DIR / "bench_ingredients.py").exists():
            raise RuntimeError(f"找不到 {PPOCR_DIR / 'bench_ingredients.py'}（辨識模組應在 cloud/vision/）")
        # server/ 接在 sys.path 最後：它有自己的 main.py 與 version.py，排前面會遮蔽 Fog 的
        # （見 tests/contract/conftest.py）。PPOCR_TEST 會由 bench_ingredients 自己插到最前面，
        # 已確認它沒有與 fog／server 同名的模組。
        if str(SERVER_DIR) not in sys.path:
            sys.path.append(str(SERVER_DIR))
        sys.path.append(str(PPOCR_DIR))

        from rapidocr import ModelType, OCRVersion, RapidOCR
        import bench_ingredients as BI
        from module_a.ingredient_matching import match_ingredients
        from seed_db import RealDictLikeCursor, load_additives_db

        # 字典檔讀不到時 load_dict() 會回空清單，切分品質靜默下降——這裡改成直接停用。
        if not BI.load_dict():
            raise RuntimeError("cloud/vision/data/ingredient_dict.json 讀不到")

        # 與 PPOCR_TEST/run_rapidocr.py 的 CFGS['v6s'] ＋ side 2048、box_thresh 0.4、--threads 4 相同，
        # 即 2026-09-11 在 v4.0（177 案）量過的組態。
        v6, small = OCRVersion.PPOCRV6, ModelType.SMALL
        ocr = RapidOCR(params={
            "Det.ocr_version": v6, "Det.model_type": small, "Det.lang_type": "chinese_cht",
            "Rec.ocr_version": v6, "Rec.model_type": small, "Rec.lang_type": "chinese_cht",
            "Det.limit_side_len": 2048, "Det.limit_type": "max", "Det.box_thresh": 0.4,
            "EngineConfig.onnxruntime.intra_op_num_threads": 4,
            "EngineConfig.onnxruntime.inter_op_num_threads": 4,
        })
        db = load_additives_db()
        # module_a 的類別統稱與物質名快取在第一次呼叫時才從 cursor 載入，先跑一次。
        match_ingredients(["水"], None, RealDictLikeCursor(db), None)

        _ocr, _extract, _match, _cursor_factory, _db = (
            ocr, BI.p_boxsep, match_ingredients, RealDictLikeCursor, db)
        _status.update(state="ready", detail=f"warm-up {time.time() - t0:.1f}s")
    except Exception as e:
        _status.update(state="disabled", detail=f"{type(e).__name__}: {e}")
    print(f"[LOCAL-OCR] {_status['state']} {_status['detail']}")


def _decode(b64: str) -> bytes:
    # 與 server/main.py 的 analyze_image_with_gemini() 同一條規則：可帶 data URL 前綴。
    if "base64," in b64:
        b64 = b64.split("base64,")[1]
    return base64.b64decode(b64)


def analyze(label_images: list, barcode) -> dict:
    """辨識 label_images，回傳 build_degraded_local_response() 的結果。呼叫前須確認 is_ready()。"""
    t0 = time.time()
    with _lock:
        images = []
        for i, b64 in enumerate(label_images):
            lines = []
            try:
                r = _ocr(_decode(b64))
                if r is not None and r.txts:
                    for box, text in zip(r.boxes, r.txts):
                        lines.append({"text": text,
                                      "box": [[int(round(float(x))), int(round(float(y)))]
                                              for x, y in box]})
            except Exception as e:
                print(f"[LOCAL-OCR] label_images[{i}] 無法辨識：{type(e).__name__}: {e}")
            images.append({"path": f"label_images[{i}]", "lines": lines})

        # 抽取器的 gt 參數一律傳 None（與 PPOCR_TEST/emit_json.py 相同），用到就會當場報錯而非靜默。
        items = _extract(barcode or "fog", {"images": images}, None) or []
        m = _match(items, None, _cursor_factory(_db), None)

    return build_degraded_local_response(
        barcode, m["chemical"] + m["basic_detail"], time.time() - t0, ENGINE)
