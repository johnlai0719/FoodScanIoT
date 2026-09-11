"""「什麼時候算 Cloud 不可用」——降階的觸發條件。

**為什麼要凍結這件事**：原本這個判定寫在 FastAPI 的 handler 裡（`fog/main.py`），
測不了；而 `CLAUDE.md` 的慣例是「契約測試一律是純函式測試」。
抽成 `transforms.should_degrade()` 之後才鎖得住。

⚠ **Cloudflare Tunnel 會改變「連不上」的形狀。** 直連時 Cloud 掛掉就是 TCP
連不上、會拋例外；但隧道在前面時，**Cloudflare 的邊緣永遠活著**，Fog 會
**成功完成一次 HTTP 對話**並拿回錯誤狀態碼：

    Cloud 服務掛了（cloudflared 還在跑）   502
    cloudflared 掛了／斷線                 530（內含 1033「隧道未連線」）
    Cloudflare 與來源站之間出問題           520–527

四種失效裡只有「Pi 完全沒網路」會拋例外。**只看例外的話，切到 Tunnel 之後
降階會在最常見的三種情境下失效**——而那三種正是自己的 Cloud 重開、
cloudflared 重啟、token 過期。

⚠ **4xx 不算不可用。** 400／401／422 是我們送錯了，降階或重試都沒有意義。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fog'))
from transforms import should_degrade, CloudUnavailable, CLOUD_DOWN_CODES  # noqa: E402


@pytest.mark.parametrize('code', sorted(CLOUD_DOWN_CODES))
def test_upstream_down_codes_degrade(code):
    assert should_degrade(status_code=code) is True


@pytest.mark.parametrize('code,why', [
    (502, 'origin unreachable：cloudflared 連不到我們的服務'),
    (530, '內含 1033，隧道未連線——cloudflared 掛了'),
    (524, 'Cloudflare 的 100 秒硬上限'),
    (521, 'Web server is down'),
])
def test_the_cloudflare_specific_ones(code, why):
    """這幾個是換成 Tunnel 之後才會出現的，列出來讓人知道它們為何在清單裡。"""
    assert should_degrade(status_code=code) is True, why


@pytest.mark.parametrize('code', [200, 201, 204, 301, 400, 401, 403, 404, 422, 429])
def test_ok_and_client_errors_do_not_degrade(code):
    """2xx/3xx 顯然不算；4xx 是我們送錯，降階沒有意義，錯誤要照實回去。"""
    assert should_degrade(status_code=code) is False


def test_connection_failure_degrades():
    """原本唯一會降階的情況：連線失敗。必須繼續成立。"""
    assert should_degrade(exc=OSError('connection refused')) is True
    assert should_degrade(status_code=None, exc=TimeoutError()) is True


def test_exception_wins_over_status_code():
    """兩者都有時以例外為準——拿得到狀態碼卻又拋例外，通常是讀取階段斷掉。"""
    assert should_degrade(status_code=200, exc=OSError()) is True


def test_no_information_does_not_degrade():
    """兩個都沒有就不該猜。"""
    assert should_degrade() is False


def test_cloud_unavailable_is_an_exception():
    """`main.py` 靠 raise 它來走進既有的 except（陳舊快取 → 本機降階）。"""
    assert issubclass(CloudUnavailable, Exception)


def test_main_uses_the_predicate_at_both_call_sites():
    """兩個呼叫 Cloud 的地方都要用同一個判定。

    帶圖那條原本**完全不看狀態碼**（直接 `.json()`，靠解析失敗才意外降階）；
    無圖那條有看 200，但非 200 直接回錯誤、不走退路。兩邊都改成先問
    `should_degrade`。這條測試防止其中一邊被改回去。
    """
    main_py = os.path.join(os.path.dirname(__file__), '..', '..', 'fog', 'main.py')
    src = open(main_py, encoding='utf-8').read()
    assert src.count('should_degrade(status_code=cloud_resp.status_code)') == 2, \
        '兩個呼叫點都要用 should_degrade'
    assert 'raise CloudUnavailable' in src
