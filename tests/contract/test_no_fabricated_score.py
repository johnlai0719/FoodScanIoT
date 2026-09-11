"""`normalize_result()` 不得替上游的錯誤憑空補上分數。

**為什麼要凍結這件事**：`fog/transforms.py` 有兩處會注入 75——
第 2 段的 `final_health_diagnosis` 骨架，以及第 3 段的 `or ... 75`。
對「Cloud 算完但缺欄位」那是合理的預設，對「Cloud 根本沒算」就是
**把錯誤呈現成一個健康分數**。2026-09-12 實測，三種錯誤 payload
全部長出 `health_score=75`：

    {'detail': 'Internal Server Error'}     FastAPI 的 500
    {'errors': [{'code': 1033}]}            Cloudflare Tunnel 斷線
    {'status': 'error', 'message': '...'}   Cloud 自己回的錯誤

而 `CLAUDE.md` 訂的判準是「**判定結果是否有效請看有沒有 health_score**」
——上面三種都會通過那個判準，App 會把沒算過的 75 分渲染給使用者。

這與 2026-08-05 把食安事件從「未查詢卻顯示為安全」改掉是同一條原則：
**對食安 App 而言，把「沒算到」呈現成一個數字有風險。**

⚠ 這幾個案例會隨著 Cloudflare Tunnel 上線變得更常見：Tunnel 斷線時
Cloudflare 的邊緣仍然活著，Fog 會**成功完成一次 HTTP 對話**並拿回錯誤，
而不是連線失敗。見 `專案管理/工程待辦.md` 的 A2／A3。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'fog'))
from transforms import (normalize_result, looks_like_analysis,  # noqa: E402
                        build_upstream_error_response)

# 上游沒有回出分析結果的三種真實形狀
NOT_ANALYSIS = [
    pytest.param({'detail': 'Internal Server Error'}, id='fastapi_500'),
    pytest.param({'errors': [{'code': 1033}]}, id='cloudflare_tunnel_down'),
    pytest.param({'status': 'error', 'message': '服務暫停'}, id='cloud_error'),
    pytest.param({'status': 'not_found'}, id='cloud_not_found'),
    pytest.param({'status': 'rejected', 'message': '非食品標示'}, id='cloud_rejected'),
    pytest.param({}, id='empty'),
]


@pytest.mark.parametrize('payload', NOT_ANALYSIS)
def test_no_score_for_non_analysis(payload):
    """最重要的一條：錯誤不得帶著 health_score 回去。"""
    out = normalize_result(dict(payload))
    assert 'health_score' not in out or out['health_score'] is None
    assert out.get('status') != 'success'


@pytest.mark.parametrize('payload', NOT_ANALYSIS)
def test_error_carries_a_message_for_the_app(payload):
    """App 在沒有分數時會顯示 message，所以一定要有一句話可以顯示。"""
    out = normalize_result(dict(payload))
    assert isinstance(out.get('message'), str) and out['message'].strip()


@pytest.mark.parametrize('payload', NOT_ANALYSIS)
def test_no_fabricated_diagnosis_block(payload):
    """也不能靠 final_health_diagnosis 的骨架把 75 分夾帶回去。"""
    out = normalize_result(dict(payload))
    diag = out.get('final_health_diagnosis') or {}
    assert diag.get('score') is None


def test_upstream_payload_is_kept_for_debugging():
    out = normalize_result({'status': 'error', 'message': 'x', 'trace_id': 'abc'})
    assert out.get('upstream', {}).get('trace_id') == 'abc'


# ── 反向：真的分析結果必須照常通過，而且分數不可被替換 ──────────────────
def test_real_result_keeps_its_score():
    out = normalize_result({'status': 'success', 'health_score': 82,
                            'product_info': {'name': '綠茶'}})
    assert out['health_score'] == 82


def test_score_inside_data_is_not_replaced_by_the_default():
    """Cloud 的 `{status, data:{...}}` 形狀會把分數放在 data 裡。

    只看頂層會找不到，於是落到骨架的預設 75——**真的算出來的 82 會被換成
    捏造的 75**。骨架是在第 2 段才注入的，它的 score 一定是 75，
    不能當成上游給的值。
    """
    out = normalize_result({'status': 'success',
                            'data': {'health_score': 82,
                                     'product_info': {'name': '綠茶'}}})
    assert out['health_score'] == 82


def test_flat_result_still_works():
    """扁平格式（只有 name／ingredients_list）仍視為分析結果。"""
    assert looks_like_analysis({'name': '綠茶', 'ingredients_list': ['水']})


def test_predicate_rejects_non_dict():
    assert not looks_like_analysis(None)
    assert not looks_like_analysis('error')


def test_builder_never_emits_a_score():
    """降階與上游錯誤走同一條原則：沒有分數，App 才會顯示 message。"""
    out = build_upstream_error_response({'detail': 'boom'}, status_code=502)
    assert 'health_score' not in out
    assert out['upstream_status'] == 502


# ── 扁平格式的包裝結果必須真的回傳 ──────────────────────────────────────
def test_flat_format_wrapping_is_returned():
    """`normalize_result` 結尾是 `return result`，而扁平路徑把資料包進**新建的**
    `target`。不合併回去的話整段白做——2026-09-12 實測，扁平輸入只回
    name／ingredients_list／health_score／risk_level，`product_info`、
    `nutrition_facts`、`ingredients_detail` 全部遺失。

    真實 Cloud 一定回 `status:success` ＋ 22 個頂層欄位，不走這條路，
    所以這個洞一直沒被發現。
    """
    out = normalize_result({
        'name': '綠茶',
        'ingredients_list': ['水', '茶葉'],
        'nutrition': {'calories': 30},
        'ingredients_detail': [{'name': '水', 'isAdditive': False}],
    })
    assert out['product_info']['name'] == '綠茶'
    assert out['product_info']['ingredients'] == '水,茶葉'
    assert out['nutrition_facts'] == {'calories': 30}
    assert len(out['ingredients_detail']) == 1
    # 而且仍然不得憑空補分數
    assert out.get('health_score') is None


# ── 0 是合法分數，null 不可留著 ────────────────────────────────────────
@pytest.mark.parametrize('score', [0, -3, -15])
def test_zero_and_negative_scores_survive(score):
    """**0 分是固體食品的 A 級邊界**，不是缺值。

    health_score 是 Nutri-Score 的原始值（points_n − points_p），
    `nutriscore_v7.get_grade` 明寫 `score <= 0 → "A"`，負分也存在。
    先前用 `result.get("health_score") or ...`，`0 or X` 取 X，
    於是一個滿分產品的分數被丟掉——修掉「補 75 分」之後，
    後果從「顯示錯的分數」變成「完全沒有分數、App 進錯誤頁」。
    """
    out = normalize_result({'status': 'success', 'health_score': score,
                            'risk_level': 'A', 'product_info': {'name': '綠茶'}})
    assert out['health_score'] == score


def test_null_score_is_removed_not_left_as_null():
    """App 用 `health_score === undefined` 判斷有效性，再用 `?? 100` 取值。

    `null` **通不過第一關**（它不是 undefined）**卻會觸發第二關的預設值**，
    於是顯示一個假的 100 分。所以不能只是「不設定」，還要把既有的 null 拿掉。

    上游若送 `{"health_score": null}`，`looks_like_analysis` 會因為鍵存在
    而判成分析結果，一路走到這裡——這條路徑是真的到得了的。
    """
    out = normalize_result({'health_score': None, 'product_info': {'name': '綠茶'}})
    assert 'health_score' not in out, 'null 會讓 App 顯示假的 100 分'
    assert 'risk_level' not in out
