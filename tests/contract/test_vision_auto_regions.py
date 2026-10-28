"""Offline geometry/failure tests; no API calls or credentials."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]/'tools'))
from vision_auto_regions import detect, extract_words


def item(i, text, rect, words=5):
    return {'id': i, 'text': text, 'rect': rect, 'words': words}


class AutoRegionTests(unittest.TestCase):
    def test_word_preview_preserves_zero_coordinates(self):
        response = {'responses': [{'fullTextAnnotation': {'pages': [{'blocks': [
            {'paragraphs': [{'words': [{'symbols': [{'text': '測'}],
              'boundingBox': {'vertices': [{}, {'x': 10}, {'x': 10, 'y': 20}, {'y': 20}]}}]}]}
        ]}]}}]}
        self.assertEqual(extract_words(response), [{'text': '測', 'rect': [0, 0, 10, 20]}])

    def test_empty_image_has_no_invented_nutrition(self):
        result = detect([], 10, (1000, 1000))
        self.assertEqual(result['status'], 'nutrition_not_found')
        self.assertEqual(result['regions'], [])
        self.assertTrue(result['preserve_original'])

    def test_ingredient_sugar_sodium_and_units_do_not_seed_nutrition(self):
        rows = [item(0, '成分蔗糖多磷酸鈉葡萄糖水100公克', [20, 20, 400, 50])]
        self.assertFalse(any(r['class'] == 'nutrition' for r in detect(rows, 10, (1000, 1000))['regions']))

    def test_seeds_on_separate_panels_are_not_combined(self):
        rows = [item(0, '營養標示', [20, 20, 100, 40]),
                item(1, '熱量', [700, 20, 750, 40]),
                item(2, '蛋白質', [700, 700, 780, 720])]
        self.assertEqual(detect(rows, 10, (1000, 1000))['status'], 'nutrition_not_found')

    def test_nutrients_below_sodium_are_kept(self):
        rows = [item(0, '營養標示', [100, 100, 180, 120]),
                item(1, '熱量蛋白質', [100, 130, 180, 160]),
                item(2, '鈉', [100, 170, 120, 190]),
                item(3, '100毫克', [200, 170, 250, 190]),
                item(4, '鈣200毫克', [100, 200, 250, 220])]
        nut = next(r for r in detect(rows, 10, (1000, 1000))['regions'] if r['class'] == 'nutrition')
        self.assertIn(4, nut['paragraph_ids'])
        self.assertGreaterEqual(nut['rect'][3], 220)

    def test_barcode_digits_are_not_dense_prose(self):
        rows = [item(0, '4713837033351', [100, 100, 300, 120], words=5),
                item(1, '49', [310, 100, 340, 120], words=1)]
        self.assertEqual(detect(rows, 10, (1000, 1000))['regions'], [])


if __name__ == '__main__':
    unittest.main()
