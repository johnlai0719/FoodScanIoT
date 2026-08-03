#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步驟 1c：TFDA 規格標準 PDF 下載與對照爬蟲
說明：自動循環 TFDA 食品添加物細節頁面 (id=1 到 820)，
      若頁面中包含規格標準 PDF 下載點，則自動下載存檔至 raw/tfda_specs/ 目錄，
      若無 PDF 則跳過（此為正常現象），最後輸出對照清單 tfda_pdf_map.csv。
"""
import os
import sys
import re
import csv
import time
import random
import urllib.request
import urllib.parse
import ssl
from html import unescape

HERE = os.path.dirname(os.path.abspath(__file__))
PDF_DIR = os.path.join(HERE, 'raw', 'tfda_specs')
MAP_CSV = os.path.join(HERE, 'harvested_links', 'tfda_pdf_map.csv')
LOG_FILE = MAP_CSV + '.log'

# 確保目錄存在
os.makedirs(PDF_DIR, exist_ok=True)
os.makedirs(os.path.dirname(MAP_CSV), exist_ok=True)

# 繞過 SSL 驗證
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# 欄位定義
FIELDS = ['detail_id', 'chinese_name', 'english_name', 'pdf_filename', 'download_url', 'status']

def log(msg):
    t_str = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{t_str}] {msg}"
    print(formatted, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(formatted + '\n')

def clean_filename(name):
    # 移除非法檔名半角字元
    name = re.sub(r'[\\/*?:"<>|]', '_', name)
    return name.strip()

def main():
    log("=== 啟動步驟 1c: TFDA 規格標準 PDF 自動採集 ===")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7'
    }
    
    with open(MAP_CSV, 'w', encoding='utf-8', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=FIELDS)
        writer.writeheader()
        
        success_count = 0
        skipped_count = 0
        
        # 迴圈輪詢 TFDA 資料庫 (預估範圍為 1 到 820)
        for detail_id in range(1, 825):
            url = f"https://consumer.fda.gov.tw/Law/FoodAdditivesListDetail.aspx?nodeID=521&id={detail_id}"
            log(f"🚀 [{detail_id}/824] 正在抓取網頁: {url} ...")
            
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, context=ctx, timeout=12) as response:
                    html = response.read().decode('utf-8')
            except Exception as e:
                log(f"  ❌ 網頁抓取失敗 (ID: {detail_id}): {e}")
                writer.writerow({
                    'detail_id': detail_id,
                    'chinese_name': '',
                    'english_name': '',
                    'pdf_filename': '',
                    'download_url': '',
                    'status': f"FETCH_ERROR: {str(e)}"
                })
                time.sleep(2.0)
                continue
            
            # 1. 抓取添加物的中文名與英文名 (以便對照)
            # 在 detail 頁面中，通常在 h3 或 td 中有品名
            zh_name = ""
            en_name = ""
            
            zh_match = re.search(r'id="ctl00_MainContent_lblChName"[^>]*>([^<]*)', html)
            if zh_match:
                zh_name = zh_match.group(1).strip()
            
            en_match = re.search(r'id="ctl00_MainContent_lblEnName"[^>]*>([^<]*)', html)
            if en_match:
                en_name = en_match.group(1).strip()
                
            # HTML 實體還原 (e.g. &#24049; -> 己)
            zh_name = unescape(zh_name)
            en_name = unescape(en_name)
            
            # 2. 搜尋 PDF 下載連結與 title
            pdf_match = re.search(r'href=["\'](/uc/GetFile\.ashx\?type=foodadditiveslist&id=[^"\']+)["\']', html)
            
            if pdf_match:
                rel_url = pdf_match.group(1)
                download_url = "https://consumer.fda.gov.tw" + rel_url
                
                # 抓取檔名 title
                title_match = re.search(r'title=["\']([^"\']+\.pdf)', html)
                if title_match:
                    pdf_filename = unescape(title_match.group(1)).replace("(另開新視窗)", "").strip()
                else:
                    pdf_filename = f"{detail_id:03d}_{zh_name or 'unnamed'}.pdf"
                
                pdf_filename = clean_filename(pdf_filename)
                pdf_path = os.path.join(PDF_DIR, pdf_filename)
                
                log(f"  ✅ 尋獲規格書 PDF: '{pdf_filename}'")
                log(f"  ↳ 開始下載: {download_url} ...")
                
                # 3. 執行下載
                try:
                    req_dl = urllib.request.Request(download_url, headers=headers)
                    with urllib.request.urlopen(req_dl, context=ctx, timeout=20) as dl_resp:
                        pdf_data = dl_resp.read()
                    
                    with open(pdf_path, 'wb') as pdf_file:
                        pdf_file.write(pdf_data)
                    
                    log(f"  💾 下載成功，存檔至: raw/tfda_specs/{pdf_filename}")
                    status = "DOWNLOADED"
                    success_count += 1
                except Exception as e:
                    log(f"  ⚠️ 下載 PDF 失敗 (ID: {detail_id}): {e}")
                    status = f"DOWNLOAD_ERROR: {str(e)}"
                    pdf_filename = ""
            else:
                log("  ℹ️ 該細節頁面無規格書 PDF 連結 (此為正常現象，跳過)。")
                pdf_filename = ""
                download_url = ""
                status = "NO_PDF_SPEC"
                skipped_count += 1
                
            # 4. 寫入 CSV 對照表
            writer.writerow({
                'detail_id': detail_id,
                'chinese_name': zh_name,
                'english_name': en_name,
                'pdf_filename': pdf_filename,
                'download_url': download_url,
                'status': status
            })
            csvfile.flush()
            
            # 🔒 友善限流延遲：每次請求後休眠 0.3s - 0.6s
            time.sleep(random.uniform(0.3, 0.6))
            
    log("=== TFDA PDF 採集任務執行完畢 ===")
    log(f"總成功下載: {success_count} 筆，無規格書跳過: {skipped_count} 筆，清單已儲存於 {MAP_CSV}")

if __name__ == '__main__':
    main()
