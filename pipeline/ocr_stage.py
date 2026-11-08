"""阶段 ②：多进程并行 OCR，每个 worker 常驻一个模型实例。

使用 PP-OCRv6 Tiny：默认 ONNX Runtime，开 --orient 时切到 Paddle。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
OFFICIAL_MODELS = MODELS_DIR / "official_models"

_ENGINE: Any = None
_ORIENT: bool = False


def _box(value: Any) -> list[float] | None:
    """[x1,y1,x2,y2] 或 [[x,y],...] → 外接矩形。"""
    if value is None:
        return None
    try:
        pts = list(value)
    except TypeError:
        return None
    if not pts:
        return None
    if isinstance(pts[0], (int, float)):
        if len(pts) >= 4:
            return [float(pts[0]), float(pts[1]), float(pts[2]), float(pts[3])]
        return None
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _poly(value: Any) -> list[list[float]] | None:
    if value is None:
        return None
    try:
        return [[float(p[0]), float(p[1])] for p in value]
    except (TypeError, IndexError, ValueError):
        return None


def init_worker(orient: bool) -> None:
    """进程池 initializer：每个进程只加载一次模型。"""
    global _ENGINE, _ORIENT
    if _ENGINE is not None:
        return

    _ORIENT = orient

    # 静默掉 paddle 的无关日志，避免污染进度输出。
    os.environ.setdefault("GLOG_minloglevel", "2")
    os.environ.setdefault("FLAGS_call_stack_level", "0")
    # 模型全部来自本地 models/，跳过联网可用性探测（每次约数秒，离线时更久）。
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

    from paddleocr import PaddleOCR

    M = MODELS_DIR
    # 方向分类模型只有 Paddle 格式，开 --orient 时切到 paddle 引擎。
    use_paddle = orient
    suffix = "" if use_paddle else "_onnx"
    _ENGINE = PaddleOCR(
        text_detection_model_name="PP-OCRv6_tiny_det",
        text_detection_model_dir=str(M / f"PP-OCRv6_tiny_det{suffix}"),
        text_recognition_model_name="PP-OCRv6_tiny_rec",
        text_recognition_model_dir=str(M / f"PP-OCRv6_tiny_rec{suffix}"),
        doc_orientation_classify_model_name="PP-LCNet_x1_0_doc_ori",
        doc_orientation_classify_model_dir=str(OFFICIAL_MODELS / "PP-LCNet_x1_0_doc_ori"),
        textline_orientation_model_name="PP-LCNet_x1_0_textline_ori",
        textline_orientation_model_dir=str(
            OFFICIAL_MODELS / "PP-LCNet_x1_0_textline_ori"
        ),
        engine="paddle" if use_paddle else "onnxruntime",
        use_doc_orientation_classify=orient,
        use_doc_unwarping=False,
        use_textline_orientation=orient,
        device="cpu",
    )


def _json_of(res: Any) -> dict[str, Any]:
    data = getattr(res, "json", None)
    if data is None:
        data = getattr(res, "res", None)
    if data is None:
        data = res
    if isinstance(data, dict) and isinstance(data.get("res"), dict):
        data = data["res"]
    return data


def _lines_from_ocr(data: dict[str, Any]) -> list[dict[str, Any]]:
    texts = data.get("rec_texts") or []
    scores = data.get("rec_scores") or []
    polys = data.get("rec_polys") or data.get("dt_polys") or []
    rects = data.get("rec_boxes") or []
    words = data.get("text_word") or []
    word_boxes = data.get("text_word_boxes") or []
    angles = data.get("textline_orientation_angles") or []

    lines: list[dict[str, Any]] = []
    for i, text in enumerate(texts):
        poly = _poly(polys[i]) if i < len(polys) else None
        rect = _box(rects[i]) if i < len(rects) else None
        if rect is None:
            rect = _box(poly)
        if rect is None:
            continue

        segments: list[dict[str, Any]] = []
        tokens = words[i] if i < len(words) else []
        tboxes = word_boxes[i] if i < len(word_boxes) else []
        if tokens and len(tokens) == len(tboxes):
            for tok, tb in zip(tokens, tboxes, strict=False):
                if not str(tok).strip():
                    continue
                seg = _box(tb)
                if seg:
                    segments.append({"t": str(tok), "box": seg})

        angle = angles[i] if i < len(angles) else 0.0
        lines.append(
            {
                "text": text,
                "score": float(scores[i]) if i < len(scores) else None,
                "box": rect,
                "poly": poly,
                "angle": float(angle)
                if isinstance(angle, (int, float)) and angle > -1
                else 0.0,
                "segments": segments,
            }
        )
    return lines


def ocr_page(page: dict[str, Any]) -> dict[str, Any]:
    """对一页做 OCR，返回带坐标的行数据。"""
    image = page["image"]
    out: dict[str, Any] = {
        "page": page["page"],
        "image": image,
        "width": page["width"],
        "height": page["height"],
        "orient": _ORIENT,
    }

    res = _ENGINE.predict(image, return_word_box=True)[0]
    out["lines"] = _lines_from_ocr(_json_of(res))
    return out


def check_models(orient: bool) -> list[str]:
    """检查模型目录是否齐全，返回缺失的路径。"""
    suffix = "" if orient else "_onnx"
    required = [
        MODELS_DIR / f"PP-OCRv6_tiny_det{suffix}",
        MODELS_DIR / f"PP-OCRv6_tiny_rec{suffix}",
    ]
    if orient:
        required += [
            OFFICIAL_MODELS / "PP-LCNet_x1_0_doc_ori",
            OFFICIAL_MODELS / "PP-LCNet_x1_0_textline_ori",
        ]
    return [str(p) for p in required if not p.is_dir()]


def default_jobs() -> int:
    cpu = os.cpu_count() or 4
    return max(1, min(8, cpu))


def configure_threads() -> None:
    """多进程 + 每进程单线程，避免线程池互相抢核。"""
    for key in ("OMP_NUM_THREADS", "ORT_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(key, "1")
