"""Offline, seed-scoped additive agreement; not independently labelled accuracy."""
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
SOURCE = Path("D:/FoodScanIot/APP-sync-server")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "server"))
sys.path.insert(0, str(SOURCE / "PPOCR_TEST"))
from module_a import ingredient_matching as m
import build_dict as bd


def load_knowledge():
    seed = SOURCE / "server/seed_data/reference_seed.sql"
    rows = []
    for hit in re.finditer(r"INSERT INTO public\.additives \(([^)]*)\) VALUES \((.*?)\);\n", seed.read_text(encoding="utf-8"), re.S):
        columns = [s.strip() for s in hit[1].split(",")]
        values = [bd._unquote(s) for s in bd._split_values(hit[2])]
        assert len(columns) == len(values)
        row = dict(zip(columns, values))
        for key in ("aliases", "category"):
            row[key] = json.loads(row[key]) if row.get(key) else []
        row["_n_zh"] = m.normalize_text(row.get("name_zh") or "")
        row["_n_zh_parts"] = [m.normalize_text(p) for p in re.split(r"[;；、]", row.get("name_zh") or "") if p.strip()]
        row["_n_aliases"] = [m.normalize_text(p) for p in row["aliases"]]
        rows.append(row)
    assert len(rows) > 700
    rows.sort(key=lambda r: len(r["name_zh"] or ""), reverse=True)
    class Cursor:
        def execute(self, sql):
            pass
        def fetchall(self):
            return rows
    m._load_generic_terms(Cursor())
    m._load_substance_names(Cursor())
    return rows, seed


def extract(label, knowledge):
    parsed = m._items_from_raw_text(label.get("ingredients_raw"))
    if parsed is not None:
        names = [item["name"] for item in parsed["items"]]
    else:
        names = [name for name, source in m._expand_ingredients(label.get("ingredients_list") or [])]
    found, review = {}, []
    for name in names:
        norm = m.normalize_text(name)
        candidates = m._candidate_names(name) or [norm]
        match = None
        if norm not in {"水", "純水", "熱水", "冰水", "蒸餾水", "礦泉水", "飲用水", "water"}:
            for candidate in candidates:
                if m._is_generic_term(candidate):
                    continue
                match = m._best_additive_match(candidate, knowledge)
                if match:
                    break
        if m._is_generic_term(m.normalize_text(m._strip_brackets(name))) and len(candidates) <= 1:
            match = None
        if match:
            found[match["record_id"]] = match["name_zh"]
        else:
            review.append(name)
    return found, review


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    knowledge, seed = load_knowledge()
    ab = {p.stem: read(p) for p in (ROOT / ".artifacts/gemini-ab-177-v1/cases").glob("*.json")}
    c = {p.stem: read(p) for p in (ROOT / ".artifacts/easyocr-union-gemini-177-v1/cases").glob("*.json")}
    gt = {p.stem: p for p in (SOURCE / "測試/量化測試/ground_truth").rglob("*.json")}
    ids = sorted(k for k in ab.keys() & c.keys() if ab[k].get("status") == c[k].get("status") == "completed")
    assert len(ids) == 163, len(ids)
    totals = {method: {"tp": 0, "fp": 0, "fn": 0} for method in "ABC"}
    details, review = [], []
    for case in ids:
        target, unmatched = extract(read(gt[case]), knowledge)
        review.extend({"case_id": case, "gt_unmatched_item": name} for name in unmatched)
        item = {"case_id": case, "GT": target}
        for method in "ABC":
            label = ab[case][method] if method != "C" else c[case][method]
            predicted, unused = extract(label, knowledge)
            actual, predicted_ids = set(target), set(predicted)
            counts = {"tp": len(actual & predicted_ids), "fp": len(predicted_ids - actual), "fn": len(actual - predicted_ids)}
            for key, value in counts.items():
                totals[method][key] += value
            item[method] = {"additives": predicted, **counts, "missing": sorted(actual - predicted_ids), "extra": sorted(predicted_ids - actual)}
        details.append(item)
    for counts in totals.values():
        tp, fp, fn = (counts[key] for key in ("tp", "fp", "fn"))
        counts.update(precision=tp / (tp + fp) if tp + fp else None, recall=tp / (tp + fn) if tp + fn else None, f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None)
    summary = {"metric": "seed-scoped additive matching agreement, micro, unique record_id per case", "cases": len(ids), "knowledge_rows": len(knowledge), "reference": "GT ingredients processed by same matcher; no independent additive annotations; unmatched GT items excluded, not verified non-additives", "vector_rag": False, "seed_sha256": hashlib.sha256(seed.read_bytes()).hexdigest(), "matcher_sha256": hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest(), "methods": totals}
    out = ROOT / ".artifacts/gemini-additive-eval-163-v1"
    out.mkdir(parents=True, exist_ok=True)
    for filename, data in (("summary.json", summary), ("per_case.json", details)):
        (out / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "gt_unmatched_review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "gt_unmatched_item"])
        writer.writeheader()
        writer.writerows(review)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
