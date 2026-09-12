"""vlcrop 的線上推論入口：一次請求的圖片 → 與 Gemini 相同形狀的欄位 dict。

**這支不重寫任何辨識邏輯。** 它做的事是把 `PPOCR_TEST/` 那些批次腳本要的
檔案佈局在一個暫時工作區裡擺出來，然後呼叫它們既有的函式。

為什麼用「合成一個假的測試集目錄」而不是把邏輯抄過來改成記憶體版：
本專案已經因為「同一份知識放兩個地方」被咬過三次（族群詞彙、撇號正規化、
添加物分母），evaluation 與 production 各一份實作是最貴的那種重複。
走檔案雖然醜，但**跑的是與評估報告完全同一份程式碼**，不會分岔。

資料流（與 run_vlcrop.py + emit_json.py 的批次流程逐步對應）：

    圖片 → PP-OCR v6_best             → out/_online_boxes/<cid>.json
         → region_crop 找框
         → HunyuanOCR 逐區讀（:8177）  → out/_online_vl/<cid>.json
         → PPStructureV3 ＋ 約束求解    → out/_online_nutri/<cid>.json
         → Qwen 整理品名／廠商（:8179） → out/_online_lf/<cid>.json
         → emit_json.build(cid)         → 最終欄位

⚠ **paddle 與 torch 不可同環境**（見「OCR 層已用盡」）。本服務只用 paddle 側；
   切分模型（bertsplit）走 torch，線上一律用規則 `boxsep`，與 emit_json 的
   預設相同。

⚠ 模型服務要先起來：HunyuanOCR :8177、Qwen :8179。`/health` 會回報。
"""
import base64
import io as _io
import json
import os
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PPOCR = REPO / "PPOCR_TEST"

# ── 環境變數必須在 import 那些模組「之前」設好 ────────────────────────────
# 它們全是在模組層讀 os.environ 的（BOXES、NUTRI_OUT、EMIT_READERS…），
# import 之後再改不會生效。本專案踩過九次「預設值沒設就靜默走舊路」，
# 這裡是刻意用同一個機制把整條線指向線上工作區。
WORK = HERE / "_work"
PRESET_BOXES = "_online_boxes"
PRESET_VL = "_online_vl"
PRESET_NUTRI = "_online_nutri"
PRESET_LF = "_online_lf"

os.environ.setdefault("EVAL_ROOT", str(WORK))
os.environ.setdefault("PPOCR_BOXES", PRESET_BOXES)
os.environ.setdefault("NUTRI_BASE", PRESET_VL)
os.environ.setdefault("NUTRI_OUT", PRESET_NUTRI)
os.environ.setdefault("LF_OUT", PRESET_LF)
os.environ.setdefault("EMIT_LOCALFIELDS", PRESET_LF)
# 過敏原／廠商／品名合併讀兩個讀取器（理由見 emit_json.py:90）。
# 線上兩份都是現算的，所以指向本次的 VL 與 PP-OCR 輸出。
os.environ.setdefault("EMIT_READERS", PRESET_VL + "," + PRESET_BOXES)
# local_fields 另有自己的一份讀取器清單（LF_READERS），預設也是舊目錄。
os.environ.setdefault("LF_READERS", PRESET_BOXES + "," + PRESET_VL)

# 一次只跑一件：8GB 的 4060 同時載 PP-OCR、PPStructureV3 與兩個 llama-server
# 已經很緊，併發只會讓每一筆都變慢（與 fog/local_ocr.py 的 _lock 同理由）。
_lock = threading.Lock()
_state = {"state": "not_started", "detail": ""}
_ocr = None
_pp = None
_mods = {}


def status():
    return dict(_state)


def is_ready():
    return _state["state"] == "ready"


def warm_up():
    """載入 paddle 模型並 import 批次腳本。失敗只記錄狀態，不拋出。"""
    global _ocr, _pp
    _state.update(state="loading", detail="")
    t0 = time.time()
    try:
        WORK.mkdir(parents=True, exist_ok=True)
        (WORK / "images").mkdir(exist_ok=True)
        # cases.json 先放空的：nutrition_pipeline.run() 會讀它，
        # 每次請求前再改寫成只含本次那一案。
        _write_cases([])

        import sys
        if str(PPOCR) not in sys.path:
            sys.path.insert(0, str(PPOCR))

        from paddleocr import PaddleOCR, PPStructureV3
        import run_baseline as RB
        import run_vlcrop as RV
        import region_crop as RC
        import nutrition_pipeline as N
        import local_fields as LF
        import emit_json as EJ

        cfg = RB.PRESETS["v6_best"]
        _ocr = PaddleOCR(device="gpu", **cfg["init"])
        _pp = PPStructureV3(use_doc_orientation_classify=False,
                            use_doc_unwarping=False, device="gpu")
        # 讀取器固定用 HunyuanOCR——vlcrop_hy 是已定案的組合
        # （決策單 #14，依據是佐證率與營養格，不是總分）。
        RV.CFG = RV.BACKENDS["hunyuan"]

        _mods.update(RB=RB, RV=RV, RC=RC, N=N, LF=LF, EJ=EJ,
                     predict=cfg["predict"])
        _state.update(state="ready", detail="warm-up %.1fs" % (time.time() - t0))
    except Exception as e:
        _state.update(state="failed", detail="%s: %s" % (type(e).__name__, e))


