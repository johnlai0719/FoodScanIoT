"""Offline OCR text coverage audit, NOT a CER/WER or field extraction evaluation."""
import argparse
import hashlib
import json
import statistics
import subprocess
import unicodedata
from pathlib import Path

from rapidfuzz.fuzz import partial_ratio_alignment
from evaluate_vision_additives import helper, matcher


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', str(text)).casefold() if c.isalnum())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('frozen_union', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    manifest = read(args.source/'manifest.json')
    frozen = read(args.frozen_union/'manifest.json')
    assert digest(args.source/'manifest.json') == frozen['source_manifest_sha256']
    args.output.mkdir(exist_ok=False, parents=True)
    (args.output/'texts').mkdir()
    knowledge, seed = helper.load_knowledge()
    knowledge_by_id = {r['record_id']: r for r in knowledge}
    cases, hashes = [], {}
    confidences = []
    for case in manifest['inventory']:
        gt_path = Path(case['gt'])
        assert digest(gt_path) == case['gt_sha256']
        gt = read(gt_path)
        texts, image_stats = [], []
        for i, image in enumerate(case['images']):
            assert digest(Path(image['path'])) == image['sha256']
            path = args.source/'vision'/case['case_id']/f'{i}.json'
            sha = digest(path)
            assert sha == frozen['vision_hashes'][str(path.resolve())]
            hashes[str(path.resolve())] = sha
            response = read(path)['responses'][0]
            assert 'error' not in response
            annotation = response.get('fullTextAnnotation', {})
            text = annotation.get('text', '')
            texts.append(text)
            count = 0
            for page in annotation.get('pages', []):
                for block in page.get('blocks', []):
                    for paragraph in block.get('paragraphs', []):
                        for word in paragraph.get('words', []):
                            count += 1
                            if 'confidence' in word:
                                confidences.append(word['confidence'])
            image_stats.append({'characters': len(normalized(text)), 'word_count': count})
        text = '\n\n[下一張照片]\n\n'.join(texts)
        norm = '\n'.join(normalized(t) for t in texts)  # Do not invent cross-photo names.
        items = []
        for name in gt.get('ingredients_list') or []:
            needle = normalized(name)
            if not needle:
                continue
            found = needle in norm
            entry = {'name': name, 'characters': len(needle), 'present': found}
            if not found:
                alignment = partial_ratio_alignment(needle, norm)
                entry['nearest_normalized_fragment'] = norm[alignment.dest_start:alignment.dest_end] if alignment else ''
                entry['fuzzy_score_for_review_only'] = alignment.score if alignment else 0
            items.append(entry)
        target, unmatched = helper.extract(gt, knowledge)
        additive = []
        for record_id, name in target.items():
            record = knowledge_by_id[record_id]
            variants = [record.get('name_zh') or '', *(record.get('aliases') or [])]
            # The canonical name can combine several synonyms in one seed entry.
            import re
            variants += re.split('[;；、]', record.get('name_zh') or '')
            matched = next((v for v in variants if len(normalized(v)) >= 2 and normalized(v) in norm), None)
            additive.append({'record_id': record_id, 'name': name, 'present': matched is not None,
                             'matched_variant': matched})
        raw = normalized(gt.get('ingredients_raw') or '')
        row = {'case_id': case['case_id'], 'food_label': gt.get('is_food_label'),
               'image_count': len(texts), 'images': image_stats, 'ocr_characters': sum(s['characters'] for s in image_stats),
               'ingredients': items, 'raw_ingredient_paragraph_present': raw in norm if raw else None,
               'reference_additives': additive, 'unmatched_gt_ingredients': unmatched,
               'name_present': normalized(gt.get('name')) in norm if gt.get('name') else None}
        meaningful = [x for x in items if x['characters'] >= 2]
        row['ingredient_coverage'] = sum(x['present'] for x in meaningful)/len(meaningful) if meaningful else None
        cases.append(row)
        (args.output/'texts'/f"{case['case_id']}.txt").write_text(text, encoding='utf-8')
    items = [x for r in cases for x in r['ingredients']]
    meaningful = [x for x in items if x['characters'] >= 2]
    additives = [x for r in cases for x in r['reference_additives']]
    eligible = [r for r in cases if r['ingredient_coverage'] is not None]
    def coverage(values):
        hit = sum(x['present'] for x in values)
        return {'present': hit, 'total': len(values), 'coverage': hit/len(values) if values else None}
    result = {'snapshot_date': '2026-10-29', 'branch': subprocess.check_output(['git','branch','--show-current'],text=True).strip(),
              'commit': subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
              'working_tree': 'local uncommitted audit script; production services unchanged by audit',
              'source_manifest_sha256': digest(args.source/'manifest.json'),
              'script_sha256': digest(Path(__file__)), 'matcher_sha256': digest(Path(matcher.__file__)),
              'seed_sha256': digest(seed), 'vision_hashes': hashes,
              'cases': len(cases), 'images': len(hashes), 'empty_ocr_cases': [r['case_id'] for r in cases if r['ocr_characters']==0],
              'ingredient_items': coverage(items), 'ingredient_items_at_least_two_characters': coverage(meaningful),
              'cases_with_ingredient_items': len(eligible),
              'cases_all_meaningful_items_present': sum(r['ingredient_coverage']==1 for r in eligible),
              'cases_at_least_90pct_present': sum(r['ingredient_coverage']>=.9 for r in eligible),
              'raw_ingredient_paragraph': {'present': sum(r['raw_ingredient_paragraph_present'] is True for r in cases),
                                           'total': sum(r['raw_ingredient_paragraph_present'] is not None for r in cases)},
              'seed_additive_name_alias_coverage': coverage(additives),
              'word_confidence_descriptive_only': {'words': len(confidences),
                  'mean': statistics.mean(confidences) if confidences else None,
                  'below_0_8': sum(v<.8 for v in confidences)},
              'limitations': ['Coverage searches the whole OCR text, not a labelled ingredient region; it is not CER, WER, precision or downstream extraction accuracy.',
                 'NFKC/casefold and removal of punctuation/whitespace; no traditional/simplified conversion, fuzzy correction or semantic synonyms for ingredient coverage.',
                 'Seed alias presence is only availability of GT-matcher-derived additive names in OCR. No independent additive labels; unmatched GT excluded.',
                 'Ingredient items and raw GT may be inconsistent; low coverage needs source-image review, not automatic attribution to OCR.',
                 'No nutrition row-column or numeric accuracy score: OCR provides full packaging text and no matching field predictions.',
                 'Existing development set and cached OCR, zero new API calls. No text-only Gemini experiment performed.']}
    (args.output/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output/'per_case.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='vision_hashes'},ensure_ascii=False,indent=2))
    print('LOW COVERAGE', [(r['case_id'],round(r['ingredient_coverage'],3)) for r in sorted(eligible,key=lambda r:r['ingredient_coverage'])[:12]])


if __name__=='__main__':
    main()
