"""
一次性腳本：為使用者提供的廠商清單建立 canonical 實體表草稿。

安全原則（同 manufacturers.json 之設計）：寧可解析失敗，不可解析錯誤。
- aliases 只放「精確、不會誤中其他法人」的名稱。單獨的常用詞（如「泰山」「大成」
  「奇美」「黑松」「乖乖」）一律不放，必須帶公司/產品限定詞。
- 品牌名僅在「明確且唯一對應此公司」時才納入 alias，以提升召回。
- exclude 窮舉「名字很像但不同法人」者，這是防止商譽誤掛的核心。

⚠️ 本檔為草稿。新廠商加入前，exclude 清單請人工再審一次。
"""
import json
import os
from database import SessionLocal
import models

_HERE = os.path.dirname(os.path.abspath(__file__))
_PATH = os.path.join(_HERE, "manufacturers.json")

# (canonical_name, aliases, exclude, exclude_reason)
# aliases 中標「品牌」者為判斷用註記，實際寫入時去除註記
NEW = [
    ("佳格食品",
     ["佳格食品", "佳格", "桂格", "得意的一天", "天地合補", "福樂"],
     ["Quaker Oats", "PepsiCo", "百事"],
     "『桂格』『福樂』為佳格旗下品牌，台灣情境下明確對應佳格，故納入 alias 提升召回；惟全球 Quaker 隸屬百事（PepsiCo），為不同法人，須排除。"),

    ("光泉牧場",
     ["光泉牧場", "光泉食品", "光泉", "午后時光", "純粹喝"],
     ["光泉寺", "光泉禪寺"],
     "『光泉』另有同名寺廟，惟食安情境重疊低，僅列示。"),

    ("旺旺集團",
     ["旺旺集團", "宜蘭食品", "旺旺食品", "旺仔", "浪味仙", "旺旺仙貝"],
     ["旺旺中時", "旺中", "中國時報", "中天電視", "中天新聞", "旺旺友聯產險"],
     "⚠️高混淆：『旺旺』亦為旺旺中時媒體集團（中國時報、中天）之名。故不放單獨『旺旺』為 alias，改用『旺旺集團／宜蘭食品』及明確食品品牌。"),

    ("宏亞食品",
     ["宏亞食品", "宏亞", "77乳加", "新貴派", "巧克力酥片"],
     ["宏亞科技", "宏碁"],
     "『宏亞』尚屬distinctive；排除易混淆之科技業同音/近音者。"),

    ("盛香珍",
     ["盛香珍", "成偉食品", "成偉"],
     [],
     "『盛香珍』『成偉』皆distinctive，暫無明確混淆對象。"),

    ("乖乖",
     ["乖乖股份有限公司", "乖乖食品", "孔雀香酥脆", "孔雀餅乾"],
     [],
     "⚠️高雜訊：『乖乖』為常用詞（乖乖聽話、IT乖乖文化），單獨放會撈回大量無關內容。故不放單獨『乖乖』，改用公司全名與明確產品名；閘門雖能擋部分，此廠商仍屬高雜訊，人工審核負擔較高。"),

    ("愛之味",
     ["愛之味", "牛奶花生", "純濃燕麥", "珍保玉筍"],
     [],
     "『愛之味』distinctive。『甜辣醬』過於通用未納入。"),

    ("維力食品",
     ["維力食品", "維力", "維力炸醬麵", "一度贊"],
     [],
     "『維力』尚distinctive。"),

    ("味丹企業",
     ["味丹企業", "味丹", "味味A", "味味一品"],
     ["味全", "味王", "味全食品", "味王醬油"],
     "⚠️高混淆：味丹／味全／味王為三家不同『味X』食品公司（味丹=味味A、味全=林鳳營、味王=味王醬油），極易互相誤掛，必須互斥。『多喝水』雖為味丹產品但詞太通用，未納入。"),

    ("泰山企業",
     ["泰山企業", "泰山食品", "泰山八寶粥", "泰山仙草蜜"],
     ["泰山區", "新北市泰山", "泰山巖", "泰山明志"],
     "⚠️高混淆：『泰山』為新北市地名（泰山區）及山名。故不放單獨『泰山』，須帶『企業／八寶粥／仙草蜜』。"),

    ("黑松",
     ["黑松沙士", "黑松汽水", "黑松飲料", "韋恩咖啡"],
     ["黑松露", "松露", "黑松盆栽", "日本黑松"],
     "⚠️高混淆：『黑松』亦指黑松露（食材）與黑松樹（松科）。故不放單獨『黑松』，須帶『沙士／汽水』等產品。"),

    ("桂冠實業",
     ["桂冠實業", "桂冠湯圓", "桂冠貢丸", "桂冠火鍋料"],
     ["桂冠詩人", "桂冠出版"],
     "『桂冠』另有『桂冠詩人（poet laureate）』『桂冠出版社』之比喻/機構用法。故傾向帶產品名。"),

    ("奇美食品",
     ["奇美食品", "奇美包子", "奇美鮮肉包"],
     ["奇美電子", "奇美實業", "奇美醫院", "奇美博物館", "群創", "群創光電"],
     "⚠️高混淆：『奇美』為大集團，含奇美電子（→群創光電）、奇美實業（ABS塑膠）、奇美醫院、奇美博物館，皆不同法人。故不放單獨『奇美』，須帶『食品／包子』。"),

    ("大成長城",
     ["大成長城", "大成食品", "大成長城企業", "大成雞肉"],
     ["大成鋼", "大成不鏽鋼", "大成中學", "大成國小"],
     "⚠️高混淆：『大成』極常見，含大成鋼（不鏽鋼）、多所大成中小學。故不放單獨『大成』，須帶『長城／食品／雞肉』。"),

    ("卜蜂企業",
     ["卜蜂企業", "卜蜂食品", "卜蜂雞肉"],
     ["卜蜂蓮花", "正大集團", "Charoen Pokphand", "卜蜂國際"],
     "⚠️混淆：台灣卜蜂 vs 泰國卜蜂（正大集團／CP）、卜蜂蓮花（中國超市）為不同法人；且與大成同為雞肉業，須靠 canonical 名區隔。"),

    ("金車",
     ["金車", "金車食品", "伯朗咖啡", "波爾茶", "健酪", "噶瑪蘭威士忌"],
     ["金車教育基金會"],
     "『金車』尚distinctive；旗下伯朗、波爾、噶瑪蘭皆明確對應，納入提升召回。"),
]


