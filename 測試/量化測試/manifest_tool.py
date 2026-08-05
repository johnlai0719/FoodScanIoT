#!/usr/bin/env python3
# 測試集圖片的完整性清單（manifest）產生與驗證。
#
# 為何需要：原圖 98 張共 167 MB，不進版控（.gitignore 擋 *.jpg/*.png）。
# 但「用同一組案例跨期比較」的前提是那組案例真的沒變過——圖片不在 git 裡，
# 就沒有任何機制能證明今天跑的 c37 和上個月跑的 c37 是同一張。
# manifest.json 記下每張圖的 SHA256 與大小，本身是純文字、進版控，
# 於是圖片雖在版控外，其「身分」仍受版控保護。
#
# 圖片本體的傳遞方式（擇一，見 README）：GitHub Release 附件 zip、或校內雲端硬碟。
# 取得後放回 images/，跑 verify 確認與 manifest 相符即可重現歷次數字。
#
# 用法：
#   python manifest_tool.py generate      # 依現有 images/ 產生 manifest.json
#   python manifest_tool.py verify        # 驗證 images/ 與 manifest.json 相符
#
# verify 的離開碼：0 相符；1 有出入（缺檔／多檔／內容不同）。可供 CI 使用。
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES = os.environ.get('EVAL_IMAGE_ROOT') or os.path.join(HERE, 'images')
MANIFEST = os.path.join(HERE, 'manifest.json')

_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.heic')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def scan():
    """回傳 {相對路徑: {sha256, bytes}}。相對路徑一律用 / 分隔，跨平台一致。"""
    out = {}
    for root, _, files in os.walk(IMAGES):
        for fn in sorted(files):
            if not fn.lower().endswith(_EXTS):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, IMAGES).replace(os.sep, '/')
            out[rel] = {"sha256": sha256(full), "bytes": os.path.getsize(full)}
    return dict(sorted(out.items()))


def generate():
    files = scan()
    total = sum(v['bytes'] for v in files.values())
    manifest = {
        "_說明": "測試集原圖的完整性清單。圖片不進版控，此檔進版控——見 manifest_tool.py 檔頭。",
        "image_root": "images/",
        "n_files": len(files),
        "total_bytes": total,
        "files": files,
    }
    with open(MANIFEST, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"已產生 {MANIFEST}")
    print(f"  {len(files)} 個檔案，共 {total / 1024 / 1024:.1f} MB")


def verify():
    if not os.path.exists(MANIFEST):
        print(f"[FAIL] 找不到 {MANIFEST}，請先跑 generate")
        return 1
    expected = json.load(open(MANIFEST, encoding='utf-8'))['files']
    actual = scan()

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(k for k in set(expected) & set(actual)
                     if expected[k]['sha256'] != actual[k]['sha256'])

    for label, items in (("缺少", missing), ("多出", extra), ("內容不同", changed)):
        for k in items:
            print(f"[{label}] {k}")

    if missing or extra or changed:
        print(f"\n[FAIL] 與 manifest 不符："
              f"缺 {len(missing)}、多 {len(extra)}、異動 {len(changed)}")
        print("測試集已非原始狀態，跨期數字不可直接比較。")
        return 1

    print(f"[OK] {len(actual)} 個檔案全部與 manifest 相符，測試集完整。")
    return 0


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'verify'
    if cmd == 'generate':
        generate()
    elif cmd == 'verify':
        sys.exit(verify())
    else:
        print(__doc__ or "用法：manifest_tool.py [generate|verify]")
        sys.exit(2)
