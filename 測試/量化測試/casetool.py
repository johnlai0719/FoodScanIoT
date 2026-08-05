#!/usr/bin/env python3
# 測試案例的建檔與一致性檢查。
#
# 為何需要：新增一個案例要同時動四個地方——照片放進 images/<case_id>/、
# cases.json 加一筆、ground_truth/<case_id>.json 建正解、manifest 重新產生。
# 漏掉任一個的後果都不會立刻報錯，而是安靜地少評一案或多算一案；
# 要補 25 個案例時，這種錯誤幾乎必然發生一次。
#
# 用法：
#   python casetool.py check                       # 檢查四處是否一致（先跑這個）
#   python casetool.py new c50_保健食品膠囊 --non-food \
#       --desc="某某葉黃素膠囊外盒" --difficulty=small_text
#   python casetool.py new c60_反光鋁袋洋芋片 --category=snack \
#       --desc="某某洋芋片" --difficulty=glare,crease
#
# new 只建骨架，正解仍須人工填寫——那是本測試集唯一不可由程式產生的東西。
# 非食品案例例外：其正解只有 is_food_label=false 一個欄位，可直接寫完。
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, 'cases.json')
GT = os.path.join(HERE, 'ground_truth')
IMAGES = os.environ.get('EVAL_IMAGE_ROOT') or os.path.join(HERE, 'images')
MANIFEST = os.path.join(HERE, 'manifest.json')

_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.heic')

VALID_DIFFICULTY = {'glare', 'curved', 'crease', 'low_light',
                    'angled', 'small_text', 'occluded'}
VALID_CATEGORY = {'beverage', 'prepared_meal', 'snack', 'instant_noodle',
                  'canned_food', 'supplement_food', 'non_food'}

# 食品案例的正解骨架。欄位名與 score_eval.py 讀取的一致；填不到的留 null，
# **不可填 0**——「標示上沒有」與「含量為零」是兩件事，混淆會讓評分失真。
FOOD_SKELETON = {
    "is_food_label": True,
    "name": "", "brand": "", "manufacturer": "",
    "ingredients_raw": "",
    "ingredients_list": [],
    "nutrition": {k: None for k in
                  ['calories', 'protein', 'fat', 'saturated_fat', 'trans_fat',
                   'carbohydrates', 'sugar', 'fiber', 'sodium']},
    "nutrition_per_serving": {k: None for k in
                              ['calories', 'protein', 'fat', 'saturated_fat', 'trans_fat',
                               'carbohydrates', 'sugar', 'fiber', 'sodium']},
    "serving_size": None,
    "servings_per_container": None,
    "allergy_warning": "",
    "certification_marks": [],
}


def load_cases():
    return json.load(open(CASES, encoding='utf-8'))


