"""Production routing: the same one-envelope geometry used in the C experiment."""
from easyocr_union_gemini import text_envelope
from vision_auto_regions import extract_words

ROUTING_POLICY = 'vision_text_envelope_v1'


def route(response, size):
    boxes = []
    for word in extract_words(response):
        x1, y1, x2, y2 = word['rect']
        boxes.append([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
    rect, fallback = text_envelope(boxes, size)
    labels = {'no_boxes': '完整影像（未取得文字框）',
              'invalid_envelope': '完整影像（文字範圍無效）',
              'little_background_removed': '完整影像（文字範圍涵蓋整圖）'}
    return {'policy': ROUTING_POLICY,
            'status': 'original_fallback' if fallback else 'text_envelope_found',
            'regions': [{'class': 'text_envelope', 'label': labels.get(fallback, '文字外接大框'),
                         'rect': list(rect), 'color': '#E66A00', 'fallback': fallback,
                         'word_box_count': len(boxes),
                         'retained_area': (rect[2]-rect[0])*(rect[3]-rect[1])/(size[0]*size[1]),
                         'basis': 'Union of all OCR word boxes; 4%/minimum16px padding; >=90% uses full image'}]}
