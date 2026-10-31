"""Conservative OCR gate; not a food classifier or a legibility guarantee."""
from vision_auto_regions import extract_words

POLICY = 'vision_min_text_v1'
MIN_CHARACTERS = 3
RETAKE_MESSAGE = '照片中幾乎沒有辨識到有效文字，請靠近包裝上的成分表或營養標示，確認對焦與光線後重新拍攝。'


def assess(responses):
    counts = []
    for response in responses:
        first = (response.get('responses') or [{}])[0]
        text = first.get('fullTextAnnotation', {}).get('text', '')
        annotations = first.get('textAnnotations') or []
        alternatives = [text, annotations[0].get('description', '') if annotations else '',
                        ''.join(word.get('text', '') for word in extract_words(response))]
        counts.append(max(sum(char.isalnum() for char in value) for value in alternatives))
    total = sum(counts)
    return {'policy': POLICY, 'decision': 'pass' if total >= MIN_CHARACTERS else 'retake',
            'minimum_characters': MIN_CHARACTERS, 'character_count': total,
            'per_image_characters': counts,
            'reason': '' if total >= MIN_CHARACTERS else RETAKE_MESSAGE}
