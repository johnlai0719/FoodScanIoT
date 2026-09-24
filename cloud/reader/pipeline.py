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
import concurrent.futures as _futures
import json
import subprocess
import os
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLOUD = HERE.parent
PPOCR = CLOUD / "vision"   # 辨識模組（2026-09-24 由 PPOCR_TEST/ 抽出執行時需要的部分）

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
# ⚠ emit_json 用的是 **EMIT_BOXES**，不是 PPOCR_BOXES——名字不同的兩個變數
# 指的是同一件事。2026-09-13 只設了後者，emit_json 去讀 out/vlcrop_hy_v4/
# 找不到檔就 return None，**成分欄靜默變空**（端到端拿得到分數與品名，
# 只有添加物是 0，看起來像「這張沒有成分表」）。這是本專案第十次踩
# 「環境變數沒設就靜默走舊路」。成分是從 VL 輸出切出來的，所以指 VL。
os.environ.setdefault("EMIT_BOXES", PRESET_VL)
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

        # paddle 跑哪裡可切換（PADDLE_DEVICE，預設 gpu）。
        # 2026-09-13 量到：paddle 常駐 GPU 時 HunyuanOCR 由 3.8 秒變 28.9 秒
        # （同一張圖、同兩區，評估時的檔案裡查得到 3.8）。評估時 run_vlcrop
        # 那個行程完全沒載 paddle，GPU 上只有 llama-server 一個房客。
        dev = os.environ.get("PADDLE_DEVICE", "gpu")
        cfg = RB.PRESETS["v6_best"]
        _ocr = PaddleOCR(device=dev, **cfg["init"])
        # 關掉用不到的子管線。PPStructureV3 預設會載公式辨識
        # （PP-FormulaNet_plus-L）、印章、圖表等模型，食品標示一個都用不到，
        # 而它們吃的是最稀缺的東西：8GB 卡上的 VRAM。
        # ⚠ use_table_recognition 必須留著——營養表的結構化就靠它
        #   （[[07-營養表結構化解析實驗]]，營養格 2121 → 2347）。
        # ⚠ use_region_detection 沒動：它會改變輸出結構，不是純粹的省資源。
        _pp = PPStructureV3(use_doc_orientation_classify=False,
                            use_doc_unwarping=False,
                            use_seal_recognition=False,
                            use_formula_recognition=False,
                            use_chart_recognition=False,
                            device=dev)
        # 讀取器固定用 HunyuanOCR——vlcrop_hy 是已定案的組合
        # （決策單 #14，依據是佐證率與營養格，不是總分）。
        RV.CFG = RV.BACKENDS["hunyuan"]

        _mods.update(RB=RB, RV=RV, RC=RC, N=N, LF=LF, EJ=EJ,
                     predict=cfg["predict"])

        # ⚠ **建立模型不等於暖好。** paddle／cuDNN 的核心選擇是在**第一次真正
        # 推論**時才做的，所以只 new 出物件的話那個成本會落在第一位使用者身上。
        #
        # 2026-09-14 量到：重啟後第一次請求 13.0 秒，其中
        # nutrition_structure 佔 7.16 秒（平常 0.75）；之後四次是
        # 6.2／5.5／5.1／5.0。差的就是這一段。
        #
        # 空跑一張純色小圖把它逼出來。內容不重要——要的是讓 cuDNN 選完核心、
        # 讓記憶體池配置完。失敗不影響服務，只是第一位使用者會慢一次。
        try:
            import numpy as _np
            _dummy = _np.full((640, 480, 3), 255, dtype=_np.uint8)
            _tw = time.time()
            list(_ocr.predict(_dummy, **cfg["predict"]))
            list(_pp.predict(_dummy))
            print("[reader] 空跑暖機 %.1f 秒" % (time.time() - _tw))
        except Exception as _e:
            print("[reader] 空跑暖機略過：%r" % (_e,))

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


# 這個 reader 的身分。**同時只會有一個 reader 聽 8180**（它們搶同一張 GPU），
# 競賽版那支在 feat/adi-multimodal-compliance 的 tools/reader_competition.py。
# Cloud 端的 VISION_BACKEND 只說「走 reader 這條路」，分不出是哪一個，
# 所以身分由 reader 自己在 /health 與 _meta.reader 報出來。切換用
# 該分支的 tools/switch_reader.ps1。
READER_NAME = "vlcrop_hy (PP-OCR→HunyuanOCR→Qwen)"


