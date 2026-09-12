"""App 打的 Cloud 埠號必須與 docker-compose 實際發布的一致。

2026-09-13 查到 `HomeScreen.tsx` 寫死 `:3003`，而正式管線是 compose 起的、
發布 `:8000`——直連 Cloud 那條路徑打不到任何東西。**而且沒有任何測試會紅**，
因為兩邊分屬不同語言、不同檔案，誰也不認識誰。

CLAUDE.md 早就寫過「注意埠號有兩套」（`main.py` 單跑是 3003、compose 是 8000），
但那是一句提醒，提醒攔不住。這支把它變成會紅的測試。

與 `test_group_vocabulary.py`／`test_degraded_marker_string.py` 同一個模式：
跨層共用的常數，用讀原始碼字串的方式鎖住。
"""
import os
import re

import pytest

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
ENDPOINTS = os.path.join(ROOT, 'APP', 'src', 'constants', 'endpoints.ts')
COMPOSE = os.path.join(ROOT, 'docker-compose.yml')


def _read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def _app_cloud_port():
    m = re.search(r"CLOUD_URL\s*=\s*['\"]https?://[^/'\"]+:(\d+)", _read(ENDPOINTS))
    assert m, '在 endpoints.ts 找不到 CLOUD_URL 的埠號'
    return int(m.group(1))


def _compose_published_ports():
    """compose 的 `'8000:8000'` 取左邊那個（對外發布的）。"""
    return {int(a) for a, _ in re.findall(r"['\"](\d+):(\d+)['\"]", _read(COMPOSE))}


def test_app_cloud_port_is_actually_published():
    port = _app_cloud_port()
    published = _compose_published_ports()
    assert port in published, (
        'App 的 CLOUD_URL 打 :%d，但 docker-compose 發布的是 %s。'
        '正式管線是用 compose 起的，所以直連 Cloud 那條路徑會打不到。'
        % (port, sorted(published)))


def test_app_has_a_fetch_timeout():
    """沒有逾時的話，網路斷掉時畫面會一直轉（靠平台預設，各家不同）。

    改用 vlcrop 後單次分析要 10 秒起跳，逾時不再是可有可無的細節。
    """
    src = _read(os.path.join(ROOT, 'APP', 'src', 'screens', 'HomeScreen.tsx'))
    assert 'AbortController' in src, 'HomeScreen 的分析請求沒有設逾時'
    assert 'ANALYSIS_TIMEOUT_MS' in src


@pytest.mark.parametrize('name', ['FOG_URL', 'CLOUD_URL', 'ANALYSIS_TIMEOUT_MS'])
def test_endpoints_are_defined_in_one_place(name):
    assert 'export const %s' % name in _read(ENDPOINTS)


def test_no_hardcoded_endpoint_left_in_the_screen():
    """端點不得再出現在畫面程式裡——同一份知識放兩個地方是本專案的舊傷
    （族群詞彙曾散在四處，導致六種族群警告從未觸發）。"""
    src = _read(os.path.join(ROOT, 'APP', 'src', 'screens', 'HomeScreen.tsx'))
    # 註解裡提到 IP 是可以的，這裡只抓「字串字面值形式的 http 端點」
    hits = re.findall(r"['\"]https?://\d+\.\d+\.\d+\.\d+:\d+[^'\"]*['\"]", src)
    assert not hits, 'HomeScreen 裡還有寫死的端點：%s（請移到 constants/endpoints.ts）' % hits


def test_app_timeout_is_longer_than_every_downstream_layer():
    """App 若比下游先放棄，後端寫好的錯誤訊息與降階結果就送不到使用者眼前。

    這正是 `normalize_result` 那次（憑空補 75 分）在防的同一件事的另一面：
    使用者該看到後端真正說了什麼。
    """
    app_ms = int(re.search(r'ANALYSIS_TIMEOUT_MS\s*=\s*([\d_]+)',
                           _read(ENDPOINTS)).group(1).replace('_', ''))
    node_ms = int(re.search(r'CLOUD_TIMEOUT\s*=\s*(\d+)',
                            _read(os.path.join(ROOT, 'fog', 'queryHandler.ts'))).group(1))
    py_s = float(re.search(r'CLOUD_READ_TIMEOUT\s*=\s*float\(os\.getenv\([^,]+,\s*"([\d.]+)"',
                           _read(os.path.join(ROOT, 'fog', 'main.py'))).group(1))
    assert app_ms >= node_ms, 'App %dms 比 Fog Node %dms 短' % (app_ms, node_ms)
    assert node_ms >= py_s * 1000, (
        'Fog Node %dms 比 Fog Python %.0fms 短——Node 會先放棄，'
        'Python 層的降階邏輯根本跑不到。' % (node_ms, py_s * 1000))
