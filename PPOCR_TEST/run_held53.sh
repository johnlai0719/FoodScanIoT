#!/bin/sh
# 微調 vs 原模型，在**沒訓練過的 53 案**上，用正式計分器比。
#
# 兩件事分開講：
#   量測是乾淨的  那 53 案訓練時一張都沒看過，可以用專案的正式指標
#   模型是髒的    它的權重看過另外 81 個評估案例，**不能上線**
#                 一旦採用，未來所有 177 案的數字都被汙染
#
# ⚠ n=53，判別力很差。57 案時代的雜訊底線是 ±8 點，這裡只會更寬——
#    小於 8 點的差異一律當「量不出來」。
# ⚠ case_id 走檔案不走命令列：含中文與 `®`，經 shell 與 argv 會被 cp950 咬掉。
set -e
cd "$(dirname "$0")"
PY=./.venv_torch/Scripts/python.exe
LORA=${LORA:-out/_contaminated_lora_ckpt_n81}
IDF=out/_contaminated_ft_pairs/held53.txt
$PY -c "
import io,json
sp=json.load(io.open('out/_contaminated_ft_pairs/split.json',encoding='utf-8'))
io.open('$IDF','w',encoding='utf-8').write('\n'.join(sp['held']))
"
echo "評估集 $(wc -l < $IDF) 案（+1）"

echo '######## 原模型 ########'
PPOCR_BOXES=v6_best $PY run_vlcrop.py --backend=hunyuan_hf \
  --preset=vlcrop_hf_base53 --cases-file=$IDF

echo '######## 微調後 ########'
VL_LORA=$LORA PPOCR_BOXES=v6_best $PY run_vlcrop.py --backend=hunyuan_hf \
  --preset=vlcrop_hf_ft53 --cases-file=$IDF

echo '######## 正式計分器（添加物層）########'
python boot_compare.py vlcrop_hf_base53:boxsep vlcrop_hf_ft53:boxsep --n=5000
