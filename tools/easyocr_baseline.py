#!/usr/bin/env python3
"""Run a reproducible EasyOCR baseline on FoodScanIoT evaluation cases."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def resolve_image(image_root: Path, relative: str) -> Path:
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


def load_cases(path: Path, selectors: list[str], limit: int) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    if selectors:
        cases = [
            case
            for case in cases
            if any(case["case_id"].startswith(selector) for selector in selectors)
        ]
    return cases[:limit] if limit else cases


def select_device(requested: str, cuda_available: bool) -> str:
    if requested == "cuda" and not cuda_available:
        raise RuntimeError("CUDA was requested but is not available in this container")
    if requested == "auto":
        return "cuda" if cuda_available else "cpu"
    return requested


def normalize_results(results: list) -> list[dict]:
    lines = []
    for box, text, confidence in results:
        normalized_box = [
            [round(float(point[0])), round(float(point[1]))] for point in box
        ]
        lines.append(
            {
                "text": str(text),
                "score": round(float(confidence), 4),
                "box": normalized_box,
            }
        )
    return sorted(
        lines,
        key=lambda line: (
            min(point[1] for point in line["box"]),
            min(point[0] for point in line["box"]),
        ),
    )


def run(args: argparse.Namespace) -> dict:
    import easyocr
    import torch

    device = select_device(args.device, torch.cuda.is_available())
    args.output.mkdir(parents=True, exist_ok=True)
    cases = load_cases(args.cases, args.case, args.limit)
    reader = easyocr.Reader(
        args.language,
        gpu=device == "cuda",
        model_storage_directory=str(args.model_dir),
        download_enabled=False,
        quantize=device == "cpu",
        cudnn_benchmark=False,
        verbose=False,
    )
    summary = {
        "engine": "easyocr",
        "version": easyocr.__version__,
        "language": args.language,
        "device": device,
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
                started = time.perf_counter()
                raw = reader.readtext(
                    str(source),
                    detail=1,
                    paragraph=False,
                    decoder="greedy",
                    batch_size=args.batch_size,
                    workers=0,
                    canvas_size=args.max_side,
                    mag_ratio=1.0,
                )
                if device == "cuda":
                    torch.cuda.synchronize()
                elapsed = time.perf_counter() - started
                lines = normalize_results(raw)
            except Exception as exc:  # keep the rest of a benchmark run observable
                summary["failed"].append(
                    {"image": str(source), "error": f"{type(exc).__name__}: {exc}"}
                )
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
            "preset": f"easyocr_{device}",
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
    parser.add_argument("--model-dir", type=Path, default=Path("/opt/easyocr-models"))
    parser.add_argument("--case", action="append", default=[], help="case-id prefix; repeatable")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--language", nargs="+", default=["ch_tra", "en"])
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--max-side", type=int, default=2400)
    return parser


if __name__ == "__main__":
    report = run(build_parser().parse_args())
    print(json.dumps(report, ensure_ascii=False, indent=2))
