#!/usr/bin/env python3
# 依 ingredients_raw 重新產生正解的 ingredients_list。
#
# 為何要有這支：人工校對時改的是 `ingredients_raw`（那是逐字原文，對著照片改），
# `ingredients_list` 不會跟著動，兩者就此脫節——0906 那批 42 案改了原文，
# 清單全部還是舊初稿，c104 的原文幾乎整段換掉、清單一項沒變。
#
# 切分規則來自正解欄位定義，是**機械規則不是判斷**：
#   `raw` 是整段原文逐字照抄，`list` 是它的切分結果
#   巢狀配方整團保留成一項——`調味劑(L-麩酸鈉、5'-次黃嘌呤核苷磷酸二鈉)` 算一項，
#   括號連同內容留在該項裡
# 所以在**括號深度 0** 的分隔符切開即可。
#
# ⚠ **不重用 bench_ingredients.split_top()**。那支是給 OCR 文字用的，
# 內建「括號開著超過 120 字就當收尾被讀丟、強制歸零」的補償（OPEN_MAX）。
# 正解是人工校對過的乾淨文字，套那個補償會把長的巢狀配方**無聲切開**。
# 這裡改成嚴格計數：括號不成對就報錯給人修，不自己猜。
#
# 也不做任何清洗（不去重、不改錯字、不補漏）——那些屬於人工校對，
# 程式擅自動手會讓「正解是人寫的」這件事不再成立。
#
# 用法：
#   python gt_split.py <intake.json>            # 只看會改什麼，不寫檔
#   python gt_split.py <intake.json> --write    # 確認後寫回（自動備份）
import argparse
import io
import json
import os
import re
import shutil
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

# 逐字原文本身的瑕疵，**來源是包裝印刷而非轉錄錯誤**。
#
# 為什麼要有這個：正解的規矩是逐字照抄，所以印錯的括號必須照抄下來。
# 但那會讓機械切分永遠失敗、每次跑都叫人「去修 raw」——遲早有人真的去改，
# 逐字原文就毀了。標記起來，把「印刷錯誤」與「還沒修」分開。
#
# ⚠ 值域鎖死。標之前要**對著照片確認**是包裝印錯，不是自己打漏。
VALID_RAW_ISSUE = {'print_error_bracket'}
RAW_ISSUE_ZH = {'print_error_bracket': '包裝印刷的括號不成對'}

OPEN, CLOSE = '([{（〔【[｛', ')]}）〕】]｝'
SEP = '、,，;；·'
HARD = '。\n\r'              # 分段界：泡麵的麵體／調味粉包／調味油包之間
TAIL = '。.;；、 　'          # 整段結尾的標點，切完丟掉

# 段落標題。既有正解 c57「麵：」、c59「2.調味粉包：」、c107「【麵】」都不進清單，
# 各段成分攤平成同一個清單（c57 的 31 項含麵體＋粉包＋油包）。
#
# 一律要求有冒號或全形括號才算標題，避免吃掉真的成分——
# `精製棕櫚油[含抗氧化劑(第三丁基氫醌)]` 的方括號後面接的是「、」不是冒號，不會被誤判。
# 段落標題的三種寫法（2026-09-07 補後兩種）：
#   麵：／2.調味粉包：／【麵】     ← 第一版就認得
#   成分(台灣)：馬鈴薯粉           ← 標題中間夾括號，舊正則的中文字串被括號打斷
#   [調味油包]精製豬油〔…〕        ← 方括號標題**後面沒有冒號**
# 方括號沒冒號時要跟「整組成分被方括號包住」區分開
# （`[黃豆(基因改造)、水、小麥、食鹽]` 是成分不是標題）：
# 標題短、且裡面不含頓號，所以限長 6 字並排除分隔符。
HEADER = re.compile(
    r'^\s*\d{0,2}\s*[.、]?\s*'
    r'(?:【[^】]{1,12}】\s*[:：]?'
    r'|\[[^\]、,，]{1,6}\]\s*[:：]?'
    r'|\[[^\]]{1,14}\]\s*[:：]'
    r'|[一-鿿\s]{1,6}[(（][^)）]{1,10}[)）]\s*[:：]'
    r'|[一-鿿][一-鿿\s]{0,7}[:：])'
    r'\s*')

