"""Offline paired summary; no API requests and no changes to predictions."""
import argparse
import json
from pathlib import Path


def summarize(root):
    records = [json.loads(p.read_text(encoding='utf-8')) for p in (root/'cases').glob('*.json')]
    paired = [r for r in records if r.get('status') == 'completed' and 'scores' in r]
    result = {'completed': sum(r.get('status') == 'completed' for r in records),
              'failed': sum(r.get('status') == 'failed' for r in records), 'paired_scored': len(paired)}
    for mode in ('A', 'B'):
        totals = {k: sum(r['scores'][mode][k] for r in paired) for k in
                  ('ingredient_exact_hit', 'ingredient_predicted', 'ingredient_gt',
                   'numeric_correct', 'numeric_wrong', 'numeric_missing', 'numeric_hardfill')}
        hit, pred, gt = (totals[k] for k in ('ingredient_exact_hit', 'ingredient_predicted', 'ingredient_gt'))
        totals['ingredient_micro_precision'] = hit/pred if pred else None
        totals['ingredient_micro_recall'] = hit/gt if gt else None
        totals['ingredient_micro_f1'] = 2*hit/(pred+gt) if pred+gt else None
        denom = totals['numeric_correct']+totals['numeric_wrong']+totals['numeric_missing']
        totals['numeric_gt_accuracy'] = totals['numeric_correct']/denom if denom else None
        calls = [meta for r in paired for name, meta in r['calls'].items() if mode == 'B' or name == 'A']
        totals['total_call_seconds'] = round(sum(c['elapsed_s'] for c in calls), 3)
        totals['mean_case_call_seconds'] = totals['total_call_seconds']/len(paired) if paired else None
        totals['total_tokens'] = sum(c['usage'].get('total_token_count', 0) or 0 for c in calls)
        result[mode] = totals
    result['fallback_cases'] = sum(bool(r.get('fallback_groups')) for r in paired)
    result['B_cost_accounting'] = 'Includes shared A request plus localization plus crop reread; A was not billed twice.'
    result['scope'] = 'Exploratory historical development dataset; exact ingredient multiset and 18 nutrition cells only.'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = summarize(args.output)
    (args.output/'paired_summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=2))
