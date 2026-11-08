"""阶段 ①：把各种源文件转成一张张页图（PNG）。

下游只认 PNG + 像素坐标，所以 PDF / Word / PPT / HTML 的差异全部收敛在这里。

跨平台说明：
- macOS / Linux 优先用 poppler 的 pdftoppm 渲染 PDF；Windows 上通常没有
  poppler，自动退回 pypdfium2（纯 wheel，无需系统依赖）。
- LibreOffice / Chrome 的安装路径按平台探测，也支持从 PATH 找。
- FONTCONFIG_PATH 只在类 Unix 上设置：Windows 的 LibreOffice 走系统的
  DirectWrite 字体回退，不需要也无法使用 fontconfig。
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
FONTCONFIG_DIR = Path(__file__).resolve().parent / "fontconfig"

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"


def _windows_candidates(relative: str) -> list[str]:
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    return [str(Path(root) / relative) for root in roots if root]


if IS_WINDOWS:
    SOFFICE_CANDIDATES = _windows_candidates("LibreOffice/program/soffice.exe")
    CHROME_CANDIDATES = _windows_candidates(
        "Google/Chrome/Application/chrome.exe"
    ) + _windows_candidates("Microsoft/Edge/Application/msedge.exe")
    SOFFICE_NAMES = ["soffice.exe", "soffice"]
    CHROME_NAMES = ["chrome", "chrome.exe", "msedge", "msedge.exe"]
else:
    SOFFICE_CANDIDATES = [
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
        "/opt/homebrew/bin/soffice",
        "/snap/bin/libreoffice",
        "/usr/lib/libreoffice/program/soffice",
    ]
    CHROME_CANDIDATES = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    SOFFICE_NAMES = ["soffice", "libreoffice"]
    CHROME_NAMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]

OFFICE_EXTS = {
    ".doc", ".docx", ".odt", ".rtf",
    ".ppt", ".pptx", ".odp",
    ".xls", ".xlsx", ".ods",
}
HTML_EXTS = {".html", ".htm", ".md", ".markdown"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}

SUPPORTED_EXTS = OFFICE_EXTS | HTML_EXTS | IMAGE_EXTS | {".pdf"}


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTS


class ConvertError(RuntimeError):
    pass


def _install_hint(tool: str) -> str:
    if IS_MACOS:
        return {
            "soffice": "brew install --cask libreoffice",
            "chrome": "brew install --cask google-chrome",
            "pdftoppm": "brew install poppler（或直接装 pypdfium2）",
            "pandoc": "brew install pandoc",
        }[tool]
    if IS_WINDOWS:
        return {
            "soffice": "winget install TheDocumentFoundation.LibreOffice",
            "chrome": "winget install Google.Chrome（或使用已安装的 Edge）",
            "pdftoppm": "可选：安装 poppler；不装会自动使用 pypdfium2",
            "pandoc": "winget install JohnMacFarlane.Pandoc",
        }[tool]
    return {
        "soffice": "sudo apt install libreoffice",
        "chrome": "sudo apt install chromium-browser",
        "pdftoppm": "sudo apt install poppler-utils（或直接装 pypdfium2）",
        "pandoc": "sudo apt install pandoc",
    }[tool]


def find_tool(candidates: list[str], names: list[str], key: str) -> str:
    for path in candidates:
        if Path(path).exists():
            return path
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    raise ConvertError(f"找不到 {key}。安装方式：{_install_hint(key)}")


def _wait_for_file(path: Path, timeout: float = 120.0) -> bool:
    """等文件出现并写完。

    Windows 上 soffice.exe 有时会在后台进程真正写完 PDF 之前就返回，
    直接检查文件是否存在会误判为失败。
    """
    import time

    deadline = time.monotonic() + timeout
    last_size = -1
    while time.monotonic() < deadline:
        if path.exists():
            size = path.stat().st_size
            if size > 0 and size == last_size:
                return True
            last_size = size
        time.sleep(0.2)
    return path.exists() and path.stat().st_size > 0


def _run(cmd: list[str], what: str, env: dict[str, str] | None = None) -> None:
    # errors="replace"：Windows 上子进程可能输出 GBK，避免解码崩掉。
    proc = subprocess.run(
        cmd, capture_output=True, text=True, errors="replace", env=env
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise ConvertError(f"{what} 失败:\n" + "\n".join(tail))


def convert_to_pdf(src: Path, workdir: Path) -> Path:
    """office / html / md → PDF（PDF 原样返回）。"""
    suffix = src.suffix.lower()
    if suffix == ".pdf":
        return src

    if suffix in OFFICE_EXTS:
        soffice = find_tool(SOFFICE_CANDIDATES, SOFFICE_NAMES, "soffice")
        outdir = workdir / "pdf"
        outdir.mkdir(parents=True, exist_ok=True)
        # 每个进程独立的 user profile，否则并发调用会互相锁死。
        profile = (workdir / "lo_profile").resolve()
        env = dict(os.environ)
        if not IS_WINDOWS:
            # 不做字体兜底的话，soffice 会把中文渲染成空白（详见 fontconfig/fonts.conf）。
            env["FONTCONFIG_PATH"] = str(FONTCONFIG_DIR)
        _run(
            [
                soffice,
                "--headless",
                "--norestore",
                f"-env:UserInstallation={profile.as_uri()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(outdir),
                str(src),
            ],
            f"LibreOffice 转换 {src.name}",
            env=env,
        )
        pdf = outdir / (src.stem + ".pdf")
        if not _wait_for_file(pdf):
            raise ConvertError(f"LibreOffice 未生成 PDF: {pdf}")
        return pdf

    if suffix in HTML_EXTS:
        chrome = find_tool(CHROME_CANDIDATES, CHROME_NAMES, "chrome")
        html = src
        if suffix in {".md", ".markdown"}:
            # Markdown 先转成独立 HTML（带中文字体样式），再让 Chrome 打印。
            pandoc = shutil.which("pandoc")
            if not pandoc:
                raise ConvertError(f"转换 Markdown 需要 pandoc：{_install_hint('pandoc')}")
            # print.css 拷到 HTML 同目录，用相对路径引用，避免 Windows 盘符路径
            # 被写进 href 里（浏览器不认 C:\...）。
            shutil.copyfile(PROJECT_DIR / "pipeline" / "print.css", workdir / "print.css")
            html = workdir / (src.stem + ".html")
            _run(
                [
                    pandoc, str(src), "-s", "-o", str(html),
                    # 用 pagetitle 只设置 <title>；用 title 会在正文顶部
                    # 额外渲染一个标题块，被 OCR 当成正文识别出来。
                    "--metadata", f"pagetitle={src.stem}",
                    "-c", "print.css",
                ],
                f"pandoc 转换 {src.name}",
            )
        outdir = workdir / "pdf"
        outdir.mkdir(parents=True, exist_ok=True)
        pdf = (outdir / (src.stem + ".pdf")).resolve()
        _run(
            [
                chrome,
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=10000",
                f"--print-to-pdf={pdf}",
                html.resolve().as_uri(),
            ],
            f"Chrome 打印 {src.name}",
        )
        if not _wait_for_file(pdf):
            raise ConvertError(f"Chrome 未生成 PDF: {pdf}")
        return pdf

    raise ConvertError(f"不支持的格式: {src.suffix}")


def pdf_to_pages(pdf: Path, pages_dir: Path, dpi: int) -> list[Path]:
    """PDF → 每页一张 PNG。优先 poppler，没有就退回 pypdfium2。"""
    pages_dir.mkdir(parents=True, exist_ok=True)

    if shutil.which("pdftoppm"):
        try:
            return _pdf_to_pages_poppler(pdf, pages_dir, dpi)
        except ConvertError as poppler_err:
            try:
                return _pdf_to_pages_pdfium(pdf, pages_dir, dpi)
            except Exception:
                raise poppler_err from None
    return _pdf_to_pages_pdfium(pdf, pages_dir, dpi)


def _pdf_to_pages_poppler(pdf: Path, pages_dir: Path, dpi: int) -> list[Path]:
    tmp = Path(tempfile.mkdtemp(prefix="pages-", dir=pages_dir.parent))
    try:
        _run(
            [
                "pdftoppm", "-r", str(dpi), "-png",
                "-aa", "yes", "-aaVector", "yes",
                str(pdf), str(tmp / "p"),
            ],
            f"pdftoppm 渲染 {pdf.name}",
        )
        raw = sorted(tmp.glob("p-*.png"), key=_page_num)
        if not raw:
            raise ConvertError(f"PDF 没有渲染出任何页: {pdf}")
        out: list[Path] = []
        for i, path in enumerate(raw, 1):
            dest = pages_dir / f"page_{i:04d}.png"
            path.replace(dest)
            out.append(dest)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _pdf_to_pages_pdfium(pdf: Path, pages_dir: Path, dpi: int) -> list[Path]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover
        raise ConvertError(
            "没有 pdftoppm，也缺少 pypdfium2。安装方式："
            + _install_hint("pdftoppm")
        ) from exc

    try:
        doc = pdfium.PdfDocument(str(pdf))
    except Exception as exc:  # noqa: BLE001
        raise ConvertError(f"pypdfium2 打不开 PDF {pdf.name}: {exc}") from exc

    out: list[Path] = []
    try:
        scale = dpi / 72
        for i in range(len(doc)):
            page = doc[i]
            try:
                image = page.render(scale=scale).to_pil()
            except Exception as exc:  # noqa: BLE001
                raise ConvertError(f"pypdfium2 渲染第 {i + 1} 页失败: {exc}") from exc
            dest = pages_dir / f"page_{i + 1:04d}.png"
            image.save(dest)
            out.append(dest)
    finally:
        doc.close()

    if not out:
        raise ConvertError(f"PDF 没有渲染出任何页: {pdf}")
    return out


def _page_num(path: Path) -> int:
    m = re.search(r"(\d+)$", path.stem)
    return int(m.group(1)) if m else 0


def image_to_page(src: Path, pages_dir: Path) -> Path:
    from PIL import Image

    pages_dir.mkdir(parents=True, exist_ok=True)
    dest = pages_dir / "page_0001.png"
    with Image.open(src) as img:
        img.load()
        if img.mode in ("RGBA", "LA") or (
            img.mode == "P" and "transparency" in img.info
        ):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
        img.save(dest)
    return dest


def build_pages(src: Path, workdir: Path, dpi: int) -> list[dict]:
    """源文件 → 页图列表。返回 [{page, image, width, height}]。"""
    pages_dir = workdir / "pages"
    suffix = src.suffix.lower()

    if suffix in IMAGE_EXTS:
        paths = [image_to_page(src, pages_dir)]
    elif suffix in OFFICE_EXTS or suffix in HTML_EXTS or suffix == ".pdf":
        pdf = convert_to_pdf(src, workdir)
        paths = pdf_to_pages(pdf, pages_dir, dpi)
    else:
        raise ConvertError(f"不支持的格式: {src.suffix}")

    from PIL import Image

    pages = []
    for i, path in enumerate(paths, 1):
        with Image.open(path) as img:
            width, height = img.size
            blank = _looks_blank(img)
        if blank:
            warn(
                f"  警告: 第 {i} 页几乎是全白的（可能字体缺失或本来就是空白页）: {path}"
            )
        pages.append(
            {"page": i, "image": str(path), "width": width, "height": height}
        )
    return pages


def _looks_blank(img) -> bool:
    """缩略图上看有没有墨迹，用来捕捉"转图失败但没报错"的情况。"""
    small = img.convert("L")
    small.thumbnail((200, 200))
    lo, _ = small.getextrema()
    return lo > 245


def check_tools(suffixes: set[str]) -> list[str]:
    """提前检查依赖，缺什么一次性告诉用户。"""
    problems: list[str] = []

    needs_office = bool(suffixes & OFFICE_EXTS)
    needs_chrome = bool(suffixes & HTML_EXTS)
    needs_pdf = bool(suffixes & (OFFICE_EXTS | HTML_EXTS | {".pdf"}))

    if needs_office:
        try:
            find_tool(SOFFICE_CANDIDATES, SOFFICE_NAMES, "soffice")
        except ConvertError:
            problems.append(
                f"缺 LibreOffice（docx/pptx/xlsx 需要）：{_install_hint('soffice')}"
            )

    if needs_chrome:
        try:
            find_tool(CHROME_CANDIDATES, CHROME_NAMES, "chrome")
        except ConvertError:
            problems.append(
                f"缺 Chrome 或 Edge（html/md 需要）：{_install_hint('chrome')}"
            )
        if any(s in {".md", ".markdown"} for s in suffixes) and not shutil.which(
            "pandoc"
        ):
            problems.append(
                f"缺 pandoc（Markdown 需要）：{_install_hint('pandoc')}"
            )

    if needs_pdf and not shutil.which("pdftoppm"):
        try:
            import pypdfium2  # noqa: F401
        except ImportError:
            problems.append(
                f"PDF 渲染需要 pdftoppm 或 pypdfium2：{_install_hint('pdftoppm')}"
            )

    return problems


def warn(msg: str) -> None:
    print(msg, file=sys.stderr)
