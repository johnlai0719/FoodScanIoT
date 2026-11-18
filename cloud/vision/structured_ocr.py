"""Validate Hunyuan JSON and adapt it to the existing text and table parsers."""
import json
import re
import unicodedata


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


NULLABLE_TEXT = {"type": ["string", "null"]}
INGREDIENT_SCHEMA = _object({
    "ingredients": {"type": "array", "items": {"type": "string"}},
    "allergy_warning": NULLABLE_TEXT,
})
INGREDIENT_PROMPT = (
    "只讀圖片中的成分或原料欄，以及另外印出的過敏原警語。"
    "ingredients 依印刷順序，每個成分一個字串，同一項只輸出一次，不猜測、不摘要。"
    "忽略品名、廠商、營養、有效日期、保存及其他說明。"
    "沒有讀到成分填 []，沒有看到過敏原警語填 null，不從成分推斷。只輸出 JSON。"
)
LABEL_SCHEMA = _object({
    **INGREDIENT_SCHEMA["properties"],
    "name": NULLABLE_TEXT,
    "manufacturer": NULLABLE_TEXT,
})
LABEL_PROMPT = (
    "只讀圖片中的成分或原料、明確印出的過敏原警語、品名和廠商。"
    "ingredients 依印刷順序，每個成分一個字串，同一項只輸出一次。"
    "allergy_warning 只填印出的警語，不從成分推斷。"
    "name 抄標示的品名；沒有品名欄時，只能抄清楚可見的完整產品名稱。"
    "manufacturer 抄製造商、委製商、負責廠商、進口商或代理商的完整名稱，依此順序取一個。"
    "廠商不含角色標籤、地址或電話。忽略營養、保存資訊和其他文字。"
    "沒有讀到成分填 []，看不到的其他欄填 null。只輸出 JSON。"
)

NUTRIENTS = ("熱量", "蛋白質", "脂肪", "飽和脂肪", "反式脂肪",
             "碳水化合物", "糖", "膳食纖維", "鈉")
COLUMNS = {
    "per_serving": "每份",
    "per_100": "每100公克／毫升",
    "daily_value": "每日參考值百分比",
}
NUTRITION_SCHEMA = _object({
    "serving_size": NULLABLE_TEXT,
    "servings_per_container": NULLABLE_TEXT,
    "columns": {"type": "array", "items": {"enum": list(COLUMNS)}},
    "rows": {"type": "array", "items": _object({
        "nutrient": {"enum": list(NUTRIENTS)},
        "values": {"type": "array", "items": NULLABLE_TEXT},
    })},
})
NUTRITION_PROMPT = (
    "只轉錄圖中的營養標示，輸出一個 JSON 物件。"
    "serving_size 抄每份量的數值與單位，servings_per_container 抄本包裝份數；沒寫就填 null。"
    "columns 依印刷順序，只列實際看得到的表頭：每份用 per_serving，"
    "每100公克或毫升用 per_100，每日參考值百分比用 daily_value。"
    "rows 只列有看到的營養素，同一營養素只列一次；values 按 columns 順序逐格抄錄數字及單位，"
    "模糊或空白的格填 null。不要換算、補值或把百分比當成每100的數字。沒有表格就填空陣列。"
)

WARNING = re.compile(
    r"[（(※＊*\s]*(?:過敏原(?:資訊|警語)?\s*[:：]|"
    r"本(?:產品|商品|品)(?:含有|與其他|生產)|食物過敏者)"
)
OTHER_FIELD = re.compile(r"^(?:品名|產品名稱|製造商|廠商|有效日期|保存期限|營養標示)\s*[:：]?")
NUMBER = r"\d+(?:[.,]\d+)?"
FIELD_PREFIX = {
    "name": re.compile(r"^(?:(?:品名|產品名稱|商品名稱)\s*[:：]\s*)+"),
    "manufacturer": re.compile(
        r"^(?:(?:廠商|製造商|製造廠商|委製商|委託製造商|負責廠商|進口商|代理商)\s*[:：]\s*)+"
    ),
}
QUANTITY_ONLY = re.compile(
    r"(?:(?:淨重|重量|內容量|容量|每份量)\s*[:：]?\s*)?"
    r"[(（]?\s*" + NUMBER + r"\s*(?:毫克|公克|公斤|公升|毫升|克|kg|mg|g|ml|l)"
    r"\s*[)）]?\s*[。.]?",
    re.IGNORECASE,
)
SERVING_SIZE = re.compile(NUMBER + r"\s*(?:公克|克|毫升|g|ml)", re.IGNORECASE)
SERVING_COUNT = re.compile(NUMBER + r"\s*份?")


