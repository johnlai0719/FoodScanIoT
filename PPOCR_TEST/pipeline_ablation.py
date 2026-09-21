#!/usr/bin/env python3
"""Run a reproducible, paired ablation over cached FoodScanIoT outputs.

The script deliberately reuses ``boot_compare.per_case`` so every stage is
scored with the same ground truth, additive dictionary, normalization, and
matching rules.  It never invokes an OCR/VLM service.

Default stages isolate the decisions for which complete cached outputs
currently exist on evaluation set v4.0:

1. full-page PP-OCR + rule parser;
2. the same PP-OCR applied to VLCrop regions + the same rule parser;
3. the same crops reread by PaddleOCR-VL;
4. the same crops reread by HunyuanOCR, isolating the local reader choice;
5. the HunyuanOCR crop output + the experimental BERT item splitter.

Usage:
    python pipeline_ablation.py
    python pipeline_ablation.py --n 10000 --out-dir out/pipeline_ablation
    python pipeline_ablation.py \
      --stage "baseline=v6_best:boxsep" \
      --stage "crop=vlcrop_hy_v4:boxsep"
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import boot_compare as BC  # noqa: E402
import score_ocr as S  # noqa: E402
import sim_match as SM  # noqa: E402


DEFAULT_STAGES = (
    ("S0 整頁 PP-OCR＋規則解析", "v6_best:boxsep"),
    ("S1 VLCrop＋PP-OCR＋相同規則解析", "ppocr_vlcrop_v4_all:boxsep"),
    ("S2 VLCrop＋PaddleOCR-VL＋相同規則解析", "vlcrop_pvl_v4:boxsep"),
    ("S3 VLCrop＋HunyuanOCR＋相同規則解析", "vlcrop_hy_v4:boxsep"),
    ("S4 HunyuanOCR 裁切精讀＋BERT 候選切分", "bertsplit_v4:items"),
)


@dataclass(frozen=True)
class Stage:
    label: str
    spec: str
    rows: dict[str, tuple[int, int, int]]


def _parse_stage(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("stage 必須是 LABEL=PRESET:HOW")
    label, spec = value.split("=", 1)
    if not label.strip() or ":" not in spec:
        raise argparse.ArgumentTypeError("stage 必須是 LABEL=PRESET:HOW")
    return label.strip(), spec.strip()


def _counts(rows: list[tuple[int, int, int]]) -> dict[str, float | int]:
    hit = sum(row[0] for row in rows)
    truth = sum(row[1] for row in rows)
    predicted = sum(row[2] for row in rows)
    recall = hit / truth if truth else 0.0
    precision = hit / predicted if predicted else 0.0
    f1 = 2 * recall * precision / (recall + precision) if recall + precision else 0.0
    return {
        "hit": hit,
        "truth": truth,
        "predicted": predicted,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _paired_ci(
    before: Stage,
    after: Stage,
    case_ids: list[str],
    n: int,
    seed: int,
) -> tuple[float, float, float]:
    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(n):
        sample = [case_ids[rng.randrange(len(case_ids))] for _ in case_ids]
        a = BC.f1_of([before.rows[cid] for cid in sample])
        b = BC.f1_of([after.rows[cid] for cid in sample])
        diffs.append((b - a) * 100)
    diffs.sort()
    lo = diffs[int(0.025 * len(diffs))]
    hi = diffs[min(int(0.975 * len(diffs)), len(diffs) - 1)]
    win = sum(diff > 0 for diff in diffs) / len(diffs)
    return lo, hi, win


def _git_snapshot() -> dict[str, str | bool | None]:
    repo = Path(__file__).resolve().parents[1]

    def run(*args: str) -> str | None:
        try:
            return subprocess.check_output(
                ["git", "-C", str(repo), *args],
                text=True,
                encoding="utf-8",
                errors="replace",
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    status = run("status", "--porcelain")
    return {
        "repository": str(repo),
        "branch": run("branch", "--show-current"),
        "commit": run("rev-parse", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


def _markdown(report: dict) -> str:
    lines = [
        "# 管線消融重評結果",
        "",
        f"- 產生時間（UTC）：{report['generated_at']}",
        f"- Git：`{report['snapshot'].get('branch')}` / "
        f"`{str(report['snapshot'].get('commit') or '')[:7]}`"
        f"{'（工作目錄有未提交變更）' if report['snapshot'].get('dirty') else ''}",
        f"- 資料集登錄案例：{report['registered_cases']} 案",
        f"- {len(report['stages'])} 條件共同可評案例：{report['common_cases']} 案",
        f"- 配對 bootstrap：{report['bootstrap_samples']} 次，seed={report['seed']}",
        "",
        "| 階段 | 共同案例 | Precision | Recall | F1 | 相對前階段 ΔF1 | 95% CI |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in report["stages"]:
        delta = "—" if row["delta_f1"] is None else f"{row['delta_f1']:+.1f}"
        ci = "—" if row["ci95"] is None else (
            f"{row['ci95'][0]:+.1f} ～ {row['ci95'][1]:+.1f}"
        )
        lines.append(
            f"| {row['label']} | {row['cases']} | {row['precision'] * 100:.1f} | "
            f"{row['recall'] * 100:.1f} | {row['f1'] * 100:.1f} | {delta} | {ci} |"
        )
    lines.extend(
        [
            "",
            "> 數值是依目前程式中的正規化、添加物字典與比對規則，對既有快取輸出重新評分；",
            "> 不等同於歷史報告在舊版計分器下記錄的數值。資料集總案數與共同可評案數不得混用。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="以相同計分器重評既有輸出，產生配對管線消融表。"
    )
    parser.add_argument(
        "--stage",
        action="append",
        type=_parse_stage,
        metavar="LABEL=PRESET:HOW",
        help="自訂階段；可重複指定。未指定時使用三個預設階段。",
    )
    parser.add_argument("--boxes", default=BC.BOXES_DEFAULT)
    parser.add_argument("--n", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="若指定，寫出 report.json、report.csv、report.md。",
    )
    args = parser.parse_args()
    if args.n < 100:
        parser.error("--n 至少為 100")

    definitions = args.stage or list(DEFAULT_STAGES)
    adds, generic = SM.load_additives()
    stages = [
        Stage(label, spec, BC.per_case(spec, adds, generic, boxes=args.boxes))
        for label, spec in definitions
    ]
    if len(stages) < 2:
        parser.error("至少需要兩個 stage")

    common = sorted(set.intersection(*(set(stage.rows) for stage in stages)))
    if not common:
        parser.error("各階段沒有共同可評案例")

    # cases.json counts the registered evaluation cases; scoring can exclude
    # non-food or unavailable cases, so report both denominators explicitly.
    cases_path = os.path.join(S.EVAL_ROOT, "cases.json")
    with open(cases_path, encoding="utf-8") as handle:
        registered_cases = len(json.load(handle)["cases"])
    rows: list[dict] = []
    for index, stage in enumerate(stages):
        metrics = _counts([stage.rows[cid] for cid in common])
        row = {
            "index": index,
            "label": stage.label,
            "spec": stage.spec,
            "cases": len(common),
            **metrics,
            "delta_f1": None,
            "ci95": None,
            "bootstrap_win_rate": None,
        }
        if index:
            previous = stages[index - 1]
            previous_f1 = BC.f1_of([previous.rows[cid] for cid in common])
            row["delta_f1"] = (metrics["f1"] - previous_f1) * 100
            lo, hi, win = _paired_ci(previous, stage, common, args.n, args.seed)
            row["ci95"] = [lo, hi]
            row["bootstrap_win_rate"] = win
        rows.append(row)

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": _git_snapshot(),
        "registered_cases": registered_cases,
        "common_cases": len(common),
        "common_case_ids": common,
        "bootstrap_samples": args.n,
        "seed": args.seed,
        "boxes": args.boxes,
        "stages": rows,
    }
    markdown = _markdown(report)
    print(markdown)

    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (args.out_dir / "report.md").write_text(markdown, encoding="utf-8")
        with (args.out_dir / "report.csv").open(
            "w", newline="", encoding="utf-8-sig"
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=(
                    "index", "label", "spec", "cases", "hit", "truth", "predicted",
                    "precision", "recall", "f1", "delta_f1", "ci95",
                    "bootstrap_win_rate",
                ),
            )
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    main()
