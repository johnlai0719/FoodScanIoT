"""資料庫裡出現的每一種族群寫法，都必須是「已收錄」或「明文決定丟棄」。

`GROUP_ZH_TO_EN` 查不到的中文族群會被 `continue` **靜默丟掉**，不報錯、
不寫 log。2026-09-13 就是這樣發現 `幼童與兒童` 漏收：`L-麩酸` 那筆風險
從資料建好以來一次都沒觸發過，而沒有任何訊號指出這件事。

`test_group_vocabulary.py` 守的是**值域**（七個英文碼，App 與 Cloud 一致）；
這一支守的是**定義域**——新的中文寫法進到資料裡時，逼人做一次決定。

⚠ 兩件事都不是錯，所以測試不能要求「全部收錄」：
  - 收進來（對應到七個碼之一）
  - 刻意丟棄（例如「高血壓患者」——高血壓走營養素閾值，不走 groupRisks，
    CLAUDE.md 的「已知陷阱」記過）
未列入下面兩張表的新寫法才會紅。

資料來源是 `server/seed_data/reference_seed.sql`，不是資料庫——契約測試
一律純函式、不連 DB（CLAUDE.md 的設計原則），而 Fog 的本機降階本來就靠
這份 seed 跑同一套比對。
"""
import json
import re
from pathlib import Path

import pytest

from module_a.ingredient_matching import GROUP_ZH_TO_EN, match_ingredients
from seed_db import RealDictLikeCursor, load_additives_db

SEED = Path(__file__).resolve().parents[2] / "server" / "seed_data" / "reference_seed.sql"

# 出現在資料裡、但**刻意不對應**到任何英文碼的族群。
# 每一項都要有理由——放進來等於宣告「這個族群的風險不會傳到 App」。
DELIBERATELY_UNMAPPED = {
    # 走營養素閾值（鈉 / 糖 / 飽和脂肪），不走 groupRisks。
    # 見 APP/src/utils/personalization.ts 的 NUTRITION_RULES。
    "高血壓患者",
    # 以下四種：App 端沒有對應的使用者設定選項，收進來也沒有人會勾。
    # 要支援的話得先在個人健康設定加選項，不只是加一行對照。
    "血鐵沉著症患者",
    "腸躁症患者",
    "威爾氏症患者",
    "肉桂或秘魯香脂過敏者",
}


def _groups_in_seed() -> set:
    """seed 是 SQL，中文以 \\uXXXX 逸出，所以不能直接用中文字比對。"""
    src = SEED.read_text(encoding="utf-8")
    found = set()
    for raw in re.findall(r'"group"\s*:\s*"([^"]+)"', src):
        # 把 \uXXXX 解回中文；JSON 解碼是最短路徑且不會誤判
        try:
            found.add(json.loads('"%s"' % raw))
        except ValueError:
            found.add(raw)
    return found


def test_seed_file_is_present_and_has_group_risks():
    """seed 讀不到或抓不到族群時，下面兩條會空跑而**全部通過**——
    這種「測試在，但什麼都沒驗」是最貴的假安全感。"""
    assert SEED.exists(), "找不到 %s" % SEED
    groups = _groups_in_seed()
    assert len(groups) >= 10, "只在 seed 裡找到 %d 種族群寫法，解析八成壞了" % len(groups)


@pytest.mark.parametrize("zh", sorted(_groups_in_seed()))
def test_every_group_in_data_is_decided(zh):
    """收錄或明文丟棄，二擇一。新寫法出現時這裡會紅。"""
    assert zh in GROUP_ZH_TO_EN or zh in DELIBERATELY_UNMAPPED, (
        "資料裡出現未決定的族群寫法「%s」。它現在會被 ingredient_matching.py "
        "靜默丟掉、不報錯。請二擇一：加進 GROUP_ZH_TO_EN（若 App 有對應的設定"
        "選項），或加進本檔的 DELIBERATELY_UNMAPPED 並寫明理由。" % zh)