def _read_object(text, schema):
    value = json.loads(text)
    if not isinstance(value, dict) or value.keys() != schema["properties"].keys():
        raise ValueError("unexpected OCR fields")
    return value


def _text(value):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 400:
        raise ValueError("invalid OCR text field")
    return " ".join(value.split())


def _key(value):
    return "".join(ch for ch in unicodedata.normalize("NFKC", value).casefold() if ch.isalnum())


def ingredients_lines(text, *, product_name="", key=_key):
    data = _read_object(text, INGREDIENT_SCHEMA)
    return _ingredient_fields(data, product_name, key)


def label_lines(text, *, product_name="", key=_key, evidence=""):
    data = _read_object(text, LABEL_SCHEMA)
    fields = {}
    for field in ("name", "manufacturer"):
        value = FIELD_PREFIX[field].sub("", _text(data[field]))
        normalized = key(value)
        if 2 <= len(normalized) <= 80 and normalized in evidence:
            fields[field] = value
    lines = _ingredient_fields(data, product_name or fields.get("name", ""), key)
    for field, label in (("name", "品名"), ("manufacturer", "廠商")):
        if field in fields:
            lines.append(f"{label}：{fields[field]}")
    return lines


def _ingredient_fields(data, product_name, key):
    items = data["ingredients"]
    if not isinstance(items, list) or len(items) > 160 or any(not isinstance(item, str) for item in items):
        raise ValueError("invalid OCR ingredients")
    unique = {}
    warnings = {}
    name_key = key(product_name)
    allergy = _text(data["allergy_warning"])
    if allergy:
        warnings[key(allergy)] = allergy
    for item in items:
        item = re.sub(r"^(?:成[分份]|原料)\s*[:：]\s*", "", _text(item))
        warning = WARNING.search(item)
        if warning:
            value = item[warning.start():].strip(" （(※＊*")
            warnings.setdefault(key(value), value)
            item = item[:warning.start()].strip(" 、,，")
        item_key = key(item)
        if (item_key and item_key != name_key and not OTHER_FIELD.match(item)
                and not QUANTITY_ONLY.fullmatch(unicodedata.normalize("NFKC", item))):
            unique.setdefault(item_key, item)
    lines = ["成分：" + "、".join(unique.values())] if unique else []
    for value in warnings.values():
        lines.append(value if "過敏原" in value else "過敏原資訊：" + value)
    return lines


def nutrition_lines(text):
    data = _read_object(text, NUTRITION_SCHEMA)
    columns, rows = data["columns"], data["rows"]
    if (not isinstance(columns, list) or len(columns) > len(COLUMNS)
            or any(not isinstance(col, str) or col not in COLUMNS for col in columns)
            or len(columns) != len(set(columns)) or not isinstance(rows, list) or len(rows) > 18):
        raise ValueError("invalid OCR table structure")
    cells = {}
    for row in rows:
        if not isinstance(row, dict) or row.keys() != {"nutrient", "values"}:
            raise ValueError("invalid OCR table row")
        label, values = row["nutrient"], row["values"]
        if label not in NUTRIENTS or not isinstance(values, list) or len(values) != len(columns):
            raise ValueError("invalid OCR table columns")
        values = tuple(_text(value) for value in values)
        if any("|" in value for value in values):
            raise ValueError("unexpected table delimiter")
        if label in cells and cells[label] != values:
            raise ValueError("conflicting duplicate OCR table row")
        cells[label] = values
    if not columns or not any(any(row) for row in cells.values()):
        return []

    serving = _text(data["serving_size"])
    count = _text(data["servings_per_container"])
    if (serving and not SERVING_SIZE.fullmatch(serving)) or (count and not SERVING_COUNT.fullmatch(count)):
        raise ValueError("invalid OCR serving metadata")
    lines = [f"每一份量 {serving}"] if serving else []
    if count:
        lines.append(f"本包裝含 {count}" + ("" if count.endswith("份") else "份"))
    header = ["營養標示", *(COLUMNS[col] for col in columns)]
    lines.extend("| " + " | ".join(row) + " |" for row in (
        header, ["----"] * len(header),
        *([label, *(value or "—" for value in values)] for label, values in cells.items()),
    ))
    return lines
