#!/usr/bin/env python3
"""進件流程的啟動器：把工作區指到雲端，並在每一步之前檢查前提。

**為什麼需要這一支**：`intake.py` 的雲端模式靠兩個環境變數
（`EVAL_INBOX`、`EVAL_WORKSHEET_DIR`）。忘了設不會報錯——它會安靜地
跑本地那份，產出看起來正常但放錯地方。本專案已經被同型的「環境變數沒設
就靜默走預設」咬過兩次（`EVAL_IMAGE_ROOT` 讓 PP-OCR 讀 0 行仍回報成功、
`EMIT_BOXES` 讓交付檔的成分來源一直是 PP-OCR）。

所以這支做三件 `intake.py` 不做的事：
  1. 設好環境變數，並**印出實際生效的路徑**讓人肉眼確認
  2. 每一步之前檢查前提（資料夾在不在、上一步的產物在不在）
  3. `apply` 前要求明確確認——那一步會寫入 `ground_truth/` 與 `cases.json`

用法：
    python intake_cloud.py              # 看現在走到哪、下一步是什麼
    python intake_cloud.py convert
    python intake_cloud.py group
    python intake_cloud.py group groups.json
    python intake_cloud.py generate
    python intake_cloud.py apply intake.json
    python intake_cloud.py --local ...  # 走本地，不碰雲端
"""
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
CLOUD = os.environ.get('FOODSCAN_CLOUD') or r'G:\我的雲端硬碟\FoodScan'
IMG_EXT = ('.jpg', '.jpeg', '.png', '.heic', '.webp', '.bmp')


def env_for(local):
    e = dict(os.environ)
    if not local:
        e['EVAL_INBOX'] = os.path.join(CLOUD, '_上傳區')
        e['EVAL_WORKSHEET_DIR'] = CLOUD
    else:
        e.pop('EVAL_INBOX', None)
        e.pop('EVAL_WORKSHEET_DIR', None)
    return e


def paths(e):
    inbox = e.get('EVAL_INBOX') or os.path.join(HERE, '_上傳區')
    work = e.get('EVAL_WORKSHEET_DIR') or HERE
    return inbox, work


def loose_photos(inbox):
    """根目錄的散圖——尚未分組成案例資料夾者。"""
    if not os.path.isdir(inbox):
        return []
    return [f for f in sorted(os.listdir(inbox))
            if os.path.isfile(os.path.join(inbox, f))
            and f.lower().endswith(IMG_EXT)]


def case_dirs(inbox):
    """已分組的案例資料夾 <類別>/<case_id>/。"""
    out = []
    if not os.path.isdir(inbox):
        return out
    for cat in sorted(os.listdir(inbox)):
        d = os.path.join(inbox, cat)
        if not os.path.isdir(d) or cat.startswith('_'):
            continue
        for cid in sorted(os.listdir(d)):
            if os.path.isdir(os.path.join(d, cid)):
                out.append((cat, cid))
    return out


