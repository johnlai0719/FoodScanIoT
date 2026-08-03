"""
清除不可信任欄位

目標：移除所有來自 PubChem 名稱搜尋的衍生資料，
     保留 TFDA 官方資料與人工審核資料。

受影響檔案：
  01_主資料庫/additives_master_candidate.json
  02_MVP輸出/all_enriched.json

用法：
  python clean_unreliable_fields.py          ← dry-run（只顯示，不寫入）
  python clean_unreliable_fields.py --apply  ← 真正執行（自動備份原檔）
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
MASTER_PATH  = os.path.join(ROOT, '01_主資料庫', 'additives_master_candidate.json')
ENRICHED_PATH = os.path.join(ROOT, '02_MVP輸出', 'all_enriched.json')

# ── 從 master 頂層移除這些欄位（全部來自 PubChem 名稱搜尋）──────────────
MASTER_FIELDS_TO_REMOVE = {
    'pubchem_cid',
    'molecular_formula',
    'molecular_weight',
    'inchikey',
    'ghs_hazard_codes',
    'ghs_signal_word',
    'function_class_jecfa',
    'cas_number',
}

# ── evidence_items 中，source_title 包含這些字串的條目視為 PubChem 來源 ──
PUBCHEM_SOURCE_MARKERS = ('PubChem',)


def is_pubchem_evidence(ev):
    title = ev.get('source_title', '')
    return any(marker in title for marker in PUBCHEM_SOURCE_MARKERS)


def clean_master(data, dry_run):
    removed_fields = {f: 0 for f in MASTER_FIELDS_TO_REMOVE}
    removed_evidence = 0
    total_evidence_before = 0
    total_evidence_after = 0

    cleaned = []
    for r in data:
        record = dict(r)

        # 移除頂層欄位
        for field in MASTER_FIELDS_TO_REMOVE:
            if field in record:
                removed_fields[field] += 1
                if not dry_run:
                    del record[field]

        # 清理 evidence_items
        evs = record.get('evidence_items', [])
        total_evidence_before += len(evs)
        kept = [ev for ev in evs if not is_pubchem_evidence(ev)]
        removed_evidence += len(evs) - len(kept)
        total_evidence_after += len(kept)
        if not dry_run:
            record['evidence_items'] = kept

        cleaned.append(record)

    return cleaned, removed_fields, removed_evidence, total_evidence_before, total_evidence_after


def clean_enriched(data, dry_run):
    reset_adi = 0
    removed_evidence = 0

    cleaned = []
    for r in data:
        record = dict(r)

        # adi_status 重置為 unknown（PubChem 推斷，不可信）
        if record.get('adi_status') and record['adi_status'] != 'unknown':
            reset_adi += 1
            if not dry_run:
                record['adi_status'] = 'unknown'
        elif 'adi_status' in record and not dry_run:
            record['adi_status'] = 'unknown'

        # new_evidence 移除 PubChem 條目
        evs = record.get('new_evidence', [])
        kept = [ev for ev in evs if not is_pubchem_evidence(ev)]
        removed_evidence += len(evs) - len(kept)
        if not dry_run:
            record['new_evidence'] = kept

        cleaned.append(record)

    return cleaned, reset_adi, removed_evidence


def backup(path):
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    bak = path + f'.bak_{ts}'
    shutil.copy2(path, bak)
    return bak


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='真正執行（否則只 dry-run）')
    args = parser.parse_args()
    dry_run = not args.apply

    if dry_run:
        print('=' * 55)
        print('DRY-RUN 模式（不會修改任何檔案）')
        print('加上 --apply 參數才會真正執行')
        print('=' * 55)
    else:
        print('=' * 55)
        print('APPLY 模式 — 正在修改檔案')
        print('=' * 55)

    # ── Master ────────────────────────────────────────────
    print('\n【01_主資料庫/additives_master_candidate.json】')
    with open(MASTER_PATH, encoding='utf-8') as f:
        master_data = json.load(f)

    cleaned_master, removed_fields, rm_ev, ev_before, ev_after = clean_master(master_data, dry_run)

    print(f'  總筆數：{len(master_data)}')
    print(f'  移除頂層欄位：')
    for field, count in removed_fields.items():
        if count > 0:
            print(f'    - {field}：{count} 筆')
    print(f'  evidence_items：{ev_before} → {ev_after}（移除 {rm_ev} 筆 PubChem 來源）')

    if not dry_run:
        bak = backup(MASTER_PATH)
        print(f'  備份：{os.path.basename(bak)}')
        with open(MASTER_PATH, 'w', encoding='utf-8') as f:
            json.dump(cleaned_master, f, ensure_ascii=False, indent=2)
        print(f'  [OK] 已寫入')

    # ── Enriched ──────────────────────────────────────────
    print('\n【02_MVP輸出/all_enriched.json】')
    with open(ENRICHED_PATH, encoding='utf-8') as f:
        enriched_data = json.load(f)

    cleaned_enriched, reset_adi, rm_ev2 = clean_enriched(enriched_data, dry_run)

    print(f'  總筆數：{len(enriched_data)}')
    print(f'  adi_status 重置為 unknown：{reset_adi if reset_adi > 0 else len(enriched_data)} 筆')
    print(f'  new_evidence 移除 PubChem 來源：{rm_ev2} 筆')

    if not dry_run:
        bak = backup(ENRICHED_PATH)
        print(f'  備份：{os.path.basename(bak)}')
        with open(ENRICHED_PATH, 'w', encoding='utf-8') as f:
            json.dump(cleaned_enriched, f, ensure_ascii=False, indent=2)
        print(f'  [OK] 已寫入')

    # ── 摘要 ──────────────────────────────────────────────
    print()
    print('=' * 55)
    if dry_run:
        print('以上是預覽。確認無誤後，執行：')
        print('  python clean_unreliable_fields.py --apply')
    else:
        print('清理完成。目前可信任的欄位：')
        print('  [OK] TFDA：名稱、INS 號碼、功能分類、用量規定、法規狀態')
        print('  [OK] 人工審核：group_risks（白名單）')
        print('  [待補] consumer_description、adi_status（JECFA 官方）')
    print('=' * 55)


if __name__ == '__main__':
    main()