def test_unmapped_list_has_no_stale_entries():
    """刻意丟棄的清單若同時也收進了對照表，兩處就互相矛盾了。"""
    both = DELIBERATELY_UNMAPPED & set(GROUP_ZH_TO_EN)
    assert not both, "這些族群同時出現在 GROUP_ZH_TO_EN 與 DELIBERATELY_UNMAPPED：%s" % sorted(both)


def test_child_variants_all_map_to_child():
    """幼童的寫法特別多（六種），漏一種就少一批警示，而且沒有任何訊號。"""
    for zh in ("嬰幼兒", "兒童", "兒童及青少年", "六個月以下嬰兒",
               "一歲以下嬰幼兒", "幼童與兒童"):
        assert GROUP_ZH_TO_EN.get(zh) == "child", "%s 沒有對應到 child" % zh


# ── 出處必須跟著風險一起送出去 ──────────────────────────────────────────────
@pytest.fixture(scope="module")
def caffeine_risk():
    """跑**真的**比對器，不是手捏的 fixture。

    `build_response()` 只是把 chemical 原樣帶過，groupRisks 是
    `module_a.match_ingredients()` 組的——在那邊斷言才驗得到真實行為。
    咖啡因是庫內少數帶完整出處的孕婦風險（WHO 2020）。
    """
    cur = RealDictLikeCursor(load_additives_db())
    # 回傳是 dict，添加物在 "chemical" 底下（原料在 "basic"／"basic_detail"）。
    result = match_ingredients(["咖啡因"], None, cur, None)
    for it in result["chemical"]:
        for r in (it.get("groupRisks") or []):
            if r.get("group") == "pregnant":
                return r
    pytest.skip("seed 裡沒有咖啡因的孕婦風險")


def test_group_risk_carries_its_source(caffeine_risk):
    """風險說明必須帶出處。

    資料庫裡一直存著 WHO／EFSA／JECFA 的連結與原文引述，2026-09-13 之前
    組裝時沒帶出去——畫面講得出理由、講不出依據。這與第 0 條原則
    「輸出的每個字都要有來源」是同一件事：有來源卻不呈現，使用者無從分辨
    這句話是查來的還是編的。

    2026-09-22 更新：`sourceTitle` 與 `sourceYear` 已**刻意不再送出**。
    庫內同一個網址（PMC4017440）掛了 8 種不同標題、26 條記錄，其中有明顯
    對不上的；年份格式也混，21 條沒有標題。錯誤的 metadata 比缺 metadata
    更糟——它看起來正式，反而讓人誤信。真正可追溯的是網址與原文引述，
    改成檢查那兩者（見 test_evidence_vocabulary.py）。欄位仍留在資料庫。
    """
    for key in ("sourceUrl", "sourceQuote", "evidenceStatus", "reviewedByHuman"):
        assert key in caffeine_risk, "groupRisks 少了 %s" % key
    assert caffeine_risk["sourceUrl"].startswith("http"), \
        "咖啡因的孕婦風險在 seed 裡有 WHO 的連結，卻沒帶出來"


def test_reviewed_flag_is_always_a_bool(caffeine_risk):
    """缺席或 None 時 App 不會標「未經人工複核」，模型整理的說明就會
    看起來像已審定的結論。所以這個鍵必須永遠存在且是布林。"""
    assert isinstance(caffeine_risk["reviewedByHuman"], bool)


def test_reason_is_not_just_the_group_name(caffeine_risk):
    """`reason` 的退路是 `ai_reasoning` → `source_quote` → 族群名。
    退到最後一層代表那筆根本沒有說明，畫面上會顯示「孕婦」兩個字當理由。"""
    assert caffeine_risk["reason"] not in ("孕婦", ""), \
        "reason 退到了族群名，表示 ai_reasoning 與 source_quote 都是空的"
