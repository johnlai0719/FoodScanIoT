"""`/api/analyze` 的共享密鑰：Fog 送出的標頭與 Cloud 驗證的標頭必須一致。

**為什麼需要這道**：`/api/analyze` 會呼叫計費的 Gemini API。
Tailscale 是私有網路，只有 tailnet 成員連得到，所以先前沒有驗證；
**Cloudflare Tunnel 給的是公開 HTTPS 網址**，任何人都能打。

⚠ **未設定 `API_SHARED_SECRET` 時放行**，理由是不能讓既有的 Tailscale 佈署
一升級就全斷。但「環境變數沒設就靜默走寬鬆路徑」正是本專案踩過九次的坑，
所以啟動時會印警告、`/health` 會回報 `analyze_auth: "disabled"`。
**這個測試把那兩個可見性保證也鎖住。**

⚠ 密鑰只存在於 Fog。把它嵌進手機 App 等於公開它——正式路徑是
App → Fog → Cloud，App 不需要持有。
"""
import os
import re

import pytest

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
CLOUD = os.path.join(ROOT, 'server', 'main.py')
FOG = os.path.join(ROOT, 'fog', 'main.py')
ENV_EXAMPLE = os.path.join(ROOT, 'server', '.env.example')

HEADER = 'X-API-Key'
ENV_VAR = 'API_SHARED_SECRET'


@pytest.fixture(scope='module')
def cloud_src():
    return open(CLOUD, encoding='utf-8').read()


@pytest.fixture(scope='module')
def fog_src():
    return open(FOG, encoding='utf-8').read()


def test_both_layers_use_the_same_header(cloud_src, fog_src):
    """標頭名稱在兩層各寫一次——不一致的話請求永遠會被擋，而且很難查。"""
    assert HEADER in cloud_src, 'Cloud 沒有引用 %s' % HEADER
    assert HEADER in fog_src, 'Fog 沒有送出 %s' % HEADER


def test_both_layers_read_the_same_env_var(cloud_src, fog_src):
    assert ENV_VAR in cloud_src
    assert ENV_VAR in fog_src


def test_analyze_endpoint_is_guarded(cloud_src):
    """`/api/analyze` 必須掛上驗證依賴。"""
    m = re.search(r'@app\.post\("/api/analyze"([^)]*)\)', cloud_src)
    assert m, '找不到 /api/analyze 的路由宣告'
    assert 'require_api_key' in m.group(1), \
        '/api/analyze 沒有掛 require_api_key——公開端點會無條件呼叫計費的 Gemini API'


def test_comparison_is_constant_time(cloud_src):
    """用 `secrets.compare_digest`，不要用 `==`——字串比較會因提前返回而洩漏前綴。"""
    assert 'compare_digest' in cloud_src


def test_health_reports_whether_auth_is_on(cloud_src):
    """未設定時放行是刻意的，但必須**看得見**。

    這是本專案第十次面對「沒設就靜默走寬鬆路徑」，前九次的教訓是
    ——靜默的預設值會一路撐到有人發現數字不對為止。
    """
    assert 'analyze_auth' in cloud_src


def test_startup_warns_when_unprotected(cloud_src):
    assert re.search(r'WARNING.*API_SHARED_SECRET|API_SHARED_SECRET.*WARNING',
                     cloud_src, re.S | re.I) or \
        ('未設定 API_SHARED_SECRET' in cloud_src)


def test_env_example_documents_it():
    """組員照 .env.example 建環境，沒寫進去等於沒有這個選項。"""
    assert ENV_VAR in open(ENV_EXAMPLE, encoding='utf-8').read()


def test_fog_only_sends_the_key_when_configured(fog_src):
    """兩邊都沒設定時，行為要與加入這道之前完全相同。"""
    m = re.search(r'def cloud_headers\(\).*?return h', fog_src, re.S)
    assert m, '找不到 cloud_headers()'
    body = m.group(0)
    assert 'if API_SHARED_SECRET' in body, '未設定時不應送出空的金鑰標頭'


def test_key_is_not_shipped_to_the_app():
    """App 不得持有密鑰——嵌進手機 App 等於公開它。"""
    app_dir = os.path.join(ROOT, 'APP', 'src')
    hits = []
    for root, _, files in os.walk(app_dir):
        for f in files:
            if not f.endswith(('.ts', '.tsx')):
                continue
            p = os.path.join(root, f)
            if ENV_VAR in open(p, encoding='utf-8', errors='replace').read():
                hits.append(os.path.relpath(p, ROOT))
    assert not hits, 'App 端出現了 %s：%s' % (ENV_VAR, hits)
