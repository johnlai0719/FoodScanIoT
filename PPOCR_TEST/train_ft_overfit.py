#!/usr/bin/env python3
"""HunyuanOCR-1.5 LoRA **過擬合測試**（不是效能實驗）。

⚠⚠ **訓練資料來自評估集。這支產出的權重是汙染的，只能回答一個是非題：
「訓練管線跑不跑得起來」。** 規矩見 `build_ft_pairs.py` 檔頭。

**任務不是「轉錄得更好」，是「只抄成分欄」。** 實測未微調的模型餵成分區裁切圖
會把整塊全部轉錄出來（品名、容量、有效日期、過敏原警語一起吐），而正解
`ingredients_raw` 只有成分那一段。所以這其實是**欄位特化**，正是「讓模型
偏向食品包裝」要的東西——只是資料量遠遠不夠，所以先驗證管線。

## 提示詞格式（踩了一小時的坑，記在這裡）

HF 版的 `tokenizer_config.json` **沒有 chat template**，`apply_chat_template`
會直接報錯。官方 README 卻叫你用它。真正的模板在 **GGUF 的
`tokenizer.chat_template` 欄位裡**，可以直接從 .gguf 檔頭讀出來。

而它的慣例跟大多數模型**相反**——轉場記號放在內容**後面**：

    <｜hy_begin▁of▁sentence｜>{圖}{提示詞}<｜hy_User｜>{答案}<｜hy_Assistant｜>

所以 `<｜hy_User｜>` 是「使用者說完了」，`<｜hy_Assistant｜>` 是「助手說完了」
——這也解釋了為什麼 `special_tokens_map.json` 把 `<｜hy_Assistant｜>` 當成 eos。
猜錯格式的症狀是輸出一整片 `$ $ $ $` 或「或或或或」，看起來像模型壞了，
其實只是提示詞不對。
"""
import argparse, io, json, os, sys, torch
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
from PIL import Image
from transformers import AutoProcessor, AutoModelForImageTextToText
from peft import LoraConfig, get_peft_model

MODEL = os.environ.get('HY_HF') or os.path.join(
    os.path.expanduser('~'), '.cache', 'huggingface', 'hub',
    'models--tencent--HunyuanOCR', 'snapshots',
    '47644ecc4fc854efa4f505155158831f36773ee4')
PAIRS = os.path.join(HERE, 'out', '_contaminated_ft_pairs')
CKPT = os.path.join(HERE, 'out', '_contaminated_lora_ckpt')
IMG_TOK = ('<｜hy_place▁holder▁no▁100｜><｜hy_place▁holder▁no▁102｜>'
           '<｜hy_place▁holder▁no▁101｜>')
BOS, USER, ASSIST = '<｜hy_begin▁of▁sentence｜>', '<｜hy_User｜>', '<｜hy_Assistant｜>'
# 實驗 A（2026-09-08）：把「多段全抄」寫進提示詞。
# 起因：微調後 `c73_老媽拌麵` 正解四段（麵條／麻醬／油膏／蔥油），模型只抄了
# 第三段就跑進營養表，命中 40/44 → 7/44。查訓練集才發現**多段案例 0 案**
# （81 筆全是單段），三個多段案例全落在評估側。它學到「成分欄＝一段」是合理的。
# 這條規則用文字講得清楚，所以先試最便宜的做法。
# ⚠ 訓練與評估必須用**同一個** PROMPT。`eval_ft_curve.py` 是 import 過來的，
#    但 `run_vlcrop.py` 的 hunyuan_hf 後端有自己的字串，要跑全管線時得一起改。
PROMPT = ('請完整轉錄圖中的成分欄文字。'
          '若標示分成多段（例如麵條、調味包、油包各有一段），**每一段都要抄，不可只抄其中一段**。'
          '保留原有的頓號與括號。')