# 混進成分清單、但根本不是成分的東西。目前只有淨重／固形量標示——
# `(固形量25公克，內容量100公克)` 出現在 c69–c72 的成分欄尾巴。
# ⚠ 值域鎖死。這是「機械可判、且明顯不是成分」才收，
# 不是拿來刪掉不方便的項目。
JUNK = re.compile(r'^[(（]?(?:固形量|內容量|淨重)[^)）]*[)）]?$')
# 標題出現在句中時（c124「著色劑(…) [宮崎芒果味]: 砂糖」）前面沒有分隔符，
# 切不開。先在標題前插入分段界。
INLINE_HEAD = re.compile(
    r'(?<!^)(?=【[^】]{1,12}】|\[[^\]、,，]{1,6}\]|\[[^\]]{1,14}\]\s*[:：])')


def _strip_head(x):
    """剝掉開頭的段落標題，可能疊了兩層（c124「成 分:[北海道哈密瓜味]:」）。"""
    for _ in range(3):
        y = HEADER.sub('', x, count=1)
        if y == x:
            break
        x = y
    return x


def split_top(raw, clamp=False):
    """在括號深度 0 的分隔符切開。回傳 (項目, 括號是否成對)。

    深度嚴格計數：出現多餘的收尾括號（深度轉負）或結尾深度不為 0，
    都回報 False——那是正解本身的錯，該讓人去看照片，不是程式補。
    ⚠ 不做 bench_ingredients 那個「開太久就強制歸零」的 OCR 補償，理由見檔頭。

    `clamp=True` 時把深度夾在 0 以上，給 `raw_issue` 已標記的案例用——
    括號不成對是**包裝上印錯**、逐字原文照抄無誤，多出來的收尾括號應理解為
    「後面的成分回到最上層」。c60 就是這樣（廠商在 `蝦香精))` 多印一個括號）。
    """
    out, buf, depth, ok = [], [], 0, True

    def flush():
        if buf:
            out.append(_strip_head(''.join(buf)).strip(TAIL).strip())
            buf.clear()

    for ch in INLINE_HEAD.sub('\n', raw):
        if ch in OPEN:
            depth += 1
        elif ch in CLOSE:
            depth -= 1
            if depth < 0:
                depth = 0
                if not clamp:
                    ok = False
        elif (ch in SEP or ch in HARD) and depth == 0:
            flush()
            continue
        buf.append(ch)
    flush()
    if depth != 0:
        ok = False
    return [x for x in out if x and not JUNK.match(x)], ok


