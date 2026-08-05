#!/usr/bin/env python3
# 在伺服器端重現 App 的上傳前壓縮，供辨識率的前後對照。
#
# 為何要有這支：影像壓縮的驗證其實是兩個問題，混在一起會卡住。
#   Q1 「縮到 1280、品質 0.8 會不會讓標示上的小字讀不出來」——這是參數本身的
#      影響，與 App 的實作無關，用同一批原圖離線處理後重跑辨識即可回答，
#      不必等手機端的程式併進主線。
#   Q2 「組員的實作有沒有真的照這組參數壓」——這要看 App 實際送出的檔案，
#      本工具不回答，也不應該用本工具的產物去代答。
# 分開之後，Q1 現在就能做，而 Q2 之後只要比對 App 送出的檔案尺寸與此處的
# 產物是否相符即可，不必再跑一次辨識。
#
# **這不是 App 壓縮的等價替代**：手機端用的是 React Native 的影像套件，
# 與 Pillow 的 JPEG 編碼器不同（量化表、色度抽樣皆有差異），同樣參數下
# 檔案大小與細節損失不會完全一致。故本工具的結論限於「這個解析度與品質
# 等級是否足以辨識」，不能用來宣稱 App 的輸出等同於此。
#
# 用法：
#   python compress_images.py                      # 產生 images_1280q80/
#   python compress_images.py --width=1080 --quality=0.7 --out=images_1080q70
#
# 產生後這樣量：
#   EVAL_IMAGE_ROOT=images_1280q80 python run_eval.py --tag=compressed_1280q80
#   python score_eval.py
import json
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'images')
_EXTS = ('.jpg', '.jpeg', '.png', '.webp')


def compress(src_dir, out_dir, max_width, quality):
    rows = []
    for root, _, files in os.walk(src_dir):
        for fn in sorted(files):
            if not fn.lower().endswith(_EXTS) or fn.startswith('._'):
                continue
            sp = os.path.join(root, fn)
            rel = os.path.relpath(sp, src_dir)
            # 一律輸出 .jpg：App 端壓縮後也是 JPEG，維持一致
            dp = os.path.join(out_dir, os.path.splitext(rel)[0] + '.jpg')
            os.makedirs(os.path.dirname(dp), exist_ok=True)

            with Image.open(sp) as im:
                im = im.convert('RGB')
                w0, h0 = im.size
                if w0 > max_width:
                    h = round(h0 * max_width / w0)
                    im = im.resize((max_width, h), Image.LANCZOS)
                im.save(dp, 'JPEG', quality=int(quality * 100), optimize=True)

            b0, b1 = os.path.getsize(sp), os.path.getsize(dp)
            rows.append({
                'file': rel.replace(os.sep, '/'),
                'w_before': w0, 'h_before': h0,
                'w_after': im.size[0], 'h_after': im.size[1],
                'bytes_before': b0, 'bytes_after': b1,
                'ratio': round(b1 / b0, 4) if b0 else None,
            })
    return rows


def main():
    max_width, quality, out_name = 1280, 0.8, None
    for a in sys.argv[1:]:
        if a.startswith('--width='):
            max_width = int(a.split('=', 1)[1])
        elif a.startswith('--quality='):
            quality = float(a.split('=', 1)[1])
        elif a.startswith('--out='):
            out_name = a.split('=', 1)[1]
        else:
            sys.exit(f"看不懂的參數：{a}")
    out_name = out_name or f"images_{max_width}q{int(quality*100)}"
    out_dir = os.path.join(HERE, out_name)

    if not os.path.isdir(SRC):
        sys.exit(f"找不到 {SRC}")
    rows = compress(SRC, out_dir, max_width, quality)
    if not rows:
        sys.exit("沒有處理到任何圖片")

    tb = sum(r['bytes_before'] for r in rows)
    ta = sum(r['bytes_after'] for r in rows)
    resized = sum(1 for r in rows if r['w_after'] != r['w_before'])
    report = {
        'source': 'images/', 'output': out_name,
        'max_width': max_width, 'quality': quality,
        'encoder': 'Pillow JPEG（非 App 端所用之編碼器，見檔頭說明）',
        'n_files': len(rows),
        'bytes_before': tb, 'bytes_after': ta,
        'ratio': round(ta / tb, 4),
        'n_resized': resized,
        'files': rows,
    }
    with open(os.path.join(out_dir, '_compression_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"輸出 {out_name}/  （{len(rows)} 張）")
    print(f"  最大寬度 {max_width}、品質 {quality}")
    print(f"  {tb/1024/1024:.1f} MB → {ta/1024/1024:.1f} MB   剩 {ta/tb*100:.1f}%")
    print(f"  實際被縮小尺寸的：{resized} 張（其餘原本就窄於 {max_width}）")
    print(f"  明細：{out_name}/_compression_report.json")
    print()
    print("接著量測：")
    print(f"  EVAL_IMAGE_ROOT={out_name} python run_eval.py --tag={out_name}")
    print("  python score_eval.py")


if __name__ == '__main__':
    main()
