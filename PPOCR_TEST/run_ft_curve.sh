#!/bin/sh
# 學習曲線：四個訓練規模，全部在同一組固定的評估集上測。
# ⚠ 汙染區實驗，輸出是斜率不是成績。
#
# 開始前先清殘留：被砍的任務會留下 python 子行程各佔 2–3 GB，
# 累積幾次就會把系統記憶體吃光，形成「被砍→留孤兒→更容易被砍」的螺旋。
set -e
cd "$(dirname "$0")"
PY=./.venv_torch/Scripts/python.exe
for p in $(ps -W 2>/dev/null | grep "venv_torch" | awk '{print $4}'); do
  echo "清掉殘留行程 $p"; taskkill //F //PID "$p" >/dev/null 2>&1 || true
done
for n in 20 40 60 80; do
  echo "################ 訓練 n=$n ################"
  $PY train_ft_overfit.py --size=$n --epochs=16 --val-n=20
done