def status(e):
    inbox, work = paths(e)
    print('生效路徑')
    print('   收件匣   %s   %s' % (inbox, '' if os.path.isdir(inbox) else '**不存在**'))
    print('   工作單   %s   %s' % (work, '' if os.path.isdir(work) else '**不存在**'))
    print('   正解／影像寫回本地：%s' % HERE)
    loose = loose_photos(inbox)
    cases = case_dirs(inbox)
    gj = os.path.join(inbox, 'groups.json')
    ij = os.path.join(inbox, 'intake.json')
    ws = os.path.join(work, '_intake_worksheet.html')
    th = os.path.join(work, '_thumbs')
    print('\n目前狀態')
    print('   根目錄散圖（未分組）   %d 張' % len(loose))
    print('   已分組的案例資料夾     %d 個' % len(cases))
    for f, lab in ((gj, 'groups.json'), (ij, 'intake.json'),
                   (ws, '_intake_worksheet.html'), (th, '_thumbs/')):
        print('   %-22s %s' % (lab, '有' if os.path.exists(f) else '—'))
    print('\n建議的下一步')
    if not os.path.isdir(inbox):
        print('   收件匣不存在。先把照片放進 %s' % inbox)
    elif not loose and not cases:
        print('   收件匣是空的，沒有待處理的照片。')
        if os.path.exists(ij) or os.path.exists(gj):
            print('   （groups.json／intake.json 是上一輪的殘留，**不要再 apply**）')
    elif any(f.lower().endswith(('.heic', '.png', '.webp', '.bmp')) for f in loose):
        print('   python intake_cloud.py convert        # 有非 JPG 待轉檔')
    elif loose:
        print('   python intake_cloud.py group          # %d 張散圖待分組' % len(loose))
    elif cases and not os.path.exists(ws):
        print('   python intake_cloud.py generate       # %d 案待填正解' % len(cases))
    elif os.path.exists(ws) and not os.path.exists(ij):
        print('   在瀏覽器開啟工作單填正解，下載 intake.json 放回收件匣：')
        print('   %s' % ws)
    elif os.path.exists(ij):
        print('   python intake_cloud.py apply intake.json   # ⚠ 會寫入 ground_truth/')
    else:
        print('   收件匣是空的，沒有待處理的照片。')


def main():
    args = sys.argv[1:]
    local = '--local' in args
    args = [a for a in args if a != '--local']
    e = env_for(local)
    inbox, work = paths(e)
    print('=== 進件流程（%s）===' % ('本地' if local else '雲端'))
    if not args:
        status(e)
        return
    cmd = args[0]
    # ── 前提檢查。每一步都問「上一步的產物在不在」──────────────────────
    if cmd in ('convert', 'group', 'generate') and not os.path.isdir(inbox):
        sys.exit('收件匣不存在：%s\n（雲端未同步，或路徑設錯）' % inbox)
    if cmd == 'generate' and not case_dirs(inbox):
        sys.exit('收件匣裡沒有案例資料夾——請先跑 group 把散圖分組。')
    if cmd == 'group' and len(args) == 2:
        if not os.path.exists(os.path.join(inbox, args[1])) and not os.path.exists(args[1]):
            sys.exit('找不到 %s。工作單下載後要放回收件匣或本目錄。' % args[1])
    if cmd == 'apply':
        if len(args) < 2:
            sys.exit('用法：intake_cloud.py apply <intake.json>')
        # ⚠ 上一輪 apply 完成後，intake.json 會留在收件匣裡，但案例資料夾
        #    已被搬進 images/。沒有這道防護時，狀態頁會建議「apply intake.json」
        #    ——那是拿上一輪的舊資料再套一次（2026-09-10 實測給出過這個建議）。
        if not case_dirs(inbox):
            sys.exit('收件匣裡沒有案例資料夾——沒有東西可以套用。'
                     '（intake.json 若還在，那是上一輪的殘留，不要再套）')
        print('\n⚠ 這一步會寫入**本地**的 images/、cases.json、ground_truth/。')
        print('   收件匣：%s' % inbox)
        if input('   確定要繼續嗎？輸入 yes 繼續：').strip().lower() != 'yes':
            sys.exit('已取消。')
    print('\n生效路徑：收件匣 %s\n          工作單 %s\n' % (inbox, work))
    r = subprocess.run([sys.executable, os.path.join(HERE, 'intake.py')] + args,
                       env=e, cwd=HERE)
    if r.returncode == 0 and cmd in ('group', 'generate'):
        for f in ('_group_worksheet.html' if cmd == 'group' else '_intake_worksheet.html',):
            p = os.path.join(work, f)
            if os.path.exists(p):
                print('\n工作單已產生：%s' % p)
                print('在瀏覽器開啟它填寫，完成後下載 JSON 放回收件匣。')
    sys.exit(r.returncode)


if __name__ == '__main__':
    main()