def build(pr, row, maxpx):
    img = Image.open(os.path.join(PAIRS, row['image'])).convert('RGB')
    img.thumbnail((maxpx, maxpx))
    head = BOS + IMG_TOK + PROMPT + USER
    inp = pr(text=[head], images=[img], return_tensors='pt')
    ans = pr.tokenizer(row['text'] + ASSIST, add_special_tokens=False,
                       return_tensors='pt')['input_ids']
    ids = torch.cat([inp['input_ids'], ans], dim=1)
    labels = ids.clone()
    labels[:, :inp['input_ids'].shape[1]] = -100        # 只在答案上算 loss
    out = {k: v for k, v in inp.items() if k not in ('input_ids', 'attention_mask',
                                                     'mm_token_type_ids')}
    out['input_ids'] = ids
    out['attention_mask'] = torch.ones_like(ids)
    if 'mm_token_type_ids' in inp:
        out['mm_token_type_ids'] = torch.cat(
            [inp['mm_token_type_ids'], torch.zeros_like(ans)], dim=1)
    out['labels'] = labels
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--size', type=int, default=None,
                    help='用 split.json 的 train_sets[size]（學習曲線用）')
    ap.add_argument('--ckpt', default=None, help='存到哪；預設依 size 命名')
    ap.add_argument('--n', type=int, default=0,
                    help='除錯用：直接取前 N 筆。⚠ 會嚴重偏斜，見下方註解')
    ap.add_argument('--epochs', type=int, default=12)
    ap.add_argument('--lr', type=float, default=1e-4)
    ap.add_argument('--rank', type=int, default=16)
    ap.add_argument('--maxpx', type=int, default=896)
    ap.add_argument('--val-n', type=int, default=25,
                    help='驗證用幾案（取自固定評估集）。25 案約半分鐘一輪')
    a = ap.parse_args()

    rows = [json.loads(l) for l in io.open(os.path.join(PAIRS, 'pairs.jsonl'),
                                           encoding='utf-8')]
    if a.n:
        # ⚠ 取前 N 筆會嚴重偏斜：case_id 的排序與加入批次相關，實測前 20 筆
        # **餅乾與醬料兩整類 0 案**。只在除錯時用。
        rows = rows[:a.n]
    else:
        sp = json.load(io.open(os.path.join(PAIRS, 'split.json'), encoding='utf-8'))
        ts = sp.get('train_sets') or {}
        keep = set(ts[str(a.size)] if a.size and str(a.size) in ts else sp['train'])
        rows = [r for r in rows if r['case_id'] in keep]
    print('⚠ 汙染實驗：用評估集的 %d 案做過擬合測試，權重不得用於任何回報數字\n'
          % len(rows))
    pr = AutoProcessor.from_pretrained(MODEL)
    m = AutoModelForImageTextToText.from_pretrained(
        MODEL, dtype=torch.bfloat16, device_map='cuda')
    m.config.use_cache = False
    m.gradient_checkpointing_enable()
    m.enable_input_require_grads()
    cfg = LoraConfig(r=a.rank, lora_alpha=a.rank * 2, lora_dropout=0.0,
                     bias='none', task_type='CAUSAL_LM',
                     target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj',
                                     'gate_proj', 'up_proj', 'down_proj'])
    m = get_peft_model(m, cfg)
    m.print_trainable_parameters()

    # ── 驗證集：**訓練 loss 沒有資訊量** ────────────────────────────────
    # 20 個樣本配 13M 可訓練參數，訓練 loss 掉到 0.001 是必然的，只證明
    # 「記得住」。真正該看的是沒訓練過的案例上的 loss：
    #     訓練↓ 驗證↓   還在學，加 epoch 或加資料有用
    #     訓練↓ 驗證平   容量夠了，這批資料的訊息榨完了
    #     訓練↓ 驗證↑   過擬合，該早停
    # 只要前向、不用生成，53 案每個 epoch 約半分鐘，很便宜。
    # ⚠ 2026-09-08 之前四個曲線點都寫死跑 12 epoch，**沒有任何依據**，
    #    量到的泛化是被過擬合壓低過的。加上這條之後才有得比。
    valrows = []
    if not a.n:
        sp2 = json.load(io.open(os.path.join(PAIRS, 'split.json'), encoding='utf-8'))
        heldset = set(sp2['held'])
        allrows = [json.loads(l) for l in io.open(os.path.join(PAIRS, 'pairs.jsonl'),
                                                  encoding='utf-8')]
        valrows = [r for r in allrows if r['case_id'] in heldset][:a.val_n]

    # ⚠ 用到才建，不要一次全建。2026-09-08 一次建好 106 份 pixel_values
    # （每份約 8 MB）常駐近 1 GB，加上被砍的任務留下的孤兒行程各佔 2–3 GB，
    # 系統記憶體一路掉到剩 0.5 GB，形成「被砍 → 留孤兒 → 更容易被砍」的螺旋。
    # 代價是每個 epoch 重做一次影像前處理，約一分鐘，換掉那 1 GB。
    def batch_of(r):
        return build(pr, r, a.maxpx)

    print('訓練 %d 案｜驗證 %d 案（取自固定評估集，不參與訓練）'
          % (len(rows), len(valrows)))
    opt = torch.optim.AdamW([p for p in m.parameters() if p.requires_grad], lr=a.lr)

    def val_loss():
        if not valrows:
            return float('nan')
        m.eval()
        tot = 0.0
        with torch.no_grad():
            for r in valrows:
                b = {k: (v.to(m.device) if hasattr(v, 'to') else v)
                     for k, v in batch_of(r).items()}
                tot += m(**b).loss.item()
                del b
        m.train()
        return tot / len(valrows)

    m.train()
    best = (float('inf'), 0)
    dst = a.ckpt or ('%s_n%d' % (CKPT, len(rows)) if a.size else CKPT)
    for ep in range(1, a.epochs + 1):
        tot = 0.0
        for r in rows:
            b = {k: (v2.to(m.device) if hasattr(v2, 'to') else v2)
                 for k, v2 in batch_of(r).items()}
            loss = m(**b).loss
            loss.backward()
            opt.step(); opt.zero_grad()
            tot += loss.item()
            del b, loss
        v = val_loss()
        mark = ''
        if v < best[0]:
            best = (v, ep)
            mark = '  ← 目前最佳，存檔'
            os.makedirs(dst, exist_ok=True)
            m.save_pretrained(dst)
        print('epoch %2d  訓練 %.4f  驗證 %.4f   VRAM %.1f GB%s'
              % (ep, tot / len(rows), v,
                 torch.cuda.max_memory_allocated() / 1e9, mark))
    # ⚠ 2026-09-08：四個曲線點原本全部寫到同一個目錄，前三個被覆蓋。
    #    依實際訓練案數命名，避免再犯。
    # 存的是**驗證 loss 最低**那個 epoch 的權重，不是最後一個 epoch。
    json.dump({'n_train': len(rows), 'best_epoch': best[1],
               'best_val_loss': best[0], 'epochs_run': a.epochs},
              io.open(os.path.join(dst, 'curve_meta.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('最佳 epoch %d（驗證 loss %.4f）→ %s（**汙染**，不得產生任何回報數字）'
          % (best[1], best[0], dst))


if __name__ == '__main__':
    main()
