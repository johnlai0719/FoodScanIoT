import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[2]/'tools'))
from gemini_compare import crop_rectangle, score
from structured_pipeline import FoodLabel
import pytest


def test_crop_coordinate_order_and_padding():
    assert crop_rectangle([100,200,500,800],(1000,2000)) == (185,170,815,1030)


@pytest.mark.parametrize('box',[[500,200,100,800],[-1,0,100,100],[0,0,1001,100],[0,0,100]])
def test_invalid_boxes_rejected(box):
    with pytest.raises(ValueError):crop_rectangle(box,(1000,1000))


def test_numeric_hardfill_is_counted():
    p=FoodLabel(is_food_label=True).model_dump();p['nutrition']['fiber']=0
    gt={'nutrition':{'protein':3},'ingredients_list':[]}
    result=score(p,gt)
    assert result['numeric_hardfill']==1
    assert result['numeric_missing']==1
