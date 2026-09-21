#!/usr/bin/env python3
# 量化測試集異地備份打包器。
#
# 為何需要：manifest.json 進版控，能證明照片的「同一性」，不能保證照片「還存在」。
# 原圖 143 MB 不進版控，只存在一台機器上——硬碟壞掉會失去的不是照片，
# 是 v2.0 以來所有數字的可重現性。這支腳本補的是存在性。
# 出自 2026-08-16 教授回饋建議 5：hashes verify integrity but not availability。
#
# 用法：
#   python make_backup.py "<輸出路徑>/量化測試集_<版號>_<日期>.zip"
#
# 產物：一個 zip ＋ 同名 .sha256（zip 自身的雜湊）。
# zip 內含 _備份清單.json，記下每個檔案打包當時的 SHA256，可逐檔驗證還原結果。
#
# 什麼時候要重跑：新增或修訂案例（set_version 變動）、ground_truth/ 有人工修訂、
# 補拍配對照片之後。舊的 zip 不要刪——它是舊版號數字的可重現性依據。
#
# 備份位置記於 README.md 的「備份與取得位置」。
import hashlib, json, os, sys, zipfile
from datetime import datetime

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「塩」「菓」…）。
# 沒有這兩行的後果不是印出亂碼，而是**整批任務在中途拋 UnicodeEncodeError 死掉**
# ——2026-09-07 run_eval 炸在第 10 案的「塩」、intake 炸在第 11 案的「菓」，
# 後者還留下半套用狀態（照片已搬、cases.json 沒存）。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

SRC = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) < 2:
    print(__doc__ or "用法：make_backup.py <輸出的 zip 路徑>")
    sys.exit(2)
OUT = os.path.abspath(sys.argv[1])

# 收錄：不可重生的資產 ＋ 壓縮實驗的實際輸入 ＋ 定義檔
INCLUDE_DIRS = [
    "images",           # 原圖 67 張 143MB，manifest 保證身分，本體只存這一份
    "images_1280q80",   # 壓縮實驗的實際輸入（JPEG 編碼隨 Pillow 版本而異，不視為可重生）
    "_上傳區",           # 手機匯入的 HEIC 原始檔與 intake 狀態
    "ground_truth",     # 人工正解，唯一不可由程式重生的資產
]
INCLUDE_FILES = ["cases.json", "labels.json", "manifest.json", "image_metrics.json", "README.md"]
# 排除：__pycache__、_zoom（標註用裁切，可重生）、predictions/results（在版控內）、*.py、*_worksheet.html
EXCLUDE_NAMES = {"__pycache__", ".DS_Store", "Thumbs.db"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect():
    items = []
    for d in INCLUDE_DIRS:
        base = os.path.join(SRC, d)
        if not os.path.isdir(base):
            print(f"[WARN] 找不到 {d}/，略過")
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x not in EXCLUDE_NAMES]
            for fn in sorted(files):
                if fn in EXCLUDE_NAMES:
                    continue
                full = os.path.join(root, fn)
                items.append((full, os.path.relpath(full, SRC).replace(os.sep, "/")))
    for fn in INCLUDE_FILES:
        full = os.path.join(SRC, fn)
        if os.path.isfile(full):
            items.append((full, fn))
        else:
            print(f"[WARN] 找不到 {fn}，略過")
    return sorted(items, key=lambda x: x[1])


def _set_versions():
    """從 cases.json 讀出各 set_version 的案例數，避免版號寫死在腳本裡。"""
    p = os.path.join(SRC, "cases.json")
    try:
        data = json.load(open(p, encoding="utf-8"))
        cases = data["cases"] if isinstance(data, dict) and "cases" in data else data
        if isinstance(cases, dict):
            cases = list(cases.values())
        n = {}
        for c in cases:
            v = c.get("set_version", "?")
            n[v] = n.get(v, 0) + 1
        return "＋".join(f"{v}（{c} 案）" for v, c in sorted(n.items())) + f"，共 {len(cases)} 案"
    except Exception as e:
        return f"未能判定（{e}）"


items = collect()
listing = {}
total = 0
for full, rel in items:
    b = os.path.getsize(full)
    listing[rel] = {"sha256": sha256(full), "bytes": b}
    total += b

meta = {
    "_說明": "量化測試集異地備份的檔案清單。每筆為備份當時的 SHA256，可據此逐檔驗證還原結果。",
    "來源": SRC,
    "打包時間": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "測試集版本": _set_versions(),
    "收錄範圍": {"目錄": INCLUDE_DIRS, "單檔": INCLUDE_FILES},
    "未收錄": ["__pycache__/", "_zoom/（標註裁切，可重生）", "predictions/", "results/", "*.py", "*_worksheet.html"],
    "n_files": len(listing),
    "total_bytes": total,
    "files": listing,
}

print(f"收錄 {len(items)} 檔，原始共 {total/1024/1024:.1f} MB → 打包中…")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
tmp = OUT + ".part"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
    z.writestr("_備份清單.json", json.dumps(meta, ensure_ascii=False, indent=2))
    for i, (full, rel) in enumerate(items, 1):
        z.write(full, rel)
        if i % 200 == 0:
            print(f"  {i}/{len(items)}")
os.replace(tmp, OUT)

zsize = os.path.getsize(OUT)
zhash = sha256(OUT)
print(f"\n[OK] {OUT}")
print(f"  zip 大小 {zsize/1024/1024:.1f} MB（原始 {total/1024/1024:.1f} MB）")
print(f"  zip SHA256 {zhash}")
with open(OUT + ".sha256", "w", encoding="utf-8") as f:
    f.write(f"{zhash}  {os.path.basename(OUT)}\n")
print(json.dumps({"zip": OUT, "sha256": zhash, "bytes": zsize, "n_files": len(items),
                  "raw_bytes": total}, ensure_ascii=False))