def read(base64_images, barcode="unknown"):
    """一次請求。回傳的 dict 形狀與 analyze_image_with_gemini 相同。"""
    if not is_ready():
        raise RuntimeError("reader 未就緒：%s" % _state)
    cid = "online_" + uuid.uuid4().hex[:12]
    rels = []
    try:
        # 排隊時間要與處理時間分開。GPU 只有一張，`_lock` 就是那個隊伍；
        # 不獨立量的話它會被算進呼叫端的 vision 分段，看起來像「辨識變慢了」。
        # 2026-09-13 Fog 端實測到這件事：vision=18.7 秒但各段加總只有 10.1 秒，
        # 差的 8.6 秒全是排隊。
        _tq = time.time()
        with _lock:
            queue_s = time.time() - _tq
            imgs = [_decode(b) for b in base64_images]
            if not imgs:
                raise ValueError("沒有圖片")
            for i, im in enumerate(imgs):
                rel = "images/%s_%d.jpg" % (cid, i)
                im.save(WORK / rel, "JPEG", quality=95)
                rels.append(rel)
            _write_cases([{"case_id": cid, "category": "online",
                           "images": rels, "set_version": "online"}])
            # 逐段計時。總時間看不出該優化哪一段——2026-09-13 第一次量到
            # 單張 55 秒時，就是因為只有總數而無法判斷方向。
            t = {"queue": round(queue_s, 2)}
            host_before = _host_sample()
            _t = time.time()
            _ppocr(cid, rels)
            t["ppocr"] = round(time.time() - _t, 2)
            _t = time.time()
            _vlcrop(cid, rels, imgs)
            t["vlcrop_hunyuan"] = round(time.time() - _t, 2)
            _t = time.time()
            _nutrition(cid)
            t["nutrition_structure"] = round(time.time() - _t, 2)
            _t = time.time()
            _localfields(cid)
            t["qwen_fields"] = round(time.time() - _t, 2)
            _t = time.time()
            out = _mods["EJ"].build(cid)
            _assert_sources(out)
            t["assemble"] = round(time.time() - _t, 2)
        out.setdefault("_meta", {})["reader"] = READER_NAME
        out["_meta"]["stage_secs"] = t
        # 前後各取一次。只取一次看不出「這次請求把記憶體撐大了多少」——
        # paddle 的記憶體池會隨用量成長且用過不還（2026-09-14 量到載入
        # 1938 MB、實跑 3851 MB），那個成長本身就是要觀察的對象。
        out["_meta"]["host_before"] = host_before
        out["_meta"]["host_after"] = _host_sample()
        out["_meta"]["barcode"] = barcode
        return out
    finally:
        _cleanup(cid, rels)


def _assert_sources(out):
    """組裝完成後檢查「每一層真的讀到了自己的輸入」。

    emit_json 的每個讀取層找不到檔案時一律回 None 而不報錯（批次跑時那是對的，
    缺一案不該中斷整批），線上卻會變成靜默的空欄位。`_check_sources()` 是
    emit_json 自己的同類防護，但它只在 main() 裡呼叫，build() 沒有。

    這裡只擋**設定錯誤**，不擋「這張圖真的沒有成分表」：
    前者的表徵是 ocr_preset 指向別的目錄，後者 ocr_preset 是對的、只是沒字。
    """
    m = out.get("_meta") or {}
    got = m.get("ocr_preset")
    if got != PRESET_VL:
        raise RuntimeError(
            "emit_json 讀的是 out/%s/ 而不是本次的 out/%s/——"
            "八成是某個環境變數沒設到（它們名字不一致：PPOCR_BOXES／EMIT_BOXES／"
            "NUTRI_BASE／EMIT_READERS／LF_READERS）。成分會靜默變空。" % (got, PRESET_VL))


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
        # 成分區與營養區**並行**送。兩者沒有先後依賴，而 llama-server 以
        # -np 2 開了兩個槽（見 start_models.py）。
        #
        # 2026-09-14 端到端實測（雞腿堡，四次中位）：
        #     vlcrop_hunyuan  7.38 → 2.30 秒
        #     端到端          9.77 → 4.79 秒
        #
        # ⚠ 收益比「兩條並行＝省一半」更大，原因不只是並行：兩個區固定落在
        #   不同的槽，各自的 KV cache 跨請求還在，prefill 大多命中；串行時
        #   兩個提示詞輪流擠同一個槽，每次都要重算。
        # ⚠ **冷啟動時並行反而略慢**（首次量到 3.75 對 3.26 秒）——快取還沒
        #   建立時，兩條序列只是把同一份算力切成兩半。穩態才有收益。
        #
        # 結果要**按固定順序**收集，不可依完成先後：all_lines 的順序會進到
        # 下游的成分解析，順序不穩的話同一張圖每次跑出來的結果會不同。
        todo = [k for k in ("ingredients", "nutrition") if r[k] is not None]
        for kind in ("ingredients", "nutrition"):
            if r[kind] is None:
                meta[kind] = {"found": False}

        # 關掉並行的開關。留著是為了**可比對**——並行只改送出方式、不該改變
        # 輸出；看到可疑結果時要能用同一份輸入跑出串行版對照，否則分不出是
        # 並行造成的還是本來就這樣。
        _parallel = (os.environ.get("READER_PARALLEL_REGIONS") not in ("0", "false")
                     and len(todo) > 1)
        done = {}
        if _parallel:
            with _futures.ThreadPoolExecutor(max_workers=len(todo)) as ex:
                fut = {ex.submit(RV.read_region, img, lines, r[k],
                                 RV.CFG[k], 3072): k for k in todo}
                for f in _futures.as_completed(fut):
                    done[fut[f]] = f.result()
        else:
            for k in todo:
                done[k] = RV.read_region(img, lines, r[k], RV.CFG[k], 3072)

        for kind in todo:                       # 固定順序，不是完成順序
            b = done[kind]
            box = r[kind]
            all_lines.extend(b["lines"])
            # 並行之後各區的耗時是**重疊**的，相加會高估。取最大值——
            # 那才是這一段實際等了多久。
            secs = max(secs, b["secs"])
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
    _mods["N"].run(os.environ.get("PADDLE_DEVICE", "gpu"), [cid], pp=_pp)


