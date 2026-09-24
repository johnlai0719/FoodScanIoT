"""釘住族群風險詞彙在 Cloud 與 App 之間的一致性。

這個檔案要擋下的真實事故（2026-08-04 修復）：
  Cloud 送出的 groupRisks[].group 是英文碼，Fog 卻先把使用者條件轉成中文再比對，
  兩邊永遠對不上，導致六種族群的個人化警告從未觸發。同一份詞彙當時散在四個地方
  各自維護，沒有任何機制會指出它們已經不一致。

另一個仍然存在的陷阱：GROUP_ZH_TO_EN 的值域**不含 hypertension／diabetes**。
資料庫裡的「高血壓患者」不在轉換表內，組裝回應時會被靜默丟棄。所以高血壓與糖尿病的
個人化只能靠營養素閾值。下方 test_no_chronic_disease_codes 就是釘住這個事實——
哪天有人把它們加進轉換表，App 端的比對邏輯也必須同步調整。
"""
import re
from pathlib import Path

import pytest

from module_a.ingredient_matching import CONCERN_TO_LEVEL, GROUP_ZH_TO_EN

APP_VOCAB_FILE = (
    Path(__file__).resolve().parents[3]
    / "APP" / "src" / "constants" / "groupVocabulary.ts"
)

# Cloud 能產出的全部族群碼。這是 groupRisks[].group 的完整值域。
EXPECTED_GROUP_CODES = {
    "pregnant", "child", "kidney_disease",
    "asthma", "aspirin_allergy", "pku", "allergy",
}


def _parse_ts_string_array(source: str, const_name: str) -> set[str]:
    """從 TS 原始碼取出 `export const NAME = [...] as const;` 的字串成員。

    刻意用 Python 讀 TS 檔而不是在 jest 那側斷言：App 的 Metro bundler 以 APP/ 為
    projectRoot，跨出去 import 共用檔會在打包時失敗；讓 Python 讀檔可以完全避開
    bundler 邊界，並把跨層檢查集中在同一個 runner。
    """
    match = re.search(
        rf"export\s+const\s+{const_name}\s*=\s*\[(.*?)\]\s*as\s+const",
        source,
        re.DOTALL,
    )
    assert match, f"在 {APP_VOCAB_FILE.name} 找不到 export const {const_name} = [...] as const"
    return set(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))


@pytest.fixture(scope="module")
def app_source() -> str:
    assert APP_VOCAB_FILE.exists(), f"找不到 {APP_VOCAB_FILE}"
    return APP_VOCAB_FILE.read_text(encoding="utf-8")


def test_cloud_group_codomain_is_frozen():
    """Cloud 的值域不得在無人察覺的情況下增減。"""
    assert set(GROUP_ZH_TO_EN.values()) == EXPECTED_GROUP_CODES


def test_app_and_cloud_vocabularies_match(app_source):
    """核心跨層斷言：App 的 CLOUD_GROUP_CODES 必須與 Cloud 的值域完全相同。"""
    app_codes = _parse_ts_string_array(app_source, "CLOUD_GROUP_CODES")
    cloud_codes = set(GROUP_ZH_TO_EN.values())

    assert app_codes == cloud_codes, (
        "App 與 Cloud 的族群詞彙已不一致。\n"
        f"  只有 Cloud 有（App 會顯示原始英文碼給使用者）：{sorted(cloud_codes - app_codes)}\n"
        f"  只有 App 有（永遠不會被觸發的死選項）：{sorted(app_codes - cloud_codes)}"
    )


def test_app_labels_cover_every_code(app_source):
    """每個族群碼都要有中文標籤，否則使用者會看到原始英文碼。"""
    codes = _parse_ts_string_array(app_source, "CLOUD_GROUP_CODES")
    labels_block = re.search(
        r"GROUP_LABELS_ZH:\s*Record<CloudGroupCode,\s*string>\s*=\s*\{(.*?)\};",
        app_source,
        re.DOTALL,
    )
    assert labels_block, "找不到 GROUP_LABELS_ZH"
    labelled = set(re.findall(r"(\w+)\s*:", labels_block.group(1)))
    assert codes <= labelled, f"缺少中文標籤：{sorted(codes - labelled)}"


def test_no_chronic_disease_codes():
    """釘住「高血壓／糖尿病不在 groupRisks 值域內」這個事實。

    若這條測試轉紅，代表有人把慢性病加進了 Cloud 的轉換表——這是好事，但
    APP/src/utils/personalization.ts 需要同步調整，否則會同時從 groupRisks 與
    營養素閾值兩條路徑重複警示同一件事。
    """
    codomain = set(GROUP_ZH_TO_EN.values())
    assert "hypertension" not in codomain
    assert "diabetes" not in codomain


def test_concern_to_level_mapping_is_frozen():
    """App 依 riskLevel 判定嚴重度；這組映射改動會直接改變前端的警示行為。"""
    assert CONCERN_TO_LEVEL == {"caution": 1, "avoid": 3, "danger": 5}
