#!/usr/bin/env python3
"""Assemble evidence-bound FoodScan JSON from EasyOCR and a CKIP BERT classifier.

This is an exploratory integration runner.  It never reads ground truth and it
never asks a generative model to invent or rewrite field values.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


LABELS = [
    "成分", "營養", "過敏原", "品名", "廠商", "保存", "日期", "份量",
    "注意", "認證", "其他",
]
NUTRIENT_FIELDS = [
    "calories", "protein", "fat", "saturated_fat", "trans_fat",
    "carbohydrates", "sugar", "fiber", "sodium",
]


class Nutrition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calories: float | None = Field(default=None, ge=0)
    protein: float | None = Field(default=None, ge=0)
    fat: float | None = Field(default=None, ge=0)
    saturated_fat: float | None = Field(default=None, ge=0)
    trans_fat: float | None = Field(default=None, ge=0)
    carbohydrates: float | None = Field(default=None, ge=0)
    sugar: float | None = Field(default=None, ge=0)
    fiber: float | None = Field(default=None, ge=0)
    sodium: float | None = Field(default=None, ge=0)


class FoodLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_food_label: bool
    reject_reason: str | None = None
    name: str | None = None
    brand: str | None = None
    ingredients_raw: str | None = None
    ingredients_list: list[str] = Field(default_factory=list)
    nutrition: Nutrition = Field(default_factory=Nutrition)
    serving_size: float | None = Field(default=None, ge=0)
    servings_per_container: float | None = Field(default=None, ge=0)
    nutrition_per_serving: Nutrition = Field(default_factory=Nutrition)
    manufacturer: str | None = None
    allergy_warning: str | None = None
    certification_marks: list[str] = Field(default_factory=list)


def levenshtein(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


_TFDA_LEXICON: set[str] | None = None
_TFDA_BY_LEN: dict[int, list[str]] | None = None


def load_tfda_lexicon() -> tuple[set[str], dict[int, list[str]]]:
    global _TFDA_LEXICON, _TFDA_BY_LEN
    if _TFDA_LEXICON is not None and _TFDA_BY_LEN is not None:
        return _TFDA_LEXICON, _TFDA_BY_LEN
    lex_p = Path(__file__).resolve().parent / "tfda_lexicon.json"
    if not lex_p.exists():
        _TFDA_LEXICON, _TFDA_BY_LEN = set(), {}
        return _TFDA_LEXICON, _TFDA_BY_LEN
    terms = set(json.loads(lex_p.read_text(encoding="utf-8")))
    by_len: dict[int, list[str]] = {}
    for w in terms:
        by_len.setdefault(len(w), []).append(w)
    _TFDA_LEXICON, _TFDA_BY_LEN = terms, by_len
    return _TFDA_LEXICON, _TFDA_BY_LEN


def correct_ingredient(term: str, lexicon: set[str], by_len: dict[int, list[str]]) -> str:
    term_clean = term.strip(" .。:：、，,；;`·'\"|*-_()（）[]【】{}")
    if not term_clean or len(term_clean) < 2 or not lexicon:
        return term
    if term_clean in lexicon:
        return term_clean
    L = len(term_clean)
    candidates = []
    for cand_len in (L, L - 1, L + 1):
        for cand in by_len.get(cand_len, []):
            dist = levenshtein(term_clean, cand)
            max_allowed = 1 if L <= 4 else 2
            if dist <= max_allowed:
                candidates.append((dist, cand))
    if candidates:
        candidates.sort(key=lambda x: (x[0], -len(x[1])))
        return candidates[0][1]
    return term_clean


def normalize_evidence(text: str) -> str:
    return re.sub(r"[\s`'\"|]", "", text or "").replace("：", ":")


def split_ingredients(text: str) -> list[str]:
    """Split only at top-level punctuation, preserving nested formulations."""
    text = re.sub(
        r"^.*?(?:成\s*[分份]|原\s*料|配\s*料|[內内]\s*容\s*物|材\s*料|(?<![成公每份水分])分)\s*[:：.．…·]?",
        "",
        text.strip(),
    )
    # Between CJK characters, whitespace represents missing OCR delimiter (e.g. "生奶 奶粉" -> "生奶、奶粉")
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "、", text)
    out, buf, depth = [], [], 0
    chars_since_open = 0
    pairs = {"(": ")", "（": "）", "[": "]", "【": "】", "{": "}"}
    closing = set(pairs.values())
    for char in text:
        if char in pairs:
            depth += 1
            chars_since_open = 0
        elif char in closing:
            depth = max(0, depth - 1)
            chars_since_open = 0
        elif depth > 0:
            chars_since_open += 1
            if chars_since_open > 35:  # unclosed bracket timeout safeguard
                depth = 0
                chars_since_open = 0
        if depth == 0 and char in "、，,；;\n`·":
            item = "".join(buf).strip(" .。:：、，,；;`·")
            if item:
                out.append(item)
            buf = []
        else:
            buf.append(char)
    item = "".join(buf).strip(" .。:：、，,；;`·")
    if item:
        out.append(item)
    return out


def choose_ingredient_lines(rows: list[dict], threshold: float) -> list[dict]:
    head_pattern = re.compile(
        r"(?:^|[\s·])(?:成\s*[分份]|原\s*料|配\s*料|[內内]\s*容\s*物|材\s*料)\s*[:：]"
        r"|(?:^|[\s·])(?<![香調味佐])料\s*[:：]"
        r"|(?:^|[\s·])(?<![成公每份水分])分\s*[:：]"
    )
    stop_pattern = re.compile(
        r"營養標示|每一份量|有效日期|保存期限|內容量|淨重|過敏原|製造|本產品|注意事項|警語|葷素"
    )

    for index, row in enumerate(rows):
        if head_pattern.search(row["text"]):
            result = [row]
            for following in rows[index + 1:index + 15]:
                if following.get("image_id") != row.get("image_id"):
                    break
                label = following["label"]
                conf = following.get("probabilities", {}).get(label, 0)
                if stop_pattern.search(following["text"]):
                    break
                if label in {"營養", "過敏原", "廠商", "保存", "日期"} and conf >= threshold:
                    break
                # Spurious '品名' inside ingredient lines should not break unless anchored or very short
                if label == "品名" and (re.search(r"^品\s*名", following["text"]) or (conf >= 0.8 and len(following["text"]) < 8)):
                    break
                result.append(following)
            return result

    selected = [
        row for row in rows
        if row["label"] == "成分" and row["probabilities"].get("成分", 0) >= threshold
    ]
    if selected:
        return selected

    # Fail-closed anchor fallback: copy at most four following OCR lines and stop
    # when another strongly classified field starts.
    for index, row in enumerate(rows):
        if re.search(r"成分|成份|原料|內容物|配料", row["text"]):
            result = [row]
            for following in rows[index + 1:index + 8]:
                if following.get("image_id") != row.get("image_id"):
                    break
                label = following["label"]
                if label in {"營養", "過敏原", "廠商", "保存", "日期"} and following.get("probabilities", {}).get(label, 0) >= threshold:
                    break
                if stop_pattern.search(following["text"]):
                    break
                result.append(following)
            return result
    return []


class BertClassifier:
    """Load the classifier once and reuse it across every case in a run."""

    def __init__(self, model_dir: Path, device: str, batch_size: int):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        actual = ("cuda" if torch.cuda.is_available() else "cpu") if device == "auto" else device
        if actual == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested for BERT but is unavailable")
        self.torch = torch
        self.device = actual
        self.batch_size = batch_size
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            model_dir, local_files_only=True
        ).to(actual).eval()

    def predict(self, lines: list[dict]) -> list[dict]:
        texts = []
        for index, line in enumerate(lines):
            previous = lines[index - 1]["text"] if index else ""
            following = lines[index + 1]["text"] if index + 1 < len(lines) else ""
            if index and lines[index - 1].get("image_id") != line.get("image_id"):
                previous = ""
            if index + 1 < len(lines) and lines[index + 1].get("image_id") != line.get("image_id"):
                following = ""
            texts.append(f"{previous} [SEP] {line['text']} [SEP] {following}")

        output = []
        with self.torch.inference_mode():
            for start in range(0, len(texts), self.batch_size):
                encoded = self.tokenizer(
                    texts[start:start + self.batch_size], padding=True, truncation=True,
                    max_length=128, return_tensors="pt",
                ).to(self.device)
                probabilities = self.torch.softmax(
                    self.model(**encoded).logits, dim=-1
                ).cpu().tolist()
                for source, values in zip(
                    lines[start:start + self.batch_size], probabilities
                ):
                    probs = {
                        label: round(float(values[i]), 4)
                        for i, label in enumerate(LABELS)
                    }
                    result = dict(source)
                    result["probabilities"] = probs
                    result["label"] = max(probs, key=probs.get)
                    output.append(result)
        return output


def _top(rows: list[dict], label: str, threshold: float) -> dict | None:
    if not rows:
        return None
    best = max(rows, key=lambda row: row["probabilities"].get(label, 0))
    return best if best["probabilities"].get(label, 0) >= threshold else None


def clean_product_name(text: str, modules: dict[str, Any]) -> str:
    text = re.sub(r'^(?:品\s*名|產品名稱|商品名稱|品項名稱|名\s*稱|^名)\s*[:：]?', '', text.strip())
    text = text.strip(" :：·、,，.。／/|-")
    cut = modules["product_name"].STOP.search(text)
    if cut:
        text = text[:cut.start()].strip(" :：·、,，.。／/|-")
    return text


def parse_nutrition_rows(rows: list[dict], parser: Any) -> dict:
    """Never let a parser consume numbers from the next nutrient's row."""
    candidates: dict[str, list[tuple]] = {}
    for row in rows:
        for key, values in parser(row["text"]).items():
            candidates.setdefault(key, []).append(tuple(values))
    return {
        key: values[0] for key, values in candidates.items()
        if len(set(values)) == 1
    }


