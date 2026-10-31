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

    def test_empty_image_preserves_original_without_inventing_regions(self):
        result = detect([], 10, (1000, 1000))
        self.assertEqual(result['status'], 'no_dense_regions')
        self.assertEqual(result['regions'], [])
        self.assertTrue(result['preserve_original'])

    def test_ingredient_text_gets_generic_region(self):
        rows = [item(0, '成分蔗糖多磷酸鈉葡萄糖水100公克', [20, 20, 400, 50])]
        regions = detect(rows, 10, (1000, 1000))['regions']
        self.assertEqual([r['class'] for r in regions], ['dense_text'])

    def test_distant_text_panels_are_not_forced_into_one_box(self):
        rows = [item(0, '成分與保存方式請見包裝背面標示', [20, 20, 300, 40]),
                item(1, '營養標示每份熱量蛋白質碳水化合物', [700, 700, 980, 740])]
        regions = detect(rows, 10, (1000, 1000))['regions']
        self.assertEqual([r['paragraph_ids'] for r in regions], [[0], [1]])

    def test_nutrients_below_sodium_are_kept(self):
        rows = [item(0, '營養標示', [100, 100, 180, 120]),
                item(1, '熱量蛋白質', [100, 130, 180, 160]),
                item(2, '鈉', [100, 170, 120, 190]),
                item(3, '100毫克', [200, 170, 250, 190]),
                item(4, '鈣200毫克', [100, 200, 250, 220])]
        regions = detect(rows, 10, (1000, 1000))['regions']
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]['paragraph_ids'], [0, 1, 2, 3, 4])
        self.assertGreaterEqual(regions[0]['rect'][3], 220)
        self.assertGreaterEqual(regions[0]['rect'][2], 250)
        self.assertEqual(regions[0]['class'], 'dense_text')

    def test_keyword_changes_do_not_change_geometry_or_class(self):
        rows = [item(0, '營養標示熱量蛋白質碳水化合物', [0, 0, 200, 30])]
        other = [{**rows[0], 'text': '包裝說明文字成分資訊保存方式'}]
        a, b = [detect(r, 10, (200, 30))['regions'][0] for r in (rows, other)]
        self.assertEqual(a['rect'], b['rect'])
        self.assertEqual(a['class'], b['class'])
        self.assertEqual(a['rect'], [0, 0, 200, 30])

    def test_barcode_digits_are_not_dense_prose(self):
        rows = [item(0, '4713837033351', [100, 100, 300, 120], words=5),
                item(1, '49', [310, 100, 340, 120], words=1)]
        self.assertEqual(detect(rows, 10, (1000, 1000))['regions'], [])


if __name__ == '__main__':
    unittest.main()
