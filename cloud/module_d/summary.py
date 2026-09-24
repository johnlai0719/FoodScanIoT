"""以規則產生兩段總結。**純函式，不呼叫任何模型。**

為什麼不用語言模型
------------------
2026-09-14 實測（本地 Qwen3.5-2B，同一份資料各跑三次）：

  未加約束     三次全部編造，且是最糟的那種——
               「高膽固醇」（營養資料裡根本沒有膽固醇欄位）、
               「經國際機構評估安全」「長期適量攝取對健康無害」
               （本系統沒有做安全性判定，無依據做此保證）。
  加上約束     禁止提及未出現的營養素、禁止宣稱安全或有害之後，
               輸出退化成把數字念一遍——而那些數字畫面上本來就有——
               且仍寫錯單位（「蛋白質 17.8 毫克」，應為公克）。

結論：**這段總結的價值全部來自詮釋性的句子，而那些正好是沒有出處的句子。**
收緊到不編造，它就沒有存在意義了。

規則版反而答得出真正該回答的問題——「為什麼是這個等級」。每一句都指得回
某個欄位，而 Gemini 那句「長期過量攝取可能增加心血管負擔」對任何產品都成立，
等於沒說。

寫作規則（改這個檔案前請先讀）
------------------------------
1. **只能引用傳進來的數字。** 不得引入任何未出現在參數裡的營養素或成分。
2. **不得宣稱安全或有害。** 本系統做的是標示判讀，不是安全性評估。
   可以說「扣了幾分」，不可以說「安全」「無害」「有害」。
3. **不得把「沒查到」講成「沒有」。** 添加物為 0 時必須同時說明可能是
   未辨識成功——177 案中添加物為 0 的 42 案裡有 11 案是漏讀的。
4. **缺值不可當成 0。** 標示沒有那一項時寫「未標示」，並說明計分上以 0 計
   是保守原則，不是實際含量為零。
"""

# 七個族群碼的中文說法。**不另建一份詞彙表**——值域的唯一來源是
# module_a 的 GROUP_ZH_TO_EN，這裡只補「英文碼 → 給人看的中文」，
# 且鍵必須落在那張表的值域內（由 test_summary_rules 斷言）。
GROUP_LABEL_ZH = {
    "pregnant": "孕婦",
    "child": "幼童與兒童",
    "kidney_disease": "慢性腎臟病患者",
    "asthma": "氣喘患者",
    "aspirin_allergy": "阿斯匹靈過敏者",
    "pku": "苯酮尿症患者",
    "allergy": "過敏體質者",
}

# 等級只用來陳述，不在這裡推導——推導是 nutriscore_v7.get_grade() 的事，
# 飲料與純水另有一套分級帶，呈現端自己推會錯（2026-09-13 已踩過）。
_GRADE_WORD = {
    "A": "營養組成佳",
    "B": "營養組成尚可",
    "C": "營養負荷中等",
    "D": "營養負荷偏高",
    "E": "營養負荷高",
}


def _fmt_points(item) -> str:
    """「鈉 9/20」。分母一定要寫——只有「鈉 9 分」看不出是滿分還是一半。"""
    pts = abs(item.get("points") or 0)
    mx = item.get("maxPoints")
    name = (item.get("reason") or "").split(" (")[0]
    return "%s %d/%d" % (name, pts, mx) if mx else "%s %d" % (name, pts)


