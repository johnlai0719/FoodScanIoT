#!/usr/bin/env python3
# 用 Vision 的資料跑 bench_parse.py 的同一批解析器，與 PP-OCR 版並排。
#
# 只換兩個來源，解析邏輯完全不動——差異才能歸因於讀取器：
#   N.OUT  out/nutrition/  →  out/nutrition_gv/   （全圖＋裁切重讀的文字）
#   BOXES  v6_hires...     →  gvision             （幾何配對用的框）
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nutrition_pipeline as N   # noqa: E402
import bench_parse as BP         # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
N.OUT = os.path.join(HERE, 'out', 'nutrition_gv')
BP.BOXES = 'gvision'

if __name__ == '__main__':
    sys.argv = [sys.argv[0]] + sys.argv[1:]
    BP.main()