def _localfields(cid):
    """第四步：Qwen3.5-2B 從 OCR 文字整理出品名與廠商（:8179）。

    決策單 #18 尚未拍板要不要採用，但實測 name／manufacturer 都贏 Gemini
    且幾乎不編造（2 案／4 案 vs 47 案／28 案），而第 0 條原則正是在擋編造。
    """
    _mods["LF"].run_one(cid)   # 回傳 (prediction, 秒數)，這裡只要它寫出檔案


# 除錯用：設 READER_KEEP=1 時保留本次的中間檔（PP-OCR／VL／營養／欄位），
# 好回頭看「辨識到底讀出什麼」。**預設關閉**——那些檔案含使用者照片轉出的
# 文字，不該在伺服器上累積。
_KEEP = os.environ.get("READER_KEEP") in ("1", "true", "yes")


# ── 主機狀態取樣 ─────────────────────────────────────────────────────────────
# 2026-09-14：同一張圖、同一個行程，實測到 6.8 秒與 11.6 秒兩種結果，而
# 四個假設（我在跑別的測試搶 GPU、冷快取、Docker 網路、圖片本身）逐一被
# 排除，事後也無法再現。問題不在缺少「哪一段慢」——stage_secs 已經有了
# ——而在缺少**當下主機是什麼狀況**。
#
# 所以每次請求連同耗時一起記下 GPU 與 CPU 的現況。下次再出現 11 秒，
# 現場資料就在回應裡，不必事後追時間戳。
#
# 成本：nvidia-smi 一次約 20–40ms，只在請求開始與結束各取一次。
# 取不到就回 None——**這是量測，不可以讓它影響服務**。
def _gpu_sample():
    """回傳 (已用 MiB, 可用 MiB, 使用率 %)；取不到回 (None, None, None)。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.free,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3).stdout.strip().splitlines()
        used, free, util = (int(x) for x in out[0].split(","))
        return used, free, util
    except Exception:
        return None, None, None


def _host_sample():
    """GPU ＋ CPU 的當下狀況。Qwen 跑 CPU，CPU 被佔住一樣會拖慢這條管線。"""
    used, free, util = _gpu_sample()
    d = {"vram_used_mb": used, "vram_free_mb": free, "gpu_util_pct": util}
    try:
        import psutil
        d["cpu_pct"] = psutil.cpu_percent(interval=None)
        d["load_procs"] = len(psutil.pids())
    except Exception:
        pass
    return d


def _cleanup(cid, rels):
    """暫存檔不留。圖片尤其不可留——那會在伺服器上累積使用者的照片，
    而個人化資料不上雲是本專案明文的界線（見 CLAUDE.md「慣例」）。

    READER_KEEP=1 時保留中間的 JSON 供除錯，**但圖片一律刪**——
    要看的是辨識結果，不是原圖。"""
    for rel in rels:
        try:
            (WORK / rel).unlink()
        except OSError:
            pass
    if _KEEP:
        return
    for pre in (PRESET_BOXES, PRESET_VL, PRESET_NUTRI, PRESET_LF):
        try:
            (PPOCR / "out" / pre / (cid + ".json")).unlink()
        except OSError:
            pass
