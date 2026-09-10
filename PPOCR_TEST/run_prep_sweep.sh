#!/bin/sh
# 排隊：1920q85 的 vlcrop，接著三種裁切後前處理（在原圖上跑）。
# 每輪約 30 分鐘。⚠ 前處理作用在**送進 VLM 的那張圖**，不是整張原圖——
# 舊的「九種前處理全部無效」是對 PP-OCR 讀整張圖量的，作用點不同。
set -e
cd "$(dirname "$0")"
echo "######## 1920q85 ########"
EVAL_IMAGE_ROOT=images_1920q85 PPOCR_BOXES=v6_best_1920q85 \
  python run_vlcrop.py --backend=hunyuan --preset=vlcrop_hy_1920q85
for m in unsharp clahe glare; do
  echo "######## 前處理 $m（原圖）########"
  VL_PREP=$m PPOCR_BOXES=v6_best \
    python run_vlcrop.py --backend=hunyuan --preset=vlcrop_hy_prep_$m
done
