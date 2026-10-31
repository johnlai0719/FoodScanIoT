import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from benchmark_vision_union import envelope


def response(words):
    return {'responses':[{'fullTextAnnotation':{'pages':[{'blocks':[{'paragraphs':[{'words':words}]}]}]}}]}


def word(text,rect):
    a,b,c,d=rect
    return {'symbols':[{'text':text}], 'boundingBox':{'vertices':[
        {'x':a,'y':b},{'x':c,'y':b},{'x':c,'y':d},{'x':a,'y':d}]}}


def test_union_keeps_all_words_and_padding():
    rect,fallback,count=envelope(response([word('成分',[200,200,400,300]),word('100',[500,500,600,600])]),(1000,1000))
    assert rect==(160,160,640,640)
    assert fallback is None and count==2


def test_missing_ocr_and_nearly_full_image_keep_original():
    assert envelope(response([]),(1000,1000))==((0,0,1000,1000),'no_boxes',0)
    assert envelope(response([word('標示',[0,0,1000,1000])]),(1000,1000))==((0,0,1000,1000),'little_background_removed',1)
