"""Deterministic serving conversion; no inferred units or missing-value defaults."""
import copy
import json
import math
import re
import unicodedata

FIELDS = ('calories', 'protein', 'fat', 'saturated_fat', 'trans_fat',
          'carbohydrates', 'sugar', 'fiber', 'sodium')


def number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def serving_evidence(texts, serving_size):
    """Require a unique explicit serving declaration agreeing with image extraction."""
    if not number(serving_size) or serving_size <= 0:
        return None
    found = {}
    pattern = r'每(?:一)?份(?:量)?\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(毫升|公克|ml|g)(?![A-Za-z])'
    for text in texts:
        text = unicodedata.normalize('NFKC', text or '')
        for match in re.finditer(pattern, text, flags=re.I):
            amount = float(match[1])
            unit = 'ml' if match[2].lower() in ('ml', '毫升') else 'g'
            found[(amount, unit)] = match[0]
    if len(found) != 1:
        return None
    (amount, unit), source = next(iter(found.items()))
    if not math.isclose(amount, serving_size, rel_tol=0, abs_tol=1e-6):
        return None
    return {'amount': amount, 'unit': unit, 'source': source}


def normalize_label(label):
    result = copy.deepcopy(label)
    evidence = result.get('serving_evidence') or {}
    # Re-validate evidence instead of trusting a bare unit field.
    verified = serving_evidence([evidence.get('source', '')], result.get('serving_size'))
    if not verified or verified != evidence:
        return result
    per_serving = result.get('nutrition_per_serving') or {}
    nutrition = result.setdefault('nutrition', {})
    converted = []
    for key in FIELDS:
        value = per_serving.get(key)
        if nutrition.get(key) is None and number(value):
            converted_value = value * (100 / verified['amount'])
            if math.isfinite(converted_value):
                nutrition[key] = converted_value
                converted.append(key)
    result['nutrition_label'] = {
        'per_serving': per_serving,
        'serving_size': verified['amount'], 'serving_unit': verified['unit'],
        'serving_source': verified['source'], 'converted_fields': converted,
        'basis': 'per_100_ml' if verified['unit'] == 'ml' else 'per_100_g',
    }
    return result


def product_label(product):
    other = product.get('other_nutrition') or {}
    if isinstance(other, str):
        try:
            other = json.loads(other)
        except (ValueError, TypeError):
            return None
    return other.get('label') if isinstance(other, dict) else None


def insufficient_message(product, missing):
    other = product.get('other_nutrition') or {}
    if isinstance(other, str):
        try:
            other = json.loads(other)
        except (ValueError, TypeError):
            other = {}
    per = other.get('per_serving') if isinstance(other, dict) else None
    if isinstance(per, dict) and any(number(per.get(k)) for k in FIELDS):
        values = []
        for key, title, unit in [('calories', '熱量', '大卡'), ('sugar', '糖', '公克'), ('sodium', '鈉', '毫克')]:
            if number(per.get(key)):
                values.append(f'{title} {per[key]:g} {unit}')
        return ('已辨識到每份營養' + ('（' + '、'.join(values) + '）' if values else '')
                + f'，但每 100 公克／毫升資料仍不足（缺少 {missing}），暫不評分。'
                '請確認每份份量及公克／毫升單位，或補拍包含份量與營養表的完整照片。')
    return f'營養標示未辨識完整（缺少 {missing}），無法計算健康評分。請對準營養標示區域重新拍攝。'
