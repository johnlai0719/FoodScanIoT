"""添加物的官方類別欄位（`category`）跨層契約。

這一欄 2026-09-14 才加。它的用途是讓 App 在「添加物」子頁做依類別的統計
（防腐劑 3、著色劑 1…）並在每一項旁標出類別徽章。

**為什麼需要契約測試**：資料庫的 `category` 是 json 欄位，形狀在不同批次的
匯入下出現過三種——json 陣列、單一字串、None。若某天又變成字串而 App 仍當
陣列處理，畫面會直接當掉；若變成 None 而 App 沒判空，統計會少算。
這裡把「一律是陣列」釘住，讓形狀回歸時在 CI 就紅，而不是在手機上才發現。
"""
import json
import re
from pathlib import Path

import pytest

import module_a.ingredient_matching as IM
from seed_db import RealDictLikeCursor, load_additives_db

APP = Path(__file__).resolve().parents[2] / "APP" / "src"


@pytest.fixture(scope="module")
def cur():
    return RealDictLikeCursor(load_additives_db())


def _additives(cur, names):
    return IM.match_ingredients(names, None, cur, None)["chemical"]


# ── Cloud 端：欄位存在且形狀固定 ─────────────────────────────────────────────
def test_matched_additive_carries_its_official_category(cur):
    """配到資料庫的添加物必須帶出官方類別。"""
    got = {a["officialName"]: a.get("category") for a in _additives(cur, ["己二烯酸鉀", "檸檬酸鈉"])}
    assert got, "沒有任何添加物配到，這個測試失去意義"
    for name, cats in got.items():
        assert cats, "%s 沒有類別" % name


def test_category_is_always_a_list(cur):
    """一律是 list——App 直接 .map()，不做型別判斷。

    資料庫那一欄可能是 json 陣列或單一字串，收斂在 Cloud 端做完。
    """
    for a in _additives(cur, ["己二烯酸鉀", "檸檬酸鈉", "維生素C", "碳酸鈣"]):
        assert isinstance(a.get("category"), list), \
            "%s 的 category 是 %r" % (a["name"], type(a.get("category")))
        for c in a["category"]:
            assert isinstance(c, str) and c.strip(), "類別不可為空字串"


def test_category_has_no_duplicates(cur):
    """同一項不重複列同一類——徽章會重複畫，統計也會重複計。"""
    for a in _additives(cur, ["己二烯酸鉀", "檸檬酸鈉", "維生素C"]):
        cats = a["category"]
        assert len(cats) == len(set(cats)), "%s 的類別有重複：%r" % (a["name"], cats)


def test_unmatched_additive_gets_empty_list_not_none(cur):
    """比對不到時是空陣列，不是 None。

    `_category_list` 對 match 為 None 一律回 []。少一個 null 判斷
    就少一種當掉的方式——而這一欄在畫面上是直接 .map() 的。
    """
    assert IM._category_list(None) == []
    assert IM._category_list({}) == []
    assert IM._category_list({"category": None}) == []


def test_category_accepts_the_three_shapes_seen_in_imports():
    """json 陣列／單一字串／None 都要收成陣列。

    三種形狀都在實際匯入批次裡出現過。若日後只剩一種，這個測試仍然成立，
    但它記錄了為什麼 Cloud 端要做這層收斂。
    """
    assert IM._category_list({"category": ["防腐劑"]}) == ["防腐劑"]
    assert IM._category_list({"category": "防腐劑"}) == ["防腐劑"]
    assert IM._category_list({"category": ["防腐劑", "防腐劑"]}) == ["防腐劑"]
    assert IM._category_list({"category": None}) == []


# ── App 端：真的有用到，而且用對了 ──────────────────────────────────────────
def _src(*parts):
    """讀原始碼並**去掉註解**。

    這一步是必要的：本專案的註解常引用欄位名與說明文字，直接掃描原始碼會掃到
    自己寫的註解而通過。已經因此誤判過三次。
    """
    text = (APP.joinpath(*parts)).read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    return text


def test_app_type_declares_category_as_string_array():
    src = _src("types.ts")
    assert re.search(r"category\?:\s*string\[\]", src), \
        "types.ts 沒有把 category 宣告成 string[]"


def test_app_renders_a_category_badge_on_each_additive():
    src = _src("components", "IngredientsList.tsx")
    assert "ing.category" in src, "清單元件沒有用到 category"
    assert "categoryBadge" in src, "沒有類別徽章的樣式"


def test_app_computes_a_per_category_breakdown():
    src = _src("screens", "HomeScreen.tsx")
    assert "additiveCategoryStats" in src, "沒有依類別的統計"
    assert "未分類" in src, \
        "統計沒有把「沒配到知識庫」獨立成一格——併進任何一類都是錯的"


def test_app_explains_that_categories_can_overlap():
    """各類相加大於總數，畫面必須講明，否則會被當成加總錯誤。

    一項添加物可以同時屬多類（己二烯酸鉀是防腐劑也是殺菌劑），
    為了讓總和好看而只取第一類，等於憑空抹掉一個身分。
    """
    src = _src("screens", "HomeScreen.tsx")
    assert "相加會大於" in src, "畫面沒有說明各類數字相加會大於添加物總數"
