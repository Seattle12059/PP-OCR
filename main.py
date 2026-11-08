#!/usr/bin/env python3
"""PP-OCRv6 Tiny 本地 OCR 命令行工具。

默认使用 ONNX Runtime 引擎（Mac 上最快），模型文件位于本项目的 models/ 目录。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent
MODELS_DIR = PROJECT_DIR / "models"


def default_model_dirs(engine: str) -> tuple[Path, Path]:
    if engine == "onnxruntime":
        return (
            MODELS_DIR / "PP-OCRv6_tiny_det_onnx",
            MODELS_DIR / "PP-OCRv6_tiny_rec_onnx",
        )
    return (
        MODELS_DIR / "PP-OCRv6_tiny_det",
        MODELS_DIR / "PP-OCRv6_tiny_rec",
    )


# 方向分类模型只有 Paddle 格式，所以 --orient 时强制走 paddle 引擎。
ORIENT_MODELS_DIR = MODELS_DIR / "official_models"
DOC_ORI_MODEL = ORIENT_MODELS_DIR / "PP-LCNet_x1_0_doc_ori"
TEXTLINE_ORI_MODEL = ORIENT_MODELS_DIR / "PP-LCNet_x1_0_textline_ori"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr",
        description="PP-OCRv6 Tiny 本地文字识别",
    )
    parser.add_argument("images", nargs="+", help="图片文件路径")
    parser.add_argument(
        "--engine",
        choices=["onnxruntime", "paddle"],
        default="onnxruntime",
        help="推理引擎（默认 onnxruntime，Mac 上最快；paddle 需要额外安装 paddlepaddle）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument(
        "--boxes",
        action="store_true",
        help="在 JSON 中输出坐标（行级四边形/矩形 + 字级/词级框）",
    )
    parser.add_argument(
        "--orient",
        action="store_true",
        help="启用方向分类（修正倒置/竖排页面），会强制使用 paddle 引擎，耗时约 +30%%",
    )
    parser.add_argument("--time", action="store_true", help="输出每张图的识别耗时")
    parser.add_argument(
        "--save-viz",
        metavar="DIR",
        help="把带检测框的标注图保存到指定目录",
    )
    return parser


def extract_result(res: Any) -> dict[str, Any]:
    if hasattr(res, "json"):
        data = res.json
    elif hasattr(res, "res"):
        data = res.res
    else:
        data = res
    if isinstance(data, dict) and "res" in data and isinstance(data["res"], dict):
        data = data["res"]
    return data


def predict(
    ocr: Any,
    image: str,
    save_viz: str | None,
    with_boxes: bool = False,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if with_boxes:
        kwargs["return_word_box"] = True
    results = ocr.predict(image, **kwargs)
    out: dict[str, Any] = {"image": image, "texts": [], "scores": []}
    if with_boxes:
        out["lines"] = []
    for res in results:
        data = extract_result(res)
        texts = data.get("rec_texts") or data.get("rec_text") or []
        scores = data.get("rec_scores") or data.get("rec_score") or []
        if isinstance(texts, str):
            texts = [texts]
        if isinstance(scores, (int, float)):
            scores = [scores]
        out["texts"].extend(texts)
        out["scores"].extend(scores)
        if with_boxes:
            out["lines"].extend(build_lines(data))
        if save_viz and hasattr(res, "save_to_img"):
            res.save_to_img(save_viz)
    return out


def _as_box(box: Any) -> list[float] | None:
    """把 [x1,y1,x2,y2] 或 [[x,y],...] 统一成外接矩形 [x1,y1,x2,y2]。"""
    if box is None:
        return None
    box = [list(p) for p in box] if not isinstance(box[0], (int, float)) else box
    if not box:
        return None
    if isinstance(box[0], (int, float)):
        if len(box) == 4:
            return [float(box[0]), float(box[1]), float(box[2]), float(box[3])]
        return None
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return [min(xs), min(ys), max(xs), max(ys)]


def build_lines(data: dict[str, Any]) -> list[dict[str, Any]]:
    """把单页 OCR 结果整理成行 + 段（中文逐字 / 英文逐词）结构。"""
    texts = data.get("rec_texts") or []
    scores = data.get("rec_scores") or []
    polys = data.get("rec_polys") or data.get("dt_polys") or []
    rects = data.get("rec_boxes") or []
    words = data.get("text_word") or []
    word_boxes = data.get("text_word_boxes") or []
    angles = data.get("textline_orientation_angles") or []

    lines: list[dict[str, Any]] = []
    for i, text in enumerate(texts):
        poly = polys[i] if i < len(polys) else None
        rect = _as_box(rects[i]) if i < len(rects) else _as_box(poly)
        if rect is None:
            continue
        segments: list[dict[str, Any]] = []
        tokens = words[i] if i < len(words) else []
        tboxes = word_boxes[i] if i < len(word_boxes) else []
        if tokens and len(tokens) == len(tboxes):
            for tok, tb in zip(tokens, tboxes, strict=False):
                if not str(tok).strip():
                    continue  # 空格不参与定位
                seg = _as_box(tb)
                if seg:
                    segments.append({"t": str(tok), "box": seg})

        line: dict[str, Any] = {
            "text": text,
            "score": float(scores[i]) if i < len(scores) else None,
            "box": rect,
            "poly": [[float(p[0]), float(p[1])] for p in poly] if poly else None,
            "angle": float(angles[i]) if i < len(angles) and _is_num(angles[i]) else 0.0,
            "segments": segments,
        }
        lines.append(line)
    return lines


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def main() -> int:
    args = build_parser().parse_args()

    # 方向分类模型只有 Paddle 格式：开了 --orient 就必须走 paddle 引擎。
    engine = "paddle" if args.orient else args.engine
    det_dir, rec_dir = default_model_dirs(engine)
    missing = [p for p in (det_dir, rec_dir) if not p.exists()]
    if args.orient:
        missing += [
            p for p in (DOC_ORI_MODEL, TEXTLINE_ORI_MODEL) if not p.exists()
        ]
    if missing:
        print(
            "模型目录不存在，请先下载模型：",
            ", ".join(str(p) for p in missing),
            file=sys.stderr,
        )
        return 2

    try:
        from paddleocr import PaddleOCR
    except ImportError:
        print("缺少依赖，请先在 ocr/ 目录运行: uv sync", file=sys.stderr)
        return 2

    init_start = time.perf_counter()
    try:
        ocr = PaddleOCR(
            text_detection_model_name="PP-OCRv6_tiny_det",
            text_detection_model_dir=str(det_dir),
            text_recognition_model_name="PP-OCRv6_tiny_rec",
            text_recognition_model_dir=str(rec_dir),
            doc_orientation_classify_model_name="PP-LCNet_x1_0_doc_ori",
            doc_orientation_classify_model_dir=str(DOC_ORI_MODEL),
            textline_orientation_model_name="PP-LCNet_x1_0_textline_ori",
            textline_orientation_model_dir=str(TEXTLINE_ORI_MODEL),
            engine=engine,
            use_doc_orientation_classify=args.orient,
            use_doc_unwarping=False,
            use_textline_orientation=args.orient,
            device="cpu",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"初始化 OCR 失败：{exc}", file=sys.stderr)
        if engine == "paddle":
            print(
                "提示：paddle 引擎需要额外安装 paddlepaddle，例如 "
                "uv pip install --python .venv/bin/python paddlepaddle",
                file=sys.stderr,
            )
        return 1
    init_elapsed = time.perf_counter() - init_start

    outputs: list[dict[str, Any]] = []
    for image in args.images:
        if not Path(image).is_file():
            print(f"文件不存在: {image}", file=sys.stderr)
            return 2
        start = time.perf_counter()
        out = predict(ocr, image, args.save_viz, with_boxes=args.boxes)
        out["elapsed_sec"] = round(time.perf_counter() - start, 3)
        outputs.append(out)

    if args.json:
        payload = {
            "engine": engine,
            "init_elapsed_sec": round(init_elapsed, 3),
            "results": outputs,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for out in outputs:
            print(f"\n{out['image']}")
            if not out["texts"]:
                print("  (未识别到文字)")
            for text, score in zip(out["texts"], out["scores"], strict=False):
                conf = f" ({score:.3f})" if isinstance(score, (int, float)) else ""
                print(f"  {text}{conf}")
            if args.time:
                print(f"  耗时: {out['elapsed_sec']:.3f}s")

    return 0


def run() -> None:
    """CLI 入口：跳过 onnxruntime 的 C++ 析构。

    1DS telemetry 的 handleStop 在解释器退出时会死循环，直接把进程卡死。
    """
    # Windows 控制台默认不是 UTF-8，中文输出会报 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    run()