def build_overall_summary(score, grade, score_breakdown) -> str:
    """為什麼是這個分數。

    結構：分數與方向 → 扣分主要來自哪幾項 → 加分抵銷多少 → 哪幾項未標示。
    """
    parts = []
    g = (grade or "").upper()
    word = _GRADE_WORD.get(g)
    # 方向必須寫出來：不寫的話「17 分」會被讀成 0–100 裡的 17 分。
    parts.append(
        "Nutri-Score 原始分數 %s 分（%s%s，分數越低越健康）。"
        % (score, g + " 級" if g else "未分級", "，" + word if word else "")
    )

    items = list(score_breakdown or [])
    penalties = sorted(
        [i for i in items if (i.get("points") or 0) < 0],
        key=lambda i: i.get("points") or 0,
    )
    bonuses = [i for i in items if (i.get("points") or 0) > 0]

    scored = [i for i in penalties if abs(i.get("points") or 0) > 0]
    if scored:
        parts.append("扣分以%s為主。" % "、".join(_fmt_points(i) for i in scored[:3]))
    else:
        parts.append("各項營養素皆未達扣分門檻。")

    penalty_total = sum(abs(i.get("points") or 0) for i in penalties)
    bonus_total = sum(i.get("points") or 0 for i in bonuses)
    if bonus_total > 0 and penalty_total > 0:
        parts.append(
            "%s合計加回 %d 分，抵銷約 %d%% 的扣分。"
            % ("、".join((i.get("reason") or "").split(" (")[0] for i in bonuses),
               bonus_total, min(round(bonus_total / penalty_total * 100), 100))
        )
    elif bonus_total > 0:
        parts.append("加分項合計 %d 分。" % bonus_total)

    # 缺值要講出來。標示沒有膳食纖維時計 0 分是**保守原則**，
    # 不是量測到 0——把兩者混為一談會讓使用者以為這項產品真的不含纖維。
    missing = [(i.get("reason") or "").split(" (")[0]
               for i in items if i.get("value") is None and i.get("unit")]
    if missing:
        parts.append(
            "%s未標示，依保守原則計 0 分（非含量為零）。" % "、".join(missing[:4])
        )
    return "".join(parts)


def build_additives_summary(chemical, basic_detail) -> str:
    """含哪些添加物、屬哪些類別、有幾項帶族群風險提示。"""
    chem = list(chemical or [])
    if not chem:
        # ⚠ 0 不等於「不含添加物」。2026-09-10 以 177 案量測：添加物為 0 的
        # 42 案中有 11 案是標示上有、我們沒讀到，而系統分不出是哪一種。
        return ("未比對到食品添加物。這可能是產品確實未使用，"
                "也可能是成分標示沒有辨識成功——系統無法分辨這兩種情況，"
                "請以包裝上的成分欄為準。")

    parts = ["共比對到 %d 種食品添加物" % len(chem)]

    # 類別統計。一項可屬多類，故各類相加會大於總數——這裡只列類別名不列次數，
    # 避免在一句話裡出現「加起來不等於總數」而讀者無從解釋。
    cats = []
    for c in chem:
        for k in (c.get("category") or []):
            if k not in cats:
                cats.append(k)
    if cats:
        parts.append("，分屬%s" % "、".join(cats[:5]))
        if len(cats) > 5:
            parts.append("等 %d 類" % len(cats))
    parts.append("。")

    risky = [c for c in chem
             if any((r.get("riskLevel") or 0) >= 3 for r in (c.get("groupRisks") or []))]
    if risky:
        groups = []
        for c in risky:
            for r in (c.get("groupRisks") or []):
                if (r.get("riskLevel") or 0) >= 3:
                    g = GROUP_LABEL_ZH.get(r.get("group"))
                    if g and g not in groups:
                        groups.append(g)
        parts.append(
            "其中 %d 項對特定族群（%s）有風險提示，每則提示均附來源。"
            % (len(risky), "、".join(groups[:4]))
        )

    # 未比對到的成分要講，但**不可寫成「非添加物」**：2026-09-10 實測
    # 30／521（5.8%）的真添加物會落在這一欄，那是誠實的缺口不是判定。
    others = [b for b in (basic_detail or [])
              if not (b.get("isAdditive") is True or b.get("isAdditive") == "true")]
    if others:
        parts.append(
            "另有 %d 項成分未比對到添加物資料庫，不代表確定不是添加物。" % len(others)
        )
    return "".join(parts)