def main():
    db = SessionLocal()
    with open(_PATH, encoding="utf-8") as f:
        data = json.load(f)

    existing_names = {m["canonical_name"] for m in data["manufacturers"]}

    added = []
    for canonical, aliases, exclude, reason in NEW:
        if canonical in existing_names:
            print(f"跳過（已存在）：{canonical}")
            continue

        # 取得或建立 producer
        prod = db.query(models.Producer).filter(models.Producer.name == canonical).first()
        if not prod:
            prod = models.Producer(name=canonical, risk_level="Low")
            db.add(prod)
            db.flush()

        data["manufacturers"].append({
            "producer_id": prod.id,
            "canonical_name": canonical,
            "aliases": aliases,
            "exclude": exclude,
            "exclude_reason": reason,
            "_draft": True,   # 標記為草稿，人工確認 exclude 後可移除
        })
        added.append((prod.id, canonical, len(aliases), len(exclude)))

    db.commit()
    db.close()

    data["_meta"]["last_updated"] = "2026-07-15"
    data["_meta"]["draft_note"] = "含 2026-07-15 新增之草稿廠商（_draft=true），exclude 清單待人工最終確認後移除此標記。"

    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n新增 {len(added)} 家：")
    for pid, name, na, ne in added:
        print(f"  id={pid:<3} {name:<10} aliases={na} exclude={ne}")


if __name__ == "__main__":
    main()