def run_gt(version, write):
    """直接作用在 ground_truth/，給已經 apply 過的案例用。

    ⚠ **預設只動指定版本**。舊的 v2.2／v3.0 清單不是機械切分的產物——
    它們帶著正規化漂移（全形逗號改半形、〔〕改寫成 ()、c14/c56 刪掉了
    `(符合CNS 3055生乳標準)` 這類註記），重新產生會改到已凍結的數字。
    那些列在正解欄位定義的待修第 5 項，要動得另外決定。
    """
    import casetool
    cs = {c['case_id']: c for c in casetool.load_cases()['cases']}
    changed, problems, noted = [], [], []
    files = []
    for cid, c in sorted(cs.items()):
        if version and c.get('set_version') != version:
            continue
        gp = casetool.gt_path(cid, c['category'])
        if not os.path.exists(gp):
            continue
        g = json.load(io.open(gp, encoding='utf-8'))
        if not g.get('is_food_label'):
            continue
        raw = (g.get('ingredients_raw') or '').strip()
        if not raw:
            continue
        issue = c.get('raw_issue')
        if issue and issue not in VALID_RAW_ISSUE:
            problems.append(f'{cid}：未知 raw_issue {issue!r}')
            continue
        new, ok = split_top(raw, clamp=bool(issue))
        if issue:
            noted.append(f'{cid}：{RAW_ISSUE_ZH[issue]}，以夾在 0 的方式切分')
        elif not ok:
            problems.append(f'{cid}：括號不成對，維持原清單不動')
            continue
        old = g.get('ingredients_list') or []
        if new != old:
            changed.append((cid, old, new))
            g['ingredients_list'] = new
            files.append((gp, g))

    print(f'版本 {version or "全部"}｜清單有變動 {len(changed)} 案｜'
          f'有問題 {len(problems)}')
    print()
    for m in problems:
        print('  [待處理] ' + m)
    for m in noted:
        print('  [已標記] ' + m)
    print()
    for cid, old, new in changed:
        print(f'{cid}  {len(old)} → {len(new)} 項')
        rm = [x for x in old if x not in new]
        add = [x for x in new if x not in old]
        if rm:
            print('   － ' + '、'.join(x[:40] for x in rm[:6]))
        if add:
            print('   ＋ ' + '、'.join(x[:40] for x in add[:6]))
    if not write:
        print('\n（試跑，沒有寫檔。確認後加 --write）')
        return
    for gp, g in files:
        bak = gp + time.strftime('.bak-%m%d-%H%M%S')
        n = 0
        while os.path.exists(bak):
            n += 1
            bak = gp + time.strftime('.bak-%m%d-%H%M%S') + '-%d' % n
        shutil.copy(gp, bak)
        with io.open(gp, 'w', encoding='utf-8') as f:
            json.dump(g, f, ensure_ascii=False, indent=2)
    print(f'\n已寫回 {len(files)} 份正解（各自留 .bak）')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path', nargs='?', help='intake.json 路徑；用 --gt 時可省略')
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--gt', action='store_true',
                    help='直接改 ground_truth/（已 apply 的案例）')
    ap.add_argument('--version', default='v4.0',
                    help='--gt 時只動這個版本，預設 v4.0')
    a = ap.parse_args()

    if a.gt:
        run_gt(a.version, a.write)
        return
    if not a.path:
        sys.exit('要嘛給 intake.json 路徑，要嘛加 --gt')

    data = json.load(io.open(a.path, encoding='utf-8'))
    changed, problems, empty, noted = [], [], [], []

    for cid, e in sorted(data.items()):
        if e.get('category') == 'non_food':
            continue
        gt = e.get('gt') or {}
        raw = (gt.get('ingredients_raw') or '').strip()
        old = gt.get('ingredients_list') or []
        if not raw:
            if old:
                problems.append(f'{cid}：raw 空的但 list 有 {len(old)} 項')
            else:
                empty.append(cid)
            continue
        issue = e.get('raw_issue')
        if issue and issue not in VALID_RAW_ISSUE:
            problems.append(f'{cid}：未知 raw_issue {issue!r}'
                            f'（可用：{sorted(VALID_RAW_ISSUE)}）')
            continue
        new, ok = split_top(raw, clamp=bool(issue))
        if issue:
            noted.append(f'{cid}：{RAW_ISSUE_ZH[issue]}，以夾在 0 的方式切分')
        elif not ok:
            # 括號不成對時切出來是垃圾（c59 就切成 2 項）。**不覆蓋**——
            # 寧可留著舊清單等人修 raw，也不要用壞掉的結果蓋掉還能看的東西。
            problems.append(f'{cid}：括號不成對，維持原清單不動 → 請對照片修 raw')
            continue
        if new != old:
            changed.append((cid, old, new))
        gt['ingredients_list'] = new

    print(f'案例 {len(data)}｜清單有變動 {len(changed)}｜'
          f'成分原文為空 {len(empty)}｜有問題 {len(problems)}')
    print()
    if problems:
        print('── 要人處理的 ──')
        for m in problems:
            print('  ' + m)
        print()
    if noted:
        print('── 已標記為原文瑕疵（不必修 raw）──')
        for m in noted:
            print('  ' + m)
        print()

    print('── 變動明細 ──')
    for cid, old, new in changed:
        print(f'{cid}  {len(old)} → {len(new)} 項')
        add = [x for x in new if x not in old]
        rm = [x for x in old if x not in new]
        if add:
            print('   ＋ ' + '、'.join(add[:8]) + (' …' if len(add) > 8 else ''))
        if rm:
            print('   － ' + '、'.join(rm[:8]) + (' …' if len(rm) > 8 else ''))

    if not a.write:
        print()
        print('（試跑，沒有寫檔。確認後加 --write）')
        return

    # 檔名要防撞：同一秒內跑兩次會讓第二個備份蓋掉第一個，
    # 結果備份與寫入後的檔案一模一樣，等於沒備份（2026-09-06 真的發生過）。
    bak = a.path + time.strftime('.bak-%m%d-%H%M%S')
    n = 0
    while os.path.exists(bak):
        n += 1
        bak = a.path + time.strftime('.bak-%m%d-%H%M%S') + '-%d' % n
    shutil.copy(a.path, bak)
    with io.open(a.path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print()
    print(f'已寫回 {os.path.basename(a.path)}（備份 {os.path.basename(bak)}）')


if __name__ == '__main__':
    main()