def _write_cases(cases):
    (WORK / "cases.json").write_text(
        json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")


def _out(preset, cid):
    d = PPOCR / "out" / preset
    d.mkdir(parents=True, exist_ok=True)
    return d / (cid + ".json")


def _decode(b64):
    from PIL import Image
    if "base64," in b64:
        b64 = b64.split("base64,")[1]
    return Image.open(_io.BytesIO(base64.b64decode(b64))).convert("RGB")


def read(base64_images, barcode="unknown"):
    """一次請求。回傳的 dict 形狀與 analyze_image_with_gemini 相同。"""
    if not is_ready():
        raise RuntimeError("reader 未就緒：%s" % _state)
    cid = "online_" + uuid.uuid4().hex[:12]
    rels = []
    try:
        with _lock:
            imgs = [_decode(b) for b in base64_images]
            if not imgs:
                raise ValueError("沒有圖片")
            for i, im in enumerate(imgs):
                rel = "images/%s_%d.jpg" % (cid, i)
                im.save(WORK / rel, "JPEG", quality=95)
                rels.append(rel)
            _write_cases([{"case_id": cid, "category": "online",
                           "images": rels, "set_version": "online"}])
            _ppocr(cid, rels)
            _vlcrop(cid, rels, imgs)
            _nutrition(cid)
            _localfields(cid)
            out = _mods["EJ"].build(cid)
        out.setdefault("_meta", {})["reader"] = "vlcrop_hy (PP-OCR→HunyuanOCR→Qwen)"
        out["_meta"]["barcode"] = barcode
        return out
    finally:
        _cleanup(cid, rels)


def _ppocr(cid, rels):
    """第一步：PP-OCR v6_best。輸出格式與 run_baseline.py 寫出的相同。"""
    RB = _mods["RB"]
    images = []
    for rel in rels:
        lines = []
        for r in _ocr.predict(str(WORK / rel), **_mods["predict"]):
            lines.extend(RB.extract_lines(r))
        images.append({"path": rel, "lines": lines, "n_lines": len(lines)})
    _out(PRESET_BOXES, cid).write_text(
        json.dumps({"case_id": cid, "preset": PRESET_BOXES, "images": images},
                   ensure_ascii=False), encoding="utf-8")


def _vlcrop(cid, rels, imgs):
    """第二步：region_crop 找框 → HunyuanOCR 逐區讀。

    與 run_vlcrop.main() 的每案迴圈逐行對應，只是圖片來自記憶體。
    `_linecls_probs` 那條分支不走——它要折外預測的預存檔，線上沒有，
    且逐行分類器的代理指標在端到端上無預測力（見「定位層已用盡」）。
    """
    RV = _mods["RV"]
    RC = _mods["RC"]
    ppocr = json.loads(_out(PRESET_BOXES, cid).read_text(encoding="utf-8"))
    images = []
    for im_rec, img in zip(ppocr["images"], imgs):
        lines = im_rec.get("lines") or []
        r = RC.find_regions(lines)
        all_lines, meta, secs = [], {}, 0.0
        for kind in ("ingredients", "nutrition"):
            box = r[kind]
            if box is None:
                meta[kind] = {"found": False}
                continue
            b = RV.read_region(img, lines, box, RV.CFG[kind], 3072)
            all_lines.extend(b["lines"])
            secs += b["secs"]
            meta[kind] = {"found": True, "rot": b["rot"], "finish": b["finish"],
                          "n_lines": len(b["lines"]), "n_cjk": b["n"],
                          "box": [int(v) for v in box]}
        images.append({"path": im_rec["path"], "elapsed_s": round(secs, 2),
                       "n_lines": len(all_lines),
                       "lines": [{"text": t, "score": None, "box": None}
                                 for t in all_lines],
                       "regions": meta})
    _out(PRESET_VL, cid).write_text(
        json.dumps({"case_id": cid, "preset": PRESET_VL, "images": images},
                   ensure_ascii=False), encoding="utf-8")


def _nutrition(cid):
    """第三步：PPStructureV3 表格 ＋ 約束求解，沿用 nutrition_pipeline.run()。

    它自己會讀 cases.json 與 out/<NUTRI_BASE>/，兩者都已備妥。
    """
    # 必須把暖機時建好的 PPStructureV3 傳進去，否則每個請求都重載模型。
    _mods["N"].run("gpu", [cid], pp=_pp)


def _localfields(cid):
    """第四步：Qwen3.5-2B 從 OCR 文字整理出品名與廠商（:8179）。

    決策單 #18 尚未拍板要不要採用，但實測 name／manufacturer 都贏 Gemini
    且幾乎不編造（2 案／4 案 vs 47 案／28 案），而第 0 條原則正是在擋編造。
    """
    _mods["LF"].run_one(cid)   # 回傳 (prediction, 秒數)，這裡只要它寫出檔案


def _cleanup(cid, rels):
    """暫存檔不留。圖片尤其不可留——那會在伺服器上累積使用者的照片，
    而個人化資料不上雲是本專案明文的界線（見 CLAUDE.md「慣例」）。"""
    for rel in rels:
        try:
            (WORK / rel).unlink()
        except OSError:
            pass
    for pre in (PRESET_BOXES, PRESET_VL, PRESET_NUTRI, PRESET_LF):
        try:
            (PPOCR / "out" / pre / (cid + ".json")).unlink()
        except OSError:
            pass