def assemble(case: dict, rows: list[dict], modules: dict[str, Any], threshold: float) -> dict:
    all_texts = [row["text"] for row in rows]
    joined = "\n".join(all_texts)
    ingredients_rows = choose_ingredient_lines(rows, threshold)
    ingredients_raw = "\n".join(row["text"] for row in ingredients_rows).strip() or None
    raw_tokens = split_ingredients(ingredients_raw or "")
    tfda_lex, tfda_by_len = load_tfda_lexicon()
    # Dictionary matches are suggestions, not evidence that the ingredient exists.
    ingredients = raw_tokens
    correction_suggestions = [
        {"ocr": tok, "suggested": corrected}
        for tok in raw_tokens
        if (corrected := correct_ingredient(tok, tfda_lex, tfda_by_len)) != tok
    ]

    # Product name: prefer anchored extraction cleaned of prefix, with fallback to longest BERT candidate.
    # Always prioritize Traditional Chinese candidates over English/alphanumeric lines.
    anchored_name = modules["product_name"].extract(all_texts)
    name = clean_product_name(anchored_name, modules) if anchored_name else None

    name_candidates = [
        row for row in rows
        if row["label"] == "品名" and row["probabilities"].get("品名", 0) >= threshold
    ]
    if not name and name_candidates:
        valid_cands = []
        for row in name_candidates:
            cleaned = clean_product_name(row["text"], modules)
            if 2 <= len(cleaned) <= 40:
                valid_cands.append(cleaned)
        if valid_cands:
            # Prioritize candidates with Chinese characters, then by length descending
            valid_cands.sort(
                key=lambda s: (any('\u4e00' <= c <= '\u9fff' for c in s), len(s)),
                reverse=True,
            )
            name = valid_cands[0]
    elif name and name_candidates:
        norm_name = normalize_evidence(name)
        for row in name_candidates:
            cleaned = clean_product_name(row["text"], modules)
            norm_cleaned = normalize_evidence(cleaned)
            if norm_name != norm_cleaned and norm_name in norm_cleaned and 2 <= len(cleaned) <= 40:
                name = cleaned
                break

    raw_mfg = modules["manufacturer"].extract_detail(all_texts)
    manufacturer = modules["manufacturer"].merge([raw_mfg])
    if manufacturer:
        if re.search(r"同一工廠|本廠亦|生產線|過敏原|設備|管線", manufacturer):
            manufacturer = None
        elif not re.search(r"公司|企業|商行|食品|實業|股份|行|社|廠", manufacturer):
            manufacturer = None
    allergy = modules["allergy"].extract(all_texts)

    nutrition_rows = [
        row for row in rows
        if row["probabilities"].get("營養", 0) >= threshold
        or re.search(r"熱量|蛋白質|飽和脂肪|反式脂肪|碳水化合物|每一份量|營養標示", row["text"])
    ]
    nutrition_text = "\n".join(row["text"] for row in nutrition_rows)
    nutrient_names = set(re.findall(
        r"熱量|蛋白質|脂肪|飽和脂肪|反式脂肪|碳水化合物|糖|膳食纖維|鈉",
        nutrition_text,
    ))
    raw_parsed = parse_nutrition_rows(nutrition_rows, modules["nutrition"].parse) if len(nutrient_names) >= 3 else {}

    # Conservative fail-closed nutrition validation:
    # A genuine Taiwanese food nutrition table must have 'calories' (熱量) as row 1
    # and at least 3 valid core nutrients. Stray fragments or misaligned lines remain null.
    core_nutrients = ("calories", "protein", "fat", "carbohydrates", "sugar", "sodium")
    valid_core = [
        k for k in core_nutrients
        if k in raw_parsed and (
            (raw_parsed[k][0] is not None and raw_parsed[k][0] >= 0)
            or (len(raw_parsed[k]) > 1 and raw_parsed[k][1] is not None and raw_parsed[k][1] >= 0)
        )
    ]
    if "calories" not in valid_core or len(valid_core) < 3:
        parsed = {}
    else:
        parsed = dict(raw_parsed)
        if "trans_fat" in parsed:
            v1, v2 = (tuple(parsed["trans_fat"]) + (None,))[:2]
            v1 = None if (v1 is not None and v1 > 10.0) else v1
            v2 = None if (v2 is not None and v2 > 10.0) else v2
            parsed["trans_fat"] = (v1, v2)

    per_serving = {key: None for key in NUTRIENT_FIELDS}
    per_100 = {key: None for key in NUTRIENT_FIELDS}
    only_100 = bool(re.search(r"每\s*100", nutrition_text)) and not bool(
        re.search(r"每\s*(?:一)?份", nutrition_text)
    )
    has_100 = bool(re.search(r"每\s*100", nutrition_text))
    has_serving = bool(re.search(r"每\s*(?:一)?份(?!量)", nutrition_text))
    has_dv = bool(re.search(r"每日[參参]考|百分比", nutrition_text))
    for key, pair in parsed.items():
        if key not in per_serving:
            continue
        first, second = (tuple(pair) + (None,))[:2]
        if only_100:
            per_100[key] = first
        else:
            per_serving[key] = first if has_serving else None
            per_100[key] = second if has_100 and has_serving and not has_dv else None
    if not re.search(r"膳食纖維", nutrition_text):
        per_serving["fiber"] = per_100["fiber"] = None

    serving_size = None
    match = re.search(r"每\s*(?:一)?份量\D{0,5}(\d+(?:\.\d+)?)", joined)
    if match:
        serving_size = float(match.group(1))
    servings = None
    match = re.search(r"本包裝含\D{0,5}(\d+(?:\.\d+)?)\s*份", joined)
    if match:
        servings = float(match.group(1))

    got_any = bool(ingredients) or any(value is not None for value in per_serving.values()) \
        or any(value is not None for value in per_100.values())
    payload = FoodLabel(
        is_food_label=got_any,
        reject_reason=None if got_any else "未讀到成分或營養標示",
        name=name,
        brand=None,
        ingredients_raw=ingredients_raw,
        ingredients_list=ingredients,
        nutrition=Nutrition(**per_100),
        serving_size=serving_size,
        servings_per_container=servings,
        nutrition_per_serving=Nutrition(**per_serving),
        manufacturer=manufacturer,
        allergy_warning=allergy,
        certification_marks=[],
    ).model_dump()

    normalized_full = normalize_evidence(joined)
    warnings = []
    if correction_suggestions:
        warnings.append("dictionary corrections require review; original OCR ingredients retained")
    if not got_any:
        for field in ("name", "brand", "ingredients_raw", "manufacturer", "allergy_warning", "serving_size", "servings_per_container"):
            payload[field] = None
        payload["ingredients_list"] = []
    for field in ("name", "manufacturer", "allergy_warning"):
        value = payload[field]
        if value and normalize_evidence(value) not in normalized_full:
            warnings.append(f"{field}: value is not a verbatim OCR span")
            payload[field] = None
    return {
        **payload,
        "_meta": {
            "pipeline": "easyocr -> ckiplab BERT -> deterministic parsers -> pydantic",
            "case_id": case["case_id"],
            "bert_threshold": threshold,
            "warnings": warnings,
            "ingredient_correction_suggestions": correction_suggestions,
            "ingredients_raw_format": "verbatim OCR lines joined with newline; not a verified transcription",
            "evidence": {
                "ingredients_line_ids": [row["line_id"] for row in ingredients_rows],
                "nutrition_line_ids": [row["line_id"] for row in nutrition_rows],
            },
            "line_classification": [
                {
                    "line_id": row["line_id"], "text": row["text"],
                    "label": row["label"],
                    "confidence": row["probabilities"][row["label"]],
                }
                for row in rows
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--threshold", type=float, default=0.35)
    parser.add_argument("--ppocr-test", type=Path, default=Path("/workspace/PPOCR_TEST"))
    args = parser.parse_args()

    sys.path.insert(0, str(args.ppocr_test))
    import allergy
    import manufacturer
    import nutrition_pipeline
    import product_name

    modules = {
        "allergy": allergy, "manufacturer": manufacturer,
        "nutrition": nutrition_pipeline, "product_name": product_name,
    }
    classifier = BertClassifier(args.model_dir, args.device, args.batch_size)
    files = sorted(args.ocr_dir.glob("*.json"))
    files = [path for path in files if path.name != "_summary.json"]
    if args.case:
        files = [path for path in files if any(path.stem.startswith(x) for x in args.case)]
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    completed = failed = 0
    for path in files:
        case = json.loads(path.read_text(encoding="utf-8"))
        lines = []
        for image_index, image in enumerate(case.get("images") or []):
            for line_index, line in enumerate(image.get("lines") or []):
                text = (line.get("text") or "").strip()
                if len(normalize_evidence(text)) < 2:
                    continue
                lines.append({
                    "line_id": f"i{image_index}:l{line_index}", "text": text,
                    "image_id": image_index,
                    "score": line.get("score"), "box": line.get("box"),
                })
        try:
            classified = classifier.predict(lines) if lines else []
            result = assemble(case, classified, modules, args.threshold)
            completed += 1
        except Exception as exc:
            failed += 1
            result = {"case_id": case.get("case_id"), "error": f"{type(exc).__name__}: {exc}"}
        (args.output / path.name).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    summary = {
        "pipeline": "easyocr -> ckiplab BERT -> deterministic JSON",
        "cases_requested": len(files), "cases_completed": completed,
        "failed": failed, "elapsed_s": round(time.perf_counter() - started, 3),
        "evaluation_warning": "integration smoke only; supplied BERT checkpoint is not out-of-fold",
    }
    (args.output / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
