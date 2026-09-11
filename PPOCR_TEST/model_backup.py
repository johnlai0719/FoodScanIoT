#!/usr/bin/env python3
"""訓練好的模型：打包到雲端、驗證、取回。

**為什麼需要這一支**：`out/linecls_model` 有 7.3 GB，其中推論真正需要的只有
**391 MB**（`model.safetensors` ＋ `config.json` ＋ tokenizer），
其餘是六個 checkpoint 的 optimizer state。程式碼進版控，權重不進——
但「不進版控」不等於「可以不管」：那份權重是 25 分鐘訓練 ＋ 弱標註管線的成果，
硬碟壞掉就要重來，而且**重訓不保證得到同一份**（資料順序、CUDA 非決定性）。

這與測試集備份是同一個問題的兩半，出自 2026-08-16 教授回饋建議 5：
**hashes verify integrity but not availability**。

⚠ **今天就吃過一次虧**：跑 `train --epochs=2` 煙霧測試時，`cmd_train` 會把
   結果存進 `OUTDIR`，**直接覆蓋了頂層的 `model.safetensors`**。
   靠 `checkpoint-528` 才救回來（trainer_state 顯示它正是 best，ing_f1 0.7358）。
   若當時頂層是唯一一份，就永久遺失了。

用法：
    python model_backup.py                    # 現況：本地有什麼、雲端有什麼
    python model_backup.py pack               # 打包最佳 checkpoint → 雲端
    python model_backup.py verify             # 比對本地與清單的 SHA256
    python model_backup.py restore            # 從雲端取回（拒絕覆蓋，需 --force）
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'out', 'linecls_model')
# 與測試集備份同一個資料夾層級，理由相同：**只能驗證不能取得的清單沒有意義**
CLOUD = os.environ.get('FOODSCAN_MODEL_DIR') or \
    r'G:\我的雲端硬碟\學校資料\專題\食安IoT\模型備份'
NAME = 'linecls_bert_v4.0_2026-09-11'
# 推論需要的東西。optimizer.pt / scheduler.pt / rng_state.pth 是訓練狀態，不收。
KEEP = ('model.safetensors', 'config.json', 'tokenizer.json',
        'tokenizer_config.json', 'vocab.txt', 'special_tokens_map.json')


def sha256(p, chunk=1 << 20):
    h = hashlib.sha256()
    size = os.path.getsize(p)
    show = size > (50 << 20) and sys.stdout.isatty()
    done = 0
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(chunk), b''):
            h.update(b)
            done += len(b)
            if show:
                print('\r   雜湊 %5.1f%%' % (100 * done / size), end='', flush=True)
    if show:
        print('\r' + ' ' * 20 + '\r', end='')
    return h.hexdigest()


def human(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or u == 'GB':
            return '%.1f %s' % (n, u)
        n /= 1024


def best_checkpoint():
    """`load_best_model_at_end` 之下，最佳的不一定是最後一個——去問 trainer_state。"""
    best, metric = None, None
    if not os.path.isdir(SRC):
        return None, None
    # ⚠ **要依步數排序，不是字串排序。** 'checkpoint-88' 字串排在 'checkpoint-528'
    #    之後（'8' > '5'），而每個 checkpoint 的 trainer_state 記的是**當下為止**
    #    的最佳——取字串最後一個會拿到第 1 epoch 的狀態（實際踩過：回報
    #    checkpoint-88 / ing_f1 0.6759，正解是 checkpoint-528 / 0.7358）。
    def step(d):
        try:
            return int(d.rsplit('-', 1)[1])
        except (IndexError, ValueError):
            return -1
    ds = sorted((d for d in os.listdir(SRC) if d.startswith('checkpoint-')),
                key=step)
    for d in ds:
        p = os.path.join(SRC, d, 'trainer_state.json')
        if not os.path.exists(p):
            continue
        try:
            st = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        b = st.get('best_model_checkpoint')
        if b:
            best, metric = os.path.basename(b), st.get('best_metric')
    return best, metric


def cmd_status():
    print('本地　%s' % SRC)
    if not os.path.isdir(SRC):
        print('   **不存在**')
    else:
        tot = sum(os.path.getsize(os.path.join(r, f))
                  for r, _, fs in os.walk(SRC) for f in fs)
        top = [f for f in KEEP if os.path.exists(os.path.join(SRC, f))]
        print('   整個目錄        %s' % human(tot))
        print('   頂層推論檔      %s' % ('、'.join(top) or '**缺**'))
        b, m = best_checkpoint()
        if b:
            print('   trainer 標的最佳 %s（ing_f1 %.4f）' % (b, m or 0))
    print('\n雲端　%s' % CLOUD)
    if not os.path.isdir(CLOUD):
        print('   **資料夾不存在**')
    else:
        fs = [f for f in sorted(os.listdir(CLOUD)) if f.endswith('.zip')]
        for f in fs:
            print('   %s   %s' % (f, human(os.path.getsize(os.path.join(CLOUD, f)))))
        if not fs:
            print('   （空）')
    print('\n下一步')
    print('   python model_backup.py pack' if os.path.isdir(SRC)
          else '   先訓練：.venv_torch/Scripts/python.exe train_linecls.py train')


def cmd_pack(a):
    if not os.path.isdir(SRC):
        sys.exit('找不到 %s' % SRC)
    b, m = best_checkpoint()
    # 頂層應該就是 best（load_best_model_at_end），但頂層會被後續的 train 覆蓋，
    # 所以**以 trainer_state 指名的 checkpoint 為準**，頂層只當備援。
    srcdir = os.path.join(SRC, b) if b and os.path.isdir(os.path.join(SRC, b)) else SRC
    print('=== 打包 %s ===' % NAME)
    print('   來源 %s%s' % (srcdir, '（trainer 標的最佳，ing_f1 %.4f）' % m if m else ''))
    files = []
    for f in KEEP:
        for d in (srcdir, SRC):          # checkpoint 裡沒有 tokenizer，回頂層找
            p = os.path.join(d, f)
            if os.path.exists(p):
                files.append((f, p))
                break
    if not any(f == 'model.safetensors' for f, _ in files):
        sys.exit('缺 model.safetensors，無法打包')
    os.makedirs(CLOUD, exist_ok=True)
    zpath = os.path.join(CLOUD, NAME + '.zip')
    if os.path.exists(zpath) and not a.force:
        sys.exit('雲端已有 %s。要覆蓋請加 --force。' % os.path.basename(zpath))
    man = {'_說明': '逐行分類器的推論權重。程式在 PPOCR_TEST/train_linecls.py。',
           'name': NAME, 'best_checkpoint': b, 'best_ing_f1': m,
           'base_model': 'ckiplab/bert-base-chinese-ws',
           'labels': 11, 'files': {}}
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, p in files:
            print('   收 %-28s %s' % (name, human(os.path.getsize(p))))
            man['files'][name] = {'sha256': sha256(p), 'bytes': os.path.getsize(p)}
            z.write(p, name)
        z.writestr('_模型清單.json', json.dumps(man, ensure_ascii=False, indent=1))
    h = sha256(zpath)
    open(zpath + '.sha256', 'w', encoding='utf-8').write(
        '%s  %s\n' % (h, os.path.basename(zpath)))
    print('\n寫出 %s（%s）' % (zpath, human(os.path.getsize(zpath))))
    print('SHA256 %s' % h)
    print('\n**把這一行記進 PPOCR_TEST/README.md 的「模型備份位置」**：')
    print('| `%s` | %s | `%s` |' % (CLOUD, os.path.basename(zpath), h))


def cmd_verify():
    zpath = os.path.join(CLOUD, NAME + '.zip')
    if not os.path.exists(zpath):
        sys.exit('雲端找不到 %s' % zpath)
    rec = os.path.join(zpath + '.sha256')
    want = open(rec, encoding='utf-8').read().split()[0] if os.path.exists(rec) else None
    got = sha256(zpath)
    print('zip SHA256 %s' % got)
    if want:
        print('清單記錄   %s   %s' % (want, '相符 ✔' if want == got else '**不符**'))
    with zipfile.ZipFile(zpath) as z:
        man = json.loads(z.read('_模型清單.json').decode('utf-8'))
    print('\n逐檔比對本地：')
    ok = True
    for name, info in man['files'].items():
        p = os.path.join(SRC, name)
        if not os.path.exists(p):
            print('   %-28s 本地缺' % name)
            ok = False
            continue
        h = sha256(p)
        same = h == info['sha256']
        ok = ok and same
        print('   %-28s %s' % (name, '相符 ✔' if same else '**不符**'))
    return 0 if ok else 1


def cmd_restore(a):
    zpath = os.path.join(CLOUD, NAME + '.zip')
    if not os.path.exists(zpath):
        sys.exit('雲端找不到 %s' % zpath)
    exist = [f for f in KEEP if os.path.exists(os.path.join(SRC, f))]
    if exist and not a.force:
        sys.exit('本地已有 %s。\n覆蓋會蓋掉現行權重——要繼續請加 --force。'
                 % '、'.join(exist))
    rec = zpath + '.sha256'
    if os.path.exists(rec):
        want = open(rec, encoding='utf-8').read().split()[0]
        got = sha256(zpath)
        if want != got:
            sys.exit('SHA256 不符，**沒有解開任何檔案**。\n   期望 %s\n   實得 %s'
                     % (want, got))
        print('SHA256 相符 ✔')
    os.makedirs(SRC, exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            if n != '_模型清單.json':
                z.extract(n, SRC)
                print('   還原 %s' % n)
    print('\n還原完成 → %s' % SRC)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', nargs='?', default='status',
                    choices=('status', 'pack', 'verify', 'restore'))
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'status':
        cmd_status()
        return 0
    if a.cmd == 'pack':
        return cmd_pack(a)
    if a.cmd == 'verify':
        return cmd_verify()
    return cmd_restore(a)


if __name__ == '__main__':
    sys.exit(main())
