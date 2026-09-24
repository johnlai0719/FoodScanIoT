"""資料庫有這一項但沒有說明時，**不可以拿模型生成的描述頂上**。

添加物知識庫的填寫紀律是「只填有確定性文獻可佐證的部分」——需要毒理或醫學判斷
的欄位一律留空（`iarc_class`、`jecfa_summary`、`medical_caution` 三者覆蓋率都是 0，
`risks` 只有 49/804）。留空是取捨，不是還沒做完。

⚠ **留空的欄位若在呈現時被模型補上，那條紀律就等於沒有。** 使用者看到的仍是
未查證的內容，而且它旁邊還掛著這一項真正的出處連結（`description_sources`
覆蓋率是 804/804），看起來像有根據——比直接留白更糟。

2026-09-22 之前 `match_ingredients()` 的分支順序是：

    if match and match['description']:  ...
    elif ai_desc:                       ← 有這一項但缺說明會掉到這裡
    elif match:                         ← 因此永遠到不了

所以清空一筆說明（例如把測試殘留清掉）會讓那一項改為顯示視覺模型當場產生的
描述。這個檔案守的是分支順序。
"""
import pytest

import module_a.ingredient_matching as IM
from seed_db import RealDictLikeCursor, load_additives_db

AI_DESC = '這段是視覺模型當場生成的說明，不該出現在畫面上'


@pytest.fixture
def conn():
    return load_additives_db()


def _first_named(conn):
    """從種子檔挑一個真的存在的添加物名稱，不硬編特定品項。"""
    cur = conn.cursor()
    cur.execute("SELECT name_zh FROM additives WHERE name_zh IS NOT NULL "
                "AND description IS NOT NULL AND description <> '' LIMIT 1")
    return cur.fetchone()[0]


def _detail(conn, name, vision_data=None):
    # 添加物進 `chemical`，非添加物才進 `basic_detail`（見 match_ingredients 末尾的
    # 回傳字典）。這個檔案測的全是添加物，所以看 chemical。
    result = IM.match_ingredients([name], vision_data, RealDictLikeCursor(conn), None)
    rows = result['chemical'] or result['basic_detail']
    assert rows, f'{name} 既不在 chemical 也不在 basic_detail'
    return rows[0]


def test_有說明時照樣用資料庫的(conn):
    name = _first_named(conn)
    d = _detail(conn, name, {'ingredient_details': {name: {'desc': AI_DESC}}})
    # 有正式說明時模型的描述不該被採用——這是原本就有的行為，一併鎖住
    assert AI_DESC not in d['description']


def test_缺說明時不採用模型描述(conn):
    name = _first_named(conn)
    conn.execute("UPDATE additives SET description = NULL WHERE name_zh = ?", (name,))

    d = _detail(conn, name, {'ingredient_details': {name: {'desc': AI_DESC}}})

    assert AI_DESC not in d['description'], '缺說明時顯示了模型生成的描述'
    assert d['description'] == IM.NO_FIELD_DATA_LABEL
    # 仍要認得這一項在資料庫裡——否則使用者會以為系統沒收錄它
    assert d['inDatabase'] is True


def test_缺說明不得寫成無風險(conn):
    """「沒有紀錄」與「評估後認定無害」意義相反，措辭不可混用。"""
    name = _first_named(conn)
    conn.execute("UPDATE additives SET description = NULL WHERE name_zh = ?", (name,))
    desc = _detail(conn, name)['description']
    for banned in ('無特定風險', '無特定化學危害', '無害', '安全'):
        assert banned not in desc, f'缺說明時不可出現「{banned}」'


def test_缺說明時不套用憑空的攝取建議(conn):
    """原本的退路是 f"此成分為{food_tech_purpose}，建議依個人體質適量攝取。"。

    兩個問題：food_tech_purpose 裝的是食藥署使用範圍及限量原文（含劑量與換行，
    最長 319 字），套進句型會產生一大段不成話的文字；而那句建議本身沒有出處。
    """
    name = _first_named(conn)
    conn.execute("UPDATE additives SET description = NULL WHERE name_zh = ?", (name,))
    desc = _detail(conn, name)['description']
    assert '建議依個人體質適量攝取' not in desc
    assert len(desc) < 40, f'缺說明的退路不該是一大段文字，實際 {len(desc)} 字'


def test_未收錄與未載明是不同的兩句話(conn):
    """未收錄＝系統沒有這一項；未載明＝有這一項但缺這個欄位。不可共用措辭。"""
    assert IM.NOT_IN_DB_LABEL != IM.NO_FIELD_DATA_LABEL
    d = _detail(conn, '這個東西資料庫裡絕對沒有XYZ')
    assert d['inDatabase'] is False
    assert d['description'] != IM.NO_FIELD_DATA_LABEL
