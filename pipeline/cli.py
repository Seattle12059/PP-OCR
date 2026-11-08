"""pp-ocr：文档 → 页图 → OCR → 白底 HTML。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from . import convert, ocr_stage, render


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pp-ocr",
        description="把 PDF / Word / PPT / HTML / 图片转成可检索的白底 HTML",
    )
    p.add_argument("inputs", nargs="*", help="源文件路径（可多个）")
    p.add_argument(
        "--folder",
        nargs="+",
        metavar="DIR",
        help="批量模式：递归扫描目录里所有可转换的文件，每个文件输出到各自子目录",
    )
    p.add_argument("--out", default="ocr-out", help="输出目录（默认 ./ocr-out）")
    p.add_argument("--dpi", type=int, default=200, help="转图分辨率（默认 200）")
    p.add_argument(
        "--jobs",
        type=int,
        default=0,
        help="并行进程数（默认 min(8, CPU 核数)）",
    )
    p.add_argument(
        "--orient",
        action="store_true",
        help="启用方向分类，修正倒置/竖排页面",
    )
    p.add_argument(
        "--font-k",
        type=float,
        default=0.78,
        help="无中文行的字号兜底系数（默认 0.78）；有中文的行按字的间隔自动估字号",
    )
    p.add_argument("--mark-low", action="store_true", help="低置信度文字标红")
    p.add_argument(
        "--color",
        action="store_true",
        help="从原图采样文字颜色（彩色标题会被保留）",
    )
    p.add_argument("--keep-work", action="store_true", help="保留中间产物（页图/单页 JSON）")
    return p


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def output_base(name: str) -> str:
    """`报告.pdf` → `报告_pdf`。

    扩展名的点换成下划线，避免产物目录和原文件同名（在同一父目录下看起来
    像是同一个东西，也免得某些工具混淆）。
    """
    p = Path(name)
    if p.suffix:
        return f"{p.stem}_{p.suffix.lstrip('.')}"
    return name


def process_one(
    src: Path,
    job_dir: Path,
    args: argparse.Namespace,
    jobs: int,
) -> dict[str, Path]:
    try:
        return _process_one(src, job_dir, args, jobs)
    except Exception:
        # 失败时别留下半成品目录；--keep-work 时保留方便排查。
        if not args.keep_work:
            shutil.rmtree(job_dir, ignore_errors=True)
        raise


def _process_one(
    src: Path,
    job_dir: Path,
    args: argparse.Namespace,
    jobs: int,
) -> dict[str, Path]:
    # 产物目录/文件名沿用原文件名，扩展名的点换成下划线。
    base = output_base(src.name)
    if job_dir.exists():
        shutil.rmtree(job_dir)
    workdir = job_dir / "_work"
    workdir.mkdir(parents=True)

    t0 = time.perf_counter()
    _log(f"[1/3] 转图: {src.name}")
    pages = convert.build_pages(src, workdir, args.dpi)
    _log(f"      {len(pages)} 页，{time.perf_counter() - t0:.1f}s")

    manifest = {
        "source": str(src.resolve()),
        "source_type": src.suffix.lower().lstrip("."),
        "dpi": args.dpi,
        "orient": args.orient,
        "pages": pages,
    }
    (workdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    t1 = time.perf_counter()
    _log(f"[2/3] OCR ({jobs} 进程): {len(pages)} 页")
    results: dict[int, dict] = {}
    if jobs == 1 or len(pages) == 1:
        ocr_stage.init_worker(args.orient)
        for page in pages:
            results[page["page"]] = ocr_stage.ocr_page(page)
            _log(f"      page {page['page']}/{len(pages)}")
    else:
        with ProcessPoolExecutor(
            max_workers=jobs,
            initializer=ocr_stage.init_worker,
            initargs=(args.orient,),
        ) as pool:
            futures = {pool.submit(ocr_stage.ocr_page, p): p["page"] for p in pages}
            done = 0
            for fut in as_completed(futures):
                num = futures[fut]
                results[num] = fut.result()
                done += 1
                _log(f"      page {num}/{len(pages)}  ({done}/{len(pages)})")

    ordered = [results[p["page"]] for p in pages]
    ocr_dir = workdir / "ocr"
    ocr_dir.mkdir(exist_ok=True)
    for item in ordered:
        (ocr_dir / f"page_{item['page']:04d}.json").write_text(
            json.dumps(item, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    _log(f"      {time.perf_counter() - t1:.1f}s")

    t2 = time.perf_counter()
    _log("[3/3] 还原 HTML")
    paths = render.write_outputs(
        ordered,
        job_dir,
        base,
        font_k=args.font_k,
        mark_low=args.mark_low,
        color=args.color,
    )
    _log(f"      {time.perf_counter() - t2:.1f}s")

    if not args.keep_work:
        shutil.rmtree(workdir, ignore_errors=True)
    return paths


def _is_hidden(rel: Path) -> bool:
    return any(part.startswith(".") for part in rel.parts)


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent)
        return True
    except ValueError:
        return False


def collect_folder(
    root: Path, out_dir: Path
) -> tuple[list[Path], list[Path]]:
    """递归收集可转换的文件；返回 (可处理的, 跳过的)。"""
    usable: list[Path] = []
    skipped: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if _is_hidden(rel):
            continue
        # 输出目录可能就在被扫描的目录里，别把自己的产物再 OCR 一遍。
        if _is_inside(path, out_dir):
            continue
        (usable if convert.is_supported(path) else skipped).append(path)
    return usable, skipped


def run_folder(args: argparse.Namespace, out_dir: Path, jobs: int) -> int:
    roots = [Path(p).expanduser().resolve() for p in args.folder]
    bad = [r for r in roots if not r.is_dir()]
    if bad:
        for r in bad:
            _log(f"目录不存在: {r}")
        return 2

    targets: list[tuple[Path, Path]] = []
    skipped: list[Path] = []
    for root in roots:
        usable, skipped_here = collect_folder(root, out_dir)
        skipped.extend(skipped_here)
        for src in usable:
            rel = src.relative_to(root)
            # 镜像子目录层级；多个根目录时再加一层根名，避免同名文件撞车。
            parent = Path(root.name) / rel.parent if len(roots) > 1 else rel.parent
            job_dir = out_dir / parent / output_base(rel.name)
            targets.append((src, job_dir))

    if not targets:
        _log(f"目录里没有可转换的文件（跳过 {len(skipped)} 个不支持的）")
        return 2

    problems = convert.check_tools({s.suffix.lower() for s, _ in targets})
    if problems:
        for msg in problems:
            _log(msg)
        return 2

    _log(f"共 {len(targets)} 个文件待处理，跳过 {len(skipped)} 个不支持的")
    started = time.perf_counter()
    ok: list[Path] = []
    failed: list[tuple[Path, str]] = []

    for i, (src, job_dir) in enumerate(targets, 1):
        _log(f"\n=== [{i}/{len(targets)}] {src} ===")
        try:
            paths = process_one(src, job_dir, args, jobs)
        except Exception as exc:  # noqa: BLE001
            _log(f"失败: {type(exc).__name__}: {exc}")
            failed.append((src, f"{type(exc).__name__}: {exc}"))
            continue
        ok.append(paths["html"])

    elapsed = time.perf_counter() - started
    _log(f"\n{'=' * 50}")
    _log(f"完成 {len(ok)} 个，失败 {len(failed)} 个，跳过 {len(skipped)} 个，用时 {elapsed:.1f}s")
    _log(f"输出目录: {out_dir}")
    if failed:
        _log("\n失败清单:")
        for src, err in failed:
            _log(f"  - {src}\n      {err}")
    if skipped:
        preview = skipped[:10]
        _log("\n跳过的文件（不支持的格式）:")
        for src in preview:
            _log(f"  - {src}")
        if len(skipped) > len(preview):
            _log(f"  ... 还有 {len(skipped) - len(preview)} 个")

    return 1 if failed else 0


def main() -> int:
    args = build_parser().parse_args()
    out_dir = Path(args.out).expanduser().resolve()

    if not args.inputs and not args.folder:
        _log("请给出文件路径，或用 --folder 指定目录")
        return 2
    if args.inputs and args.folder:
        _log("--folder 不能和文件路径同时使用")
        return 2

    missing_models = ocr_stage.check_models(args.orient)
    if missing_models:
        for path in missing_models:
            _log(f"模型目录不存在: {path}")
        _log("模型随仓库提供；请用 uv tool install --editable <仓库路径> 安装。")
        return 2

    ocr_stage.configure_threads()
    jobs = args.jobs or ocr_stage.default_jobs()
    if jobs < 1:
        jobs = 1

    if args.folder:
        return run_folder(args, out_dir, jobs)

    srcs = [Path(p) for p in args.inputs]
    missing = [p for p in srcs if not p.is_file()]
    if missing:
        for p in missing:
            _log(f"文件不存在: {p}")
        return 2

    problems = convert.check_tools({p.suffix.lower() for p in srcs})
    if problems:
        for msg in problems:
            _log(msg)
        return 2

    failed = 0
    for src in srcs:
        _log(f"\n=== {src.name} ===")
        try:
            paths = process_one(src, out_dir / output_base(src.name), args, jobs)
        except Exception as exc:  # noqa: BLE001
            _log(f"失败: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        _log(f"完成: {paths['html']}")
        _log(f"      {paths['txt']}  |  {paths['json']}")

    return 1 if failed else 0


def _force_utf8() -> None:
    """Windows 控制台默认不是 UTF-8，中文输出会报 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def run() -> None:
    """CLI 入口。

    onnxruntime 的 C++ 析构（1DS telemetry 的 handleStop）在解释器退出时
    会死循环，实测直接把进程卡死。这里刷干净输出后用 os._exit 跳过析构。
    """
    _force_utf8()
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    run()
