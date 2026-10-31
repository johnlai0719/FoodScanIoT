"""Offline benchmark accounting; partial cases must never inflate paired scores."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools'))
from benchmark_vision_pipeline import save, summarize, timing


def arm(hit, predicted, target, seconds):
    return {'status': 'completed', 'reader_wall_s': seconds,
            'call': {'elapsed_s': seconds-1, 'usage': {'total_token_count': 100}},
            'stage_secs': {'vision': .8, 'routing': .2},
            'score': {'ingredient_exact_hit': hit, 'ingredient_predicted': predicted,
                      'ingredient_gt': target, 'numeric_correct': 2, 'numeric_wrong': 1,
                      'numeric_missing': 1, 'numeric_hardfill': 1}}


def test_summary_uses_only_paired_completed_cases(tmp_path):
    save(tmp_path/'cases'/'one.json', {'case_id': 'one', 'A': arm(2, 4, 3, 3), 'B': arm(3, 3, 3, 4)})
    save(tmp_path/'cases'/'two.json', {'case_id': 'two', 'A': arm(20, 20, 20, 1), 'B': {'status': 'failed'}})
    result = summarize(tmp_path)
    assert result['paired_completed'] == 1
    assert result['failed_or_partial'] == ['two']
    assert result['A']['scores']['ingredient_micro_f1'] == 4/7
    assert result['B']['scores']['ingredient_micro_f1'] == 1
    assert result['A']['scores']['nutrition_exact_accuracy'] == .5
    assert result['B']['vision']['mean_s'] == .8
    assert result['B']['total_tokens'] == 100


def test_timing_uses_case_totals_and_handles_empty():
    assert timing([]) is None
    assert timing([1, 3]) == {'n': 2, 'mean_s': 2, 'p50_s': 2, 'p95_s': 2.9}
