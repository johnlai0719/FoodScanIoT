"""測試者上傳的樣本蒐集：標頭要一路轉發，圖片要在閘門之前就存下來。

**為什麼需要這道**：這條鏈有四層（App → Fog Node → Fog Python → Cloud），
任何一層漏掉 `X-Tester-Id` 都不會有錯誤訊息，只會安靜地收不到樣本——而正式
路徑正是走 Fog，直連只是對照用。Fog 的 `cloud_headers()` 先前是**重新建一個
dict**、不是轉發收到的標頭，漏掉這件事整個功能就只有直連能用。

另外釘住兩個容易被「優化」掉的決定：

1. **存圖在相關性閘門之前。** 先前是通過閘門才存（理由是「避免無關照片佔用
   硬碟」），於是被判非食品標籤或無實質內容的照片全部丟掉。那正是最該收的
   一批——能通過閘門的照片代表管線已經讀得動它了。
2. **測試者的辨識結果不寫進 products／producers。** 辨識結果會隨讀取器版本
   變動，寫進共享產品庫之後就分不出哪些是實驗殘留。

這個檔案用原始碼比對而不是跑起服務，理由同 test_analyze_auth.py：契約測試要
能在沒有資料庫、沒有 API 金鑰的 CI 上直接跑。
"""
import os
import re

import pytest

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
CLOUD = os.path.join(ROOT, 'server', 'main.py')
MODELS = os.path.join(ROOT, 'server', 'models.py')
FOG_PY = os.path.join(ROOT, 'fog', 'main.py')
FOG_NODE = os.path.join(ROOT, 'fog', 'server.ts')
FOG_HANDLER = os.path.join(ROOT, 'fog', 'queryHandler.ts')
APP_ENDPOINTS = os.path.join(ROOT, 'APP', 'src', 'constants', 'endpoints.ts')
APP_SCREEN = os.path.join(ROOT, 'APP', 'src', 'screens', 'HomeScreen.tsx')
COMPOSE = os.path.join(ROOT, 'docker-compose.yml')

HEADER = 'X-Tester-Id'


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


@pytest.mark.parametrize('path', [CLOUD, FOG_PY, FOG_NODE, FOG_HANDLER, APP_SCREEN])
def test_每一層都用同一個標頭名(path):
    # 大小寫在 HTTP 上不敏感，但四層寫法不一致時，用 grep 找這條鏈的人會漏掉一層。
    assert HEADER in read(path), f'{os.path.basename(path)} 沒有提到 {HEADER}'


def test_fog_轉發標頭而不是自己重建():
    src = read(FOG_PY)
    assert 'def cloud_headers(tester_id' in src, 'cloud_headers 必須收下 tester_id'
    assert re.search(r'if tester_id:\s*\n\s*h\["X-Tester-Id"\] = tester_id', src), \
        'cloud_headers 必須把 tester_id 放進送往 Cloud 的標頭'
    # 呼叫點若沒把收到的標頭傳進去，上面兩條都會通過但實際上永遠是 None
    assert 'cloud_headers()' not in src, \
        'cloud_headers() 的呼叫點必須帶上 request 收到的 X-Tester-Id'


def test_存圖在閘門之前():
    src = read(CLOUD)
    save_at = src.index('_save_scan_images(label_images')
    gate_return = src.index('"status": "rejected"')
    assert save_at < gate_return, \
        '存圖與建檔必須排在閘門的 early return 之前，否則沒通過的照片會被丟掉'


def test_閘門結果要記進紀錄而不只是擋下來():
    src = read(CLOUD)
    assert 'gate_passed=scan_ok' in src, 'scan_uploads 要記下當下的閘門判定'
    assert 'gate_reason=None if scan_ok else scan_reason' in src, \
        '沒通過的原因要一起記，否則事後分不出是哪一類失敗'


def test_測試者不寫入共享產品庫():
    src = read(CLOUD)
    assert 'skip_shared_writes = is_test_mode or bool(tester_id)' in src
    # 三處共享寫入（producers、products、食安監控背景任務）都要吃這個閘
    assert src.count('if not skip_shared_writes:') == 3, \
        '共享資料庫的寫入點數量變了，請確認新增的那處是否也該跳過'
    assert 'if not is_test_mode:' not in src, \
        '還有寫入點只看 is_test_mode，測試者的資料會漏進共享庫'


def test_紀錄表存得下事後篩選需要的欄位():
    src = read(MODELS)
    assert '__tablename__ = "scan_uploads"' in src
    for col in ('tester_id', 'sha256', 'gate_passed', 'gate_reason',
                'vision_backend', 'recognized', 'request_id'):
        assert re.search(rf'^\s*{col} = Column\(', src, re.M), f'scan_uploads 缺少 {col}'


def test_上傳目錄要掛出來否則重建就沒了():
    # 這是這次最容易回頭踩的一條：沒有 volume 時功能「看起來正常」，
    # 圖片也真的寫進去了，只是活到下一次 docker compose up --build 為止。
    compose = read(COMPOSE)
    assert './uploads:/app/uploads' in compose, \
        'cloud-server 必須把 uploads/ 掛成 volume，否則上傳的圖片不會存活'


def test_app_的測試者代號來自環境變數():
    src = read(APP_ENDPOINTS)
    assert 'process.env.EXPO_PUBLIC_TESTER_ID' in src
    # 必須是靜態存取：Expo 在打包時做字面替換，解構或用變數取鍵都拿不到值
    assert 'process.env.EXPO_PUBLIC_TESTER_ID' in read(APP_ENDPOINTS)
    example = read(os.path.join(ROOT, 'APP', '.env.example'))
    assert 'EXPO_PUBLIC_TESTER_ID' in example, '.env.example 要列出這個變數'