def save_cases(data):
    with open(CASES, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def images_for(case_id, category):
    """列出 images/<category>/<case_id>/ 底下的圖片，回傳相對於 images root 的路徑。"""
    d = os.path.join(IMAGES, category, case_id)
    if not os.path.isdir(d):
        return []
    return [f'images/{category}/{case_id}/{fn}' for fn in sorted(os.listdir(d))
            if fn.lower().endswith(_EXTS)]


def gt_path(case_id, category):
    return os.path.join(GT, category, f'{case_id}.json')


def scan_gt():
    """{case_id: 所在類別資料夾}。底線開頭的資料夾為歸檔區，略過。"""
    out = {}
    if not os.path.isdir(GT):
        return out
    for cat in os.listdir(GT):
        d = os.path.join(GT, cat)
        if not os.path.isdir(d) or cat.startswith('_'):
            continue
        for fn in os.listdir(d):
            if fn.endswith('.json'):
                out[fn[:-5]] = cat
    return out


def scan_img_dirs():
    """{case_id: 所在類別資料夾}。"""
    out = {}
    if not os.path.isdir(IMAGES):
        return out
    for cat in os.listdir(IMAGES):
        d = os.path.join(IMAGES, cat)
        if not os.path.isdir(d) or cat.startswith('_'):
            continue
        for cid in os.listdir(d):
            if os.path.isdir(os.path.join(d, cid)):
                out[cid] = cat
    return out


# ─── check ────────────────────────────────────────────────────────────────────

def check():
    data = load_cases()
    cases = {c['case_id']: c for c in data['cases']}
    problems = []

    gt_at = scan_gt()          # case_id → 正解所在的類別資料夾
    img_at = scan_img_dirs()   # case_id → 圖片所在的類別資料夾

    for cid in sorted(set(cases) - set(gt_at)):
        problems.append(f"[缺正解] {cid} 在 cases.json 內，但 ground_truth/ 底下找不到"
                        f" → 該案會被靜默略過，不列入任何指標")
    for cid in sorted(set(gt_at) - set(cases)):
        problems.append(f"[孤兒正解] ground_truth/{gt_at[cid]}/{cid}.json"
                        f" 沒有對應的 cases.json 項目")
    for cid in sorted(set(img_at) - set(cases)):
        problems.append(f"[未登記] images/{img_at[cid]}/{cid}/ 存在，"
                        f"但 cases.json 沒有這個案例")

    for cid, c in sorted(cases.items()):
        # 圖片實際存在
        for rel in c.get('images', []):
            p = os.path.join(IMAGES, rel[len('images/'):]) if rel.startswith('images/') \
                else os.path.join(HERE, rel)
            if not os.path.exists(p):
                problems.append(f"[圖片不存在] {cid} → {rel}")
        if not c.get('images'):
            problems.append(f"[無圖片] {cid} 沒有列出任何照片")

        # 欄位完整
        if not c.get('set_version'):
            problems.append(f"[缺 set_version] {cid} → 不會出現在任何版本切片中")
        cat = c.get('category')
        if not cat:
            problems.append(f"[缺 category] {cid}")
        elif cat not in VALID_CATEGORY:
            problems.append(f"[未知 category] {cid} → '{cat}'（可用：{sorted(VALID_CATEGORY)}）")

        # 類別現在同時存在於兩處：cases.json 的欄位、以及資料夾位置。
        # 兩者必須一致——分類改了卻只改一邊，就會出現「JSON 說是零食、
        # 檔案卻放在飲料資料夾」的狀態，人工審核與程式評分看到的分類不同。
        # 此檢查是允許用資料夾表達分類的前提。
        if cat and cid in img_at and img_at[cid] != cat:
            problems.append(f"[分類不一致] {cid} category={cat}，"
                            f"但圖片放在 images/{img_at[cid]}/")
        if cat and cid in gt_at and gt_at[cid] != cat:
            problems.append(f"[分類不一致] {cid} category={cat}，"
                            f"但正解放在 ground_truth/{gt_at[cid]}/")
        for d in c.get('difficulty') or []:
            if d not in VALID_DIFFICULTY:
                problems.append(f"[未知 difficulty] {cid} → '{d}'"
                                f"（可用：{sorted(VALID_DIFFICULTY)}）")

        # 正解與 category 是否自相矛盾
        gp = gt_path(cid, gt_at.get(cid, cat or ''))
        if os.path.exists(gp):
            try:
                gt = json.load(open(gp, encoding='utf-8'))
            except json.JSONDecodeError as e:
                problems.append(f"[正解格式錯誤] {cid} → {e}")
                continue
            flag = gt.get('is_food_label')
            if flag is None:
                problems.append(f"[正解缺 is_food_label] {cid} → 相關性閘門不會評到這案")
            elif cat == 'non_food' and flag is not False:
                problems.append(f"[矛盾] {cid} category=non_food 但正解 is_food_label={flag}")
            elif cat and cat != 'non_food' and flag is not True:
                problems.append(f"[矛盾] {cid} category={cat} 但正解 is_food_label={flag}")

    # manifest 是否跟得上
    if os.path.exists(MANIFEST):
        listed = set(json.load(open(MANIFEST, encoding='utf-8'))['files'])
        actual = set()
        for root, _, files in os.walk(IMAGES):
            for fn in files:
                if fn.lower().endswith(_EXTS):
                    rel = os.path.relpath(os.path.join(root, fn), IMAGES).replace(os.sep, '/')
                    actual.add(rel)
        if listed != actual:
            problems.append(f"[manifest 過期] 清單 {len(listed)} 張、實際 {len(actual)} 張"
                            f" → 跑 python manifest_tool.py generate")
    else:
        problems.append("[無 manifest] 跑 python manifest_tool.py generate")

    # 組成摘要：非食品比例是目前最該盯的數字
    by_ver, by_cat, n_diff = {}, {}, 0
    for c in cases.values():
        by_ver[c.get('set_version') or '?'] = by_ver.get(c.get('set_version') or '?', 0) + 1
        by_cat[c.get('category') or '?'] = by_cat.get(c.get('category') or '?', 0) + 1
        if c.get('difficulty'):
            n_diff += 1
    n = len(cases)
    n_nonfood = by_cat.get('non_food', 0)

    print(f"案例 {n} 件")
    print("  版本：" + "、".join(f"{k} {v}" for k, v in sorted(by_ver.items())))
    print("  類別：" + "、".join(f"{k} {v}" for k, v in sorted(by_cat.items())))
    print(f"  非食品 {n_nonfood} 件（{n_nonfood / n * 100:.0f}%）"
          f"{'  ← 負例過少，相關性閘門的精確率不可信' if n_nonfood < 5 else ''}")
    print(f"  已標困難度 {n_diff} 件"
          f"{'  ← 無人標註，by_difficulty 切片會是空的' if n_diff == 0 else ''}")

    if problems:
        print(f"\n{len(problems)} 個問題：")
        for p in problems:
            print("  " + p)
        return 1
    print("\n[OK] cases.json、ground_truth/、images/、manifest.json 四處一致。")
    return 0


# ─── new ──────────────────────────────────────────────────────────────────────

def new(argv):
    case_id = argv[0]
    opts = {}
    non_food = False
    for a in argv[1:]:
        if a == '--non-food':
            non_food = True
        elif a.startswith('--') and '=' in a:
            k, v = a[2:].split('=', 1)
            opts[k] = v
        else:
            sys.exit(f"看不懂的參數：{a}")

    data = load_cases()
    if any(c['case_id'] == case_id for c in data['cases']):
        sys.exit(f"{case_id} 已存在於 cases.json")

    category = 'non_food' if non_food else opts.get('category')
    if not category:
        sys.exit("食品案例需指定 --category=（或用 --non-food）")
    if category not in VALID_CATEGORY:
        sys.exit(f"未知 category：{category}（可用：{sorted(VALID_CATEGORY)}）")

    imgs = images_for(case_id, category)
    if not imgs:
        # 也接受照片暫放在 images/ 底下未分類處，代為搬到正確的類別資料夾——
        # 從手機匯入時很難記得先建對資料夾，讓工具處理比讓人記得可靠。
        loose = os.path.join(IMAGES, case_id)
        if os.path.isdir(loose):
            os.makedirs(os.path.join(IMAGES, category), exist_ok=True)
            shutil.move(loose, os.path.join(IMAGES, category, case_id))
            print(f"  照片已自 images/{case_id}/ 移至 images/{category}/{case_id}/")
            imgs = images_for(case_id, category)
        else:
            sys.exit(f"找不到照片。請把照片放進 "
                     f"{os.path.join(IMAGES, category, case_id)}/ "
                     f"（或暫放 {loose}/，本工具會代為歸位）")

    difficulty = [d for d in (opts.get('difficulty') or '').split(',') if d]
    bad = set(difficulty) - VALID_DIFFICULTY
    if bad:
        sys.exit(f"未知 difficulty：{sorted(bad)}（可用：{sorted(VALID_DIFFICULTY)}）")

    data['cases'].append({
        "case_id": case_id,
        "desc": opts.get('desc', ''),
        "barcode": "TEST",
        "images": imgs,
        "tags": {"is_food": not non_food},
        "set_version": opts.get('set_version', 'v3'),
        "category": category,
        "difficulty": difficulty,
    })
    data['cases'].sort(key=lambda c: c['case_id'])
    save_cases(data)

    gp = gt_path(case_id, category)
    os.makedirs(os.path.dirname(gp), exist_ok=True)
    if os.path.exists(gp):
        print(f"[略過] {gp} 已存在，未覆寫")
    else:
        # 非食品的正解只有一個欄位，可直接完成；食品的骨架須人工填寫
        gt = {"is_food_label": False} if non_food else dict(FOOD_SKELETON)
        with open(gp, 'w', encoding='utf-8') as f:
            json.dump(gt, f, ensure_ascii=False, indent=2)

    print(f"已新增 {case_id}（{category}，{len(imgs)} 張照片"
          + (f"，困難度 {'/'.join(difficulty)}" if difficulty else "") + "）")
    if non_food:
        print("  正解已完成（非食品只需 is_food_label=false）")
    else:
        print(f"  → 請填寫 ground_truth/{category}/{case_id}.json，"
              f"照著照片上看得到的填，看不到的留 null（不可填 0）")
    print("  → 接著跑：python manifest_tool.py generate && python casetool.py check")


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if cmd == 'check':
        sys.exit(check())
    elif cmd == 'new':
        if len(sys.argv) < 3:
            sys.exit("用法：casetool.py new <case_id> [--non-food | --category=X] "
                     "[--desc=X] [--difficulty=a,b]")
        new(sys.argv[2:])
    else:
        sys.exit("用法：casetool.py [check|new]")
