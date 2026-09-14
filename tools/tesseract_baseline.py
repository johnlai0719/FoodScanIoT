#!/usr/bin/env python3
"""Run a reproducible Tesseract OCR baseline on FoodScanIoT evaluation cases.

The output mirrors the line-level shape produced by ``PPOCR_TEST/run_baseline.py``
so later experiments can reuse the existing region and parsing tools. This script
measures the reader only; it does not claim production readiness.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageOps


def parse_tsv(tsv: str) -> list[dict]:
    """Convert Tesseract word TSV into ordered line records."""
    groups: dict[tuple[int, int, int, int], list[dict]] = defaultdict(list)
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        text = (row.get("text") or "").strip()
        if not text or row.get("level") != "5":
            continue
        try:
            confidence = float(row["conf"])
            left, top = int(row["left"]), int(row["top"])
            width, height = int(row["width"]), int(row["height"])
            key = tuple(
                int(row[name])
                for name in ("page_num", "block_num", "par_num", "line_num")
            )
        except (KeyError, TypeError, ValueError):
            continue
        groups[key].append(
            {
                "text": text,
                "confidence": confidence,
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
            }
        )

    lines = []
    for key in sorted(groups):
        words = sorted(groups[key], key=lambda word: (word["left"], word["top"]))
        left = min(word["left"] for word in words)
        top = min(word["top"] for word in words)
        right = max(word["right"] for word in words)
        bottom = max(word["bottom"] for word in words)
        valid = [word["confidence"] for word in words if word["confidence"] >= 0]
        lines.append(
            {
                "text": " ".join(word["text"] for word in words),
                "score": round(sum(valid) / len(valid) / 100, 4) if valid else None,
                "box": [[left, top], [right, top], [right, bottom], [left, bottom]],
            }
        )
    return lines


def resolve_image(image_root: Path, relative: str) -> Path:
    """Resolve roots both above and below the manifest's ``images/`` folder."""
    parts = [part for part in relative.replace("\\", "/").split("/") if part]
    candidates = [image_root.joinpath(*parts)]
    if parts and parts[0].lower() == "images":
        candidates.append(image_root.joinpath(*parts[1:]))
    for candidate in candidates:
        if candidate.exists():
            return candidate
        jpeg = candidate.with_suffix(".jpg")
        if jpeg.exists():
            return jpeg
    return candidates[0]


def prepare_image(source: Path, destination: Path, preprocess: str, max_side: int) -> None:
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        if max_side > 0 and max(image.size) > max_side:
            scale = max_side / max(image.size)
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
        if preprocess == "gray":
            image = ImageOps.autocontrast(ImageOps.grayscale(image))
        image.save(destination, format="PNG")


def recognize(
    image_path: Path,
    *,
    language: str,
    psm: int,
    preprocess: str,
    max_side: int,
) -> tuple[list[dict], float]:
    with tempfile.TemporaryDirectory(prefix="foodscan-tesseract-") as temp_dir:
        prepared = Path(temp_dir) / "input.png"
        prepare_image(image_path, prepared, preprocess, max_side)
        command = [
            "tesseract",
            str(prepared),
            "stdout",
            "-l",
            language,
            "--oem",
            "1",
            "--psm",
            str(psm),
            "tsv",
        ]
        started = time.perf_counter()
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        elapsed = time.perf_counter() - started
    return parse_tsv(completed.stdout), elapsed


def load_cases(path: Path, selectors: list[str], limit: int) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    if selectors:
        cases = [
            case
            for case in cases
            if any(case["case_id"].startswith(selector) for selector in selectors)
        ]
    return cases[:limit] if limit else cases


def run(args: argparse.Namespace) -> dict:
    args.output.mkdir(parents=True, exist_ok=True)
    cases = load_cases(args.cases, args.case, args.limit)
    summary = {
        "engine": "tesseract",
        "language": args.language,
        "psm": args.psm,
        "preprocess": args.preprocess,
        "max_side": args.max_side,
        "cases_requested": len(cases),
        "cases_completed": 0,
        "images_completed": 0,
        "missing": [],
        "failed": [],
        "elapsed_s": 0.0,
    }
    total_started = time.perf_counter()

    for case in cases:
        images = []
        case_failed = False
        for relative in case.get("images", []):
            source = resolve_image(args.image_root, relative)
            if not source.exists():
                summary["missing"].append(str(source))
                case_failed = True
                continue
            try:
                lines, elapsed = recognize(
                    source,
                    language=args.language,
                    psm=args.psm,
                    preprocess=args.preprocess,
                    max_side=args.max_side,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                summary["failed"].append({"image": str(source), "error": str(exc)})
                case_failed = True
                continue
            images.append(
                {
                    "path": relative,
                    "source": str(source),
                    "elapsed_s": round(elapsed, 3),
                    "n_lines": len(lines),
                    "lines": lines,
                }
            )
            summary["images_completed"] += 1

        result = {
            "case_id": case["case_id"],
            "preset": "tesseract_basic",
            "images": images,
            "error": "one or more images failed" if case_failed else None,
        }
        (args.output / f"{case['case_id']}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if images and not case_failed:
            summary["cases_completed"] += 1

    summary["elapsed_s"] = round(time.perf_counter() - total_started, 3)
    (args.output / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--image-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[], help="case-id prefix; repeatable")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--language", default="chi_tra+eng")
    parser.add_argument("--psm", type=int, default=11, help="11=sparse text")
    parser.add_argument("--preprocess", choices=("none", "gray"), default="none")
    parser.add_argument("--max-side", type=int, default=2400)
    return parser


if __name__ == "__main__":
    report = run(build_parser().parse_args())
    print(json.dumps(report, ensure_ascii=False, indent=2))
