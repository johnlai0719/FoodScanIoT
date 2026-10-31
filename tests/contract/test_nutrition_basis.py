import copy
import math

import pytest

from module_b.nutrition_basis import serving_evidence, normalize_label, insufficient_message
from module_b.scoring import calculate_nutriscore
from module_d.response_builder import build_response
from transforms import normalize_result


def label():
    return {
        'serving_size': 330.0,
        'nutrition': {'calories': None, 'sugar': None, 'sodium': None},
        'nutrition_per_serving': {'calories': 132.0, 'sugar': 32.0, 'sodium': 63.0,
                                 'protein': 0.0, 'fat': 0.0, 'saturated_fat': 0.0},
        'serving_evidence': serving_evidence(['營養標示\n每一份量330毫升\n本包裝含1份'], 330),
    }


def test_reported_beverage_converts_without_extra_model_call_or_mutation():
    original = label()
    frozen = copy.deepcopy(original)
    result = normalize_label(original)
    assert original == frozen
    assert result['nutrition']['calories'] == pytest.approx(40)
    assert result['nutrition']['sugar'] == pytest.approx(32 / 3.3)
    assert result['nutrition']['sodium'] == pytest.approx(63 / 3.3)
    assert result['nutrition']['fat'] == 0  # Zero is measured, not missing.
    assert result['nutrition'].get('fiber') is None
    assert result['nutrition_label']['per_serving']['sodium'] == 63
    assert result['nutrition_label']['basis'] == 'per_100_ml'
    product = dict(name='檸檬氣泡飲', **result['nutrition'])
    assert calculate_nutriscore(product, [])['deterministic_grade']


def test_direct_per100_value_is_preserved_and_each_field_converts_once():
    data = label()
    data['nutrition']['calories'] = 41
    result = normalize_label(data)
    assert result['nutrition']['calories'] == 41
    assert 'calories' not in result['nutrition_label']['converted_fields']


@pytest.mark.parametrize('text,size', [
    ('每份30公克', 30), ('每一份量：330 mL', 330), ('每份量 25 g', 25),
    ('每份量\n３３０毫升', 330),
])
def test_explicit_mass_and_volume(text, size):
    evidence = serving_evidence([text], size)
    assert evidence and evidence['amount'] == size


@pytest.mark.parametrize('texts,size', [
    (['內容量330毫升'], 330), (['每份2片'], 2), (['每份30公克'], 25),
    (['每份330毫升', '每份30公克'], 330), (['每份0公克'], 0),
    (['每份330毫升'], None), (['每份330毫升'], math.nan),
    (['每份1kg'], 1),
])
def test_ambiguous_units_or_amounts_do_not_convert(texts, size):
    assert serving_evidence(texts, size) is None
    data = label()
    data.pop('serving_evidence')
    assert normalize_label(data)['nutrition']['calories'] is None


def test_invalid_values_not_converted_and_unknown_basis_message_is_truthful():
    data = label()
    data['nutrition_per_serving'].update(sugar=math.inf, sodium=-1, fat=True)
    result = normalize_label(data)
    assert result['nutrition']['sugar'] is None
    assert result['nutrition']['sodium'] is None
    assert result['nutrition'].get('fat') is None
    message = insufficient_message({'other_nutrition': {'per_serving': {'calories': 132, 'sugar': 32}}}, '熱量')
    assert '已辨識到每份營養' in message and '132' in message and '單位' in message


def test_converted_provenance_survives_cloud_response_and_fog_normalization():
    result = normalize_label(label())
    product = dict(name='檸檬氣泡飲', barcode='TEST', brand='', manufacturer='',
                   other_nutrition={'label': result['nutrition_label']}, **result['nutrition'])
    scored = calculate_nutriscore(product, [])
    response = build_response(product, {'grade': scored['deterministic_grade']},
                              scored['calc_result'], scored['deterministic_score'],
                              [], [], {}, [], '', result['nutrition'])
    forwarded = normalize_result(response)
    assert forwarded['data']['nutrition_label']['converted_fields']
    assert forwarded['data']['nutrition_label']['per_serving']['calories'] == 132
    assert forwarded['data']['nutrition_facts']['calories'] == pytest.approx(40)
