"""
資料夾整理腳本

將歷史檔案移至 99_歸檔/，只保留目前仍在使用的檔案。

用法：
  python 04_工具腳本/reorganize.py          ← dry-run（只列出動作）
  python 04_工具腳本/reorganize.py --apply  ← 真正執行
"""

import argparse
import os
import shutil
import sys

sys.stdout.reconfigure(encoding='utf-8')

ROOT    = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
ARCHIVE = os.path.join(ROOT, '99_歸檔')


def rel(path):
    return os.path.relpath(path, ROOT)


def move(src, dest_dir, dry_run):
    if not os.path.exists(src):
        print(f'  [跳過] 不存在：{rel(src)}')
        return
    dest = os.path.join(dest_dir, os.path.basename(src))
    print(f'  移動：{rel(src)}  ->  {rel(dest)}')
    if not dry_run:
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(src, dest)


def remove_empty_dir(path, dry_run):
    if os.path.isdir(path) and not os.listdir(path):
        print(f'  刪除空資料夾：{rel(path)}')
        if not dry_run:
            os.rmdir(path)


def delete(path, dry_run):
    if not os.path.exists(path):
        return
    print(f'  刪除：{rel(path)}')
    if not dry_run:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    dry_run = not args.apply

    if dry_run:
        print('=' * 55)
        print('DRY-RUN — 加上 --apply 才會真正執行')
        print('=' * 55)
    else:
        print('=' * 55)
        print('APPLY — 正在整理')
        print('=' * 55)

    # ── 01_主資料庫 ───────────────────────────────────────────
    print('\n【01_主資料庫】')
    arch = os.path.join(ARCHIVE, '舊主資料庫')
    db_dir = os.path.join(ROOT, '01_主資料庫')
    if os.path.exists(db_dir):
        # 舊 master（已由 tfda_clean 取代）
        move(os.path.join(db_dir, 'additives_master_candidate.json'), arch, dry_run)
        # 備份檔
        for f in os.listdir(db_dir):
            if '.bak_' in f:
                move(os.path.join(db_dir, f), arch, dry_run)

    # ── 02_MVP輸出 ────────────────────────────────────────────
    print('\n【02_MVP輸出】')
    arch = os.path.join(ARCHIVE, '舊MVP輸出')
    mvp_dir = os.path.join(ROOT, '02_MVP輸出')
    if os.path.exists(mvp_dir):
        for f in os.listdir(mvp_dir):
            move(os.path.join(mvp_dir, f), arch, dry_run)
        remove_empty_dir(mvp_dir, dry_run)

    # ── 03_enrichment補充 ─────────────────────────────────────
    print('\n【03_enrichment補充】')
    arch = os.path.join(ARCHIVE, '舊enrichment')
    enrich_dir = os.path.join(ROOT, '03_enrichment補充')
    
    keep_enrichment = {
        'gemini_patch.json',
        'ncbi_patch.json',
        'ncbi_raw',
        'all_slim.json',
        'Prompt.md',
        'enrichment_prompt_template.md',
        'batch_input.json',
        'batch_output.json',
        '_cache'
    }

    if os.path.exists(enrich_dir):
        for item in os.listdir(enrich_dir):
            if item in keep_enrichment:
                continue
            item_path = os.path.join(enrich_dir, item)
            move(item_path, arch, dry_run)

    # ── 04_工具腳本 ───────────────────────────────────────────
    print('\n【04_工具腳本】')
    arch = os.path.join(ARCHIVE, '舊工具腳本')
    scripts_dir = os.path.join(ROOT, '04_工具腳本')

    keep_scripts = {
        'run_all.py',
        'agent_batch_coordinator.py',
        'local_synthesis.py',
        'gemini_enrich.py',
        'clean_unreliable_fields.py',
        'export_tfda_clean.py',
        'process_data.py',
        'reorganize.py',
        '__pycache__',
        'archive_一次性腳本',
        'vision_test'
    }

    if os.path.exists(scripts_dir):
        for item in os.listdir(scripts_dir):
            if item in keep_scripts:
                continue
            item_path = os.path.join(scripts_dir, item)
            move(item_path, arch, dry_run)

    # ── 完成 ──────────────────────────────────────────────────
    print()
    print('=' * 55)
    if dry_run:
        print('預覽完畢，確認後執行：')
        print('  python 04_工具腳本/reorganize.py --apply')
    else:
        print('整理完成。剩餘結構：')
        for item in sorted(os.listdir(ROOT)):
            if item.startswith('.'):
                continue
            full = os.path.join(ROOT, item)
            marker = '/' if os.path.isdir(full) else ''
            print(f'  {item}{marker}')
    print('=' * 55)


if __name__ == '__main__':
    main()
