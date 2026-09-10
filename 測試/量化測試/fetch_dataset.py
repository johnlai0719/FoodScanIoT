#!/usr/bin/env python3
"""一個指令把量化測試集取回來，並且**證明它拿來就能測**。

為什麼需要這一支：既有的工具各做一半——`make_backup.py` 打包、
`manifest_tool.py` 驗雜湊、README 記位置。但「新的人要怎麼從零跑起來」
散在三個地方，而且最後一段（還原完之後到底能不能用）從來沒有人驗證過。
`manifest.json` 證明的是照片的**同一性**，不是**可用性**：
雜湊全對、但 `cases.json` 少了三案的正解，照樣跑不動而且不會報錯。

  出自 2026-08-16 教授回饋建議 5：hashes verify integrity but not availability。
  這支補的是 availability 的最後一哩：**取得 → 驗證 → 還原 → 自我測試**。

用法：
    python fetch_dataset.py                  # 現況：本地有什麼、有哪些來源可用
    python fetch_dataset.py install          # 取得並還原（自動挑最新的備份）
    python fetch_dataset.py install --from=<路徑或網址>
    python fetch_dataset.py install --version=v2.2+v3.0
    python fetch_dataset.py verify           # 只驗證，不改動任何東西
    python fetch_dataset.py selftest         # 證明現有的資料集可以拿來評分

⚠ **`install` 預設拒絕覆蓋既有的 `ground_truth/` 與 `cases.json`。**
   兩份分岔的正解會讓所有跨期比較失效，而那是這個測試集唯一的價值。
   在已經有資料的機器上要重灌，得明確加 `--force` 並打字確認。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「塩」「菓」…）。
# 沒有這兩行的後果不是亂碼，而是**整批任務在中途拋 UnicodeEncodeError 死掉**。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))

# 已發布的備份。SHA256 抄自 README「備份與取得位置」那張表——
# **那張表是權威，這裡是它的機器可讀版本**。改了一邊要改另一邊。
RELEASES = [
    {'version': 'v4.0', 'date': '2026-09-07', 'cases': 177,
     'file': '量化測試集_v4.0_2026-09-07.zip',
     'sha256': 'c852a3ae4e6b7d9d0bfbf6ccd3033481019956ef402a00c57d024cf5c632d58b'},
    {'version': 'v2.2+v3.0', 'date': '2026-08-18', 'cases': 58,
     'file': '量化測試集_v2.2+v3.0_2026-08-18.zip',
     'sha256': 'fb1987c1e7498ba03445ee31e9f2dbae5b5f3a63926071432c92b9d7e1ea0294'},
]

# 找備份的地方，依序試。第一個是擁有者機器上的 Google Drive 掛載點；
# 組員沒有掛載時要用 `--from=<網址>` 或先自行下載再指路徑。
SEARCH_DIRS = [
    os.environ.get('FOODSCAN_BACKUP_DIR') or '',
    r'G:\我的雲端硬碟\學校資料\專題\食安IoT\測試集備份',
    r'G:\我的雲端硬碟\FoodScan\_backup',
    os.path.join(HERE, '_backup'),
]

# 還原之後必須存在的東西。少一樣就不算裝好——
# 「解壓縮沒報錯」不等於「可以拿來評分」。
REQUIRED = ['cases.json', 'manifest.json', 'ground_truth', 'images']


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    size = os.path.getsize(path)
    done = 0
    # ⚠ 只在真的接著終端機時畫進度。接到管線或記錄檔時 \r 不會覆蓋，
    #    1.2 GB 的檔案會吐出兩百多行「計算雜湊 x%」把其他輸出淹掉。
    show = size > (50 << 20) and sys.stdout.isatty()
    with open(path, 'rb') as f:
        for blk in iter(lambda: f.read(chunk), b''):
            h.update(blk)
            done += len(blk)
            if show:
                print('\r   計算雜湊 %5.1f%%' % (100 * done / size), end='', flush=True)
    if show:
        print('\r' + ' ' * 24 + '\r', end='')
    return h.hexdigest()


def find_local(rel):
    for d in SEARCH_DIRS:
        if not d:
            continue
        p = os.path.join(d, rel['file'])
        if os.path.exists(p):
            return p
    return None


def human(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or u == 'GB':
            return '%.1f %s' % (n, u)
        n /= 1024


def local_state():
    """本地現況。回傳 (已裝好的項目, 缺的項目, 案數)。"""
    have, miss = [], []
    for r in REQUIRED:
        (have if os.path.exists(os.path.join(HERE, r)) else miss).append(r)
    n = 0
    p = os.path.join(HERE, 'cases.json')
    if os.path.exists(p):
        try:
            n = len(json.load(open(p, encoding='utf-8'))['cases'])
        except Exception:
            n = -1
    return have, miss, n


def cmd_status():
    have, miss, n = local_state()
    print('本地現況　%s' % HERE)
    for r in REQUIRED:
        mark = '有' if r in have else '**缺**'
        extra = ''
        if r == 'ground_truth' and r in have:
            extra = '　%d 份正解' % len(
                [f for _, _, fs in os.walk(os.path.join(HERE, r))
                 for f in fs if f.endswith('.json') and not f.startswith('_')])
        if r == 'images' and r in have:
            extra = '　%d 張' % sum(len(fs) for _, _, fs in
                                    os.walk(os.path.join(HERE, r)))
        print('   %-14s %-6s%s' % (r, mark, extra))
    if n > 0:
        print('   cases.json　　%d 案' % n)

    print('\n可取得的備份')
    for r in RELEASES:
        p = find_local(r)
        where = p if p else '（本機找不到，需 --from=<網址或路徑>）'
        print('   %-12s %s　%d 案　%s' % (r['version'], r['date'], r['cases'], where))

    print('\n下一步')
    if miss:
        print('   python fetch_dataset.py install        # 缺 %s' % '、'.join(miss))
    else:
        print('   python fetch_dataset.py selftest       # 已齊全，驗一次能不能評分')


def fetch_to(rel, src, tmpdir):
    """把備份取到本地暫存目錄，回傳檔案路徑。"""
    if src.startswith(('http://', 'https://')):
        import urllib.request
        out = os.path.join(tmpdir, rel['file'])
        print('   下載 %s' % src)

        tty = sys.stdout.isatty()

        def hook(blk, bs, total):
            if total > 0 and tty:
                print('\r   %5.1f%%  %s' % (100 * blk * bs / total, human(total)),
                      end='', flush=True)
        urllib.request.urlretrieve(src, out, hook)
        print()
        return out
    if os.path.isdir(src):
        src = os.path.join(src, rel['file'])
    if not os.path.exists(src):
        sys.exit('找不到備份：%s' % src)
    return src


def cmd_install(args):
    rel = next((r for r in RELEASES if r['version'] == args.version), None) \
        if args.version else RELEASES[0]
    if rel is None:
        sys.exit('沒有這個版本：%s（可用：%s）'
                 % (args.version, '、'.join(r['version'] for r in RELEASES)))
    print('=== 取得量化測試集 %s（%s，%d 案）===\n' % (rel['version'], rel['date'], rel['cases']))

    # ── 防呆：既有資料不可被靜默覆蓋 ────────────────────────────────────
    have, miss, n = local_state()
    blocking = [r for r in ('ground_truth', 'cases.json') if r in have]
    if blocking and not args.force:
        print('已經有資料了：%s' % '、'.join(blocking))
        if n > 0:
            print('   cases.json 目前是 %d 案' % n)
        sys.exit(
            '\n已中止，**沒有動任何檔案**。\n'
            '覆蓋既有的正解會產生兩份分岔的 ground_truth，而跨期比較的前提\n'
            '就是那份正解從頭到尾是同一份。真的要重灌請加 --force。\n'
            '（只是想確認完整性的話：python fetch_dataset.py verify）')

    src = args.source or find_local(rel)
    if not src:
        sys.exit('本機找不到 %s。\n請用 --from=<網址或路徑>，或設 FOODSCAN_BACKUP_DIR。\n'
                 '已找過：\n  %s' % (rel['file'],
                                     '\n  '.join(d for d in SEARCH_DIRS if d)))

    with tempfile.TemporaryDirectory() as tmp:
        zpath = fetch_to(rel, src, tmp)
        print('   來源 %s（%s）' % (zpath, human(os.path.getsize(zpath))))

        # ── 驗證：先確認拿到的是對的東西，再動本地檔案 ──────────────────
        got = sha256(zpath)
        if got != rel['sha256']:
            sys.exit('SHA256 不符，**沒有解開任何檔案**。\n'
                     '   期望 %s\n   實得 %s\n'
                     '下載不完整，或這不是 README 記錄的那一份。'
                     % (rel['sha256'], got))
        print('   SHA256 相符 ✔')

        if args.force and blocking:
            print('\n⚠ --force：即將覆蓋既有的 %s' % '、'.join(blocking))
            if input('   輸入 yes 確認：').strip().lower() != 'yes':
                sys.exit('已取消。')

        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
            print('   解開 %d 個檔案 → %s' % (len(names), HERE))
            z.extractall(HERE)

    print('\n=== 還原完成，開始自我測試 ===\n')
    return selftest(strict=True)


def cmd_verify():
    """只驗證，不改任何東西。"""
    print('=== 驗證（不會改動任何檔案）===\n')
    ok = True
    have, miss, n = local_state()
    if miss:
        print('缺少：%s' % '、'.join(miss))
        return 1
    sys.path.insert(0, HERE)
    try:
        import manifest_tool
        print('圖片完整性（manifest_tool verify）')
        rc = manifest_tool.verify() if hasattr(manifest_tool, 'verify') else None
        if rc is None:
            import subprocess
            rc = subprocess.run([sys.executable, os.path.join(HERE, 'manifest_tool.py'),
                                 'verify'], cwd=HERE).returncode
        ok = ok and (rc == 0)
    except Exception as e:
        print('   manifest 驗證跑不起來：%s' % str(e)[:120])
        ok = False
    return 0 if ok else 1


def selftest(strict=False):
    """證明這份資料集**拿來就能評分**，而不只是檔案都在。

    ⚠ 這一段才是這支腳本存在的理由。`manifest_tool verify` 只回答
       「照片是不是同一批」，回答不了「少了三案的正解會不會靜默少評三案」
       ——而後者正是本專案 2026-09-07 踩過的坑（`EMIT_READERS` 讓 119 案
       被靜默丟掉，指標照樣算得出來，只是分母悄悄變小）。
    """
    print('自我測試')
    fail = []

    p = os.path.join(HERE, 'cases.json')
    if not os.path.exists(p):
        print('   ✗ 找不到 cases.json')
        return 1
    cases = json.load(open(p, encoding='utf-8'))['cases']
    print('   ✔ cases.json 讀得到，%d 案' % len(cases))

    # 1. 每一案都要有正解
    miss_gt = []
    for c in cases:
        gt = os.path.join(HERE, 'ground_truth', c.get('category') or '',
                          c['case_id'] + '.json')
        if not os.path.exists(gt):
            miss_gt.append(c['case_id'])
    if miss_gt:
        fail.append('缺 %d 份正解：%s' % (len(miss_gt), '、'.join(miss_gt[:5])))
        print('   ✗ 缺正解 %d 案' % len(miss_gt))
    else:
        print('   ✔ %d 案都有正解' % len(cases))

    # 2. 正解都要解析得開，而且欄位長得對
    bad, nfield = [], 0
    for c in cases:
        gt = os.path.join(HERE, 'ground_truth', c.get('category') or '',
                          c['case_id'] + '.json')
        if not os.path.exists(gt):
            continue
        try:
            d = json.load(open(gt, encoding='utf-8'))
            nfield += len([k for k, v in d.items() if v not in (None, '', [], {})])
        except Exception as e:
            bad.append('%s（%s）' % (c['case_id'], str(e)[:40]))
    if bad:
        fail.append('正解解析失敗：%s' % '、'.join(bad[:3]))
        print('   ✗ %d 份正解解析失敗' % len(bad))
    else:
        print('   ✔ 正解都解析得開，共 %d 個有值欄位' % nfield)

    # 3. 每一張被引用的照片都要在
    miss_img = []
    nimg = 0
    for c in cases:
        for rel in (c.get('images') or []):
            nimg += 1
            f = os.path.join(HERE, rel.replace('/', os.sep))
            if not os.path.exists(f) and not os.path.exists(
                    os.path.splitext(f)[0] + '.jpg'):
                miss_img.append(rel)
    if miss_img:
        fail.append('缺 %d 張照片：%s' % (len(miss_img), '、'.join(miss_img[:3])))
        print('   ✗ 缺照片 %d 張' % len(miss_img))
    else:
        print('   ✔ %d 張照片都在' % nimg)

    # 4. 照片內容是不是同一批（這才是可跨期比較的前提）
    mp = os.path.join(HERE, 'manifest.json')
    if os.path.exists(mp):
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(HERE, 'manifest_tool.py'),
                            'verify'], cwd=HERE, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        if r.returncode == 0:
            print('   ✔ 照片雜湊與 manifest 相符（與歷次執行同一批）')
        else:
            tail = (r.stdout or r.stderr or '').strip().splitlines()
            fail.append('manifest 驗證未通過：%s' % (tail[-1] if tail else '?'))
            print('   ✗ 照片雜湊與 manifest 不符')

    print()
    if fail:
        print('**未通過**，這份資料集還不能拿來評分：')
        for f in fail:
            print('   - %s' % f)
        return 1
    print('通過。可以評分了：')
    print('   cd %s' % HERE)
    print('   python score_eval.py                     # 對 predictions/ 評分')
    print('   python manifest_tool.py verify           # 之後隨時可再驗一次')
    print('\n要跑完整的本地辨識管線（需 GPU 與模型）：')
    print('   cd ../../PPOCR_TEST && python emit_json.py --report')
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('cmd', nargs='?', default='status',
                    choices=('status', 'install', 'verify', 'selftest'))
    ap.add_argument('--from', dest='source', default=None,
                    help='備份的路徑或網址；省略時自動搜尋')
    ap.add_argument('--version', default=None, help='要哪一版，預設最新')
    ap.add_argument('--force', action='store_true',
                    help='覆蓋既有的 ground_truth／cases.json（會再問一次）')
    a = ap.parse_args()
    if a.cmd == 'status':
        cmd_status()
        return 0
    if a.cmd == 'install':
        return cmd_install(a)
    if a.cmd == 'verify':
        return cmd_verify()
    return selftest()


if __name__ == '__main__':
    sys.exit(main())
