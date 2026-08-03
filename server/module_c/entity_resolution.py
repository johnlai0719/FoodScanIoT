"""
Module C — Stage 0：廠商實體解析

v1 定案：精確比對 + 失敗即中止。不使用 record linkage（四家廠商規模不需要，
且兩邊無共用識別碼）。

設計原則（依重要性排序）：
1. 寧可解析失敗，不可解析錯誤。把 A 公司的違規掛到 B 公司的商品上，是整條管線
   唯一可能造成商譽糾紛的失誤。
2. 解析失敗 → 不執行任何搜尋（回傳 None，呼叫端必須中止）。不做模糊猜測。
3. 文件若同時提及本廠商與其易混淆的兄弟法人（如統一企業 vs 統一超商），
   標記為 ambiguous，交由人工審核判斷違規究竟屬於誰——不自動決定。
"""
import json
import os
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_MANUFACTURERS_PATH = os.path.join(_HERE, "manufacturers.json")


@dataclass
class ManufacturerHit:
    """一次成功的廠商解析結果。"""
    producer_id: int
    canonical_name: str
    matched_alias: str
    # 文件中同時出現的「易混淆兄弟法人」名稱。非空即代表此文件有歸屬歧義，
    # 人工審核時必須確認違規主體究竟是誰。
    ambiguous_with: list[str] = field(default_factory=list)

    @property
    def is_ambiguous(self) -> bool:
        return len(self.ambiguous_with) > 0


def _load_manufacturers() -> list[dict]:
    with open(_MANUFACTURERS_PATH, encoding="utf-8") as f:
        return json.load(f)["manufacturers"]


_MANUFACTURERS = _load_manufacturers()


def resolve(text: str) -> list[ManufacturerHit]:
    """
    從文件內容解析出所有命中的 canonical 廠商。

    比對方式為精確子字串比對（非模糊比對、非相似度）。aliases 已刻意設計為
    不會誤觸兄弟法人的精確名稱——例如「聯華」不列為 alias（會誤中聯華電子），
    必須出現「聯華食品」才算命中。

    回傳空 list 代表解析失敗，呼叫端必須中止，不得繼續。
    """
    if not text:
        return []

    hits: list[ManufacturerHit] = []

    for m in _MANUFACTURERS:
        matched_alias = None
        for alias in m["aliases"]:
            if alias in text:
                matched_alias = alias
                break

        if matched_alias is None:
            continue

        # 檢查文件是否同時提及易混淆的兄弟法人 → 標記歧義供人工判斷
        ambiguous_with = [ex for ex in m["exclude"] if ex in text]

        hits.append(
            ManufacturerHit(
                producer_id=m["producer_id"],
                canonical_name=m["canonical_name"],
                matched_alias=matched_alias,
                ambiguous_with=ambiguous_with,
            )
        )

    return hits


def resolve_or_abort(text: str) -> list[ManufacturerHit] | None:
    """
    Stage 0 的正式入口。解析失敗回傳 None，呼叫端必須中止整條管線。

    「失敗即中止」是刻意的：無法確定違規屬於哪家公司時，正確的行為是什麼都不做，
    而不是猜一個最像的。
    """
    hits = resolve(text)
    return hits if hits else None


def get_manufacturer(producer_id: int) -> dict | None:
    """依 producer_id 取得 canonical 廠商定義。"""
    for m in _MANUFACTURERS:
        if m["producer_id"] == producer_id:
            return m
    return None


def all_manufacturers() -> list[dict]:
    return list(_MANUFACTURERS)
