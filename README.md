# PP-OCR

本机离线 OCR 工具集，基于 PaddleOCR 3.7 的 **PP-OCRv6 Tiny**（检测 0.43M + 识别 1.1M 参数）。
不联网、不需要 GPU，macOS / Linux / Windows 都能跑。

提供两个命令：

| 命令 | 作用 |
|---|---|
| `ocr` | 识别一张或多张图片里的文字，可输出字级坐标 |
| `pp-ocr` | 文档流水线：PDF / Word / PPT / Excel / HTML / Markdown / 图片 → **可检索的白底 HTML** |

---

## 功能

- **字级坐标**：中文逐字、英文逐词，直接拿到每个字的外接框和原图位置。
- **多格式转图**：PDF 用 poppler（没有则自动退回 pypdfium2），Office 走 LibreOffice，
  HTML / Markdown 走 Chrome/Edge 无头打印。
- **并行 OCR**：多进程 + 每进程常驻模型，单页约 0.7 秒。
- **白底重排**：每页一个 SVG，画布坐标等于原图像素坐标，用 `textLength` 把字宽锁回检测框。
- **方向矫正**：`--orient` 处理倒置 / 竖排页面。
- **批量目录**：`pp-ocr --folder` 递归处理整个目录，每个文件单独一个输出子目录。

---

## 安装

### 1. 前置依赖

| 依赖 | 用途 | 是否必须 |
|---|---|---|
| Python 3.10 – 3.12 | 运行时 | 必须 |
| [uv](https://github.com/astral-sh/uv) | 安装与依赖管理 | 推荐 |
| LibreOffice | docx / pptx / xlsx → PDF | 处理 Office 文件时 |
| Chrome 或 Edge | html / md → PDF | 处理 html / md 时 |
| pandoc | Markdown → HTML | 处理 .md 时 |
| poppler (`pdftoppm`) | PDF → 图片 | 可选，缺失时用 pypdfium2 |

安装方式：

```bash
# macOS
brew install --cask libreoffice
brew install --cask google-chrome
brew install pandoc poppler

# Windows（PowerShell）
winget install TheDocumentFoundation.LibreOffice
winget install Google.Chrome        # 或直接使用系统自带的 Edge
winget install JohnMacFarlane.Pandoc
# poppler 可选，不装会自动用 pypdfium2

# Ubuntu / Debian
sudo apt install libreoffice chromium-browser pandoc poppler-utils
```

### 2. 安装本工具

```bash
uv tool install --editable /path/to/PP-OCR
```

安装后 `ocr` 和 `pp-ocr` 会出现在 PATH 上（Windows 下是 `ocr.exe` / `pp-ocr.exe`）。

从源码开发时也可以直接用启动脚本：

```bash
./bin/pp-ocr --help          # macOS / Linux
bin\pp-ocr.cmd --help        # Windows
```

---

## 用法

### `ocr`：图片识别

```bash
ocr photo.png
ocr --json --boxes a.png b.png
ocr --orient scan_180.png        # 倒置/竖排
ocr --save-viz ./viz photo.png   # 保存带检测框的标注图
```

`--json --boxes` 输出：

```json
{
  "text": "上海斯格威铂尔大酒店",
  "score": 0.9999,
  "box": [31.0, 41.0, 348.0, 73.0],
  "poly": [[31,41],[348,41],[348,73],[31,73]],
  "segments": [
    {"t": "上", "box": [33.0, 41.0, 65.0, 73.0]},
    {"t": "海", "box": [65.0, 41.0, 97.0, 73.0]}
  ]
}
```

### `pp-ocr`：文档流水线

```bash
pp-ocr 报告.pdf --out ./Out
pp-ocr a.docx b.pdf c.png --out ./Out
pp-ocr --folder ./资料 --out ./Out        # 递归批量
```

常用选项：

| 选项 | 默认 | 说明 |
|---|---|---|
| `--folder DIR...` | 无 | 批量模式，递归扫描目录 |
| `--out` | `./ocr-out` | 输出目录 |
| `--dpi` | `200` | 转图分辨率，小字号扫描件可提到 300 |
| `--jobs` | min(8, 核数) | 并行进程数 |
| `--orient` | 关 | 方向分类，修正倒置/竖排 |
| `--font-k` | `0.78` | 无中文行的字号兜底系数 |
| `--mark-low` | 关 | 置信度 < 0.5 的文字标红 |
| `--color` | 关 | 从原图采样文字颜色 |
| `--keep-work` | 关 | 保留中间产物（页图、单页 JSON） |

输出结构 —— 每个源文件一个目录，扩展名的点换成下划线：

```text
Out/
├── 报告_pdf/
│   ├── 报告_pdf.html    # 白底还原页，浏览器可直接打开 / 打印成 PDF
│   ├── 报告_pdf.json    # 全部页的行级 + 字级坐标
│   └── 报告_pdf.txt     # 纯文本，按阅读顺序
└── 子目录/
    └── 论文_docx/
        ├── 论文_docx.html
        ├── 论文_docx.json
        └── 论文_docx.txt
```

---

## 工作原理

```text
源文件 ──①转图──> pages/*.png
          │         Office → LibreOffice → PDF
          │         HTML/MD → Chrome → PDF
          │         PDF → pdftoppm / pypdfium2 → PNG
          ▼
        ②并行 OCR（进程池，每进程常驻模型，return_word_box=True）
          ▼
        ③白底重排（每页一个 SVG，逐字绝对定位）
```

①之后只认 PNG + 像素坐标，源格式差异全部隔离在第一步。

字号不是直接取检测框高度：检测框经过 `unclip_ratio=1.5` 外扩，比真实字高大 40%~50%。
有中文的行用「相邻两字的起点间距」估字号（中文是全宽方块字，起点差 = 字前进宽度），
纯拉丁行退回 `框高 × 0.78`。

---

## 支持的格式

| 类型 | 扩展名 |
|---|---|
| 图片 | `.png .jpg .jpeg .webp .bmp .tif .tiff .gif` |
| PDF | `.pdf` |
| Word | `.doc .docx .odt .rtf` |
| PowerPoint | `.ppt .pptx .odp` |
| Excel | `.xls .xlsx .ods` |
| 网页 | `.html .htm` |
| Markdown | `.md .markdown` |

不支持：`.key`、`.pages`、`.numbers`、`.epub`、`.txt`、`.csv`。
新增格式只需在 `pipeline/convert.py` 的扩展名集合里加一项，或先转成 PDF / 图片再喂进来。

---

## 平台支持

| | macOS | Linux | Windows |
|---|---|---|---|
| `ocr` | ✅ | ✅ | ✅ |
| `pp-ocr`（PDF / 图片） | ✅ | ✅ | ✅ |
| `pp-ocr`（Office） | ✅ | ✅ | ✅ |
| `pp-ocr`（HTML / MD） | ✅ | ✅ | ✅（Chrome 或 Edge） |
| PDF 渲染 | poppler | poppler / pdfium | pdfium（无需装 poppler） |

> macOS 上有一个已知坑：LibreOffice headless 依赖 fontconfig 找字体，而
> `sans-serif:lang=zh` 默认会落到没有中文字形的 Verdana，导出的 PDF 里中文整段空白。
> 本项目通过 `pipeline/fontconfig/fonts.conf` 把中文兜底到 PingFang / Songti 解决了这个问题。

---

## 已知限制

- 白底重排**只还原文字**，图表、图片、表格框线、分隔线不会重建。
- Tiny 识别模型精度有限，小字号 / 特殊字体会出现错字。
- 阅读顺序按「竖直中心聚类 + 行内 x 排序」，多栏文档可能串行。
- 默认关闭文档方向矫正，倒置 / 竖排需显式加 `--orient`。

---

## 目录结构

```text
PP-OCR/
├── main.py                 # ocr 命令：单图/多图识别
├── pipeline/
│   ├── cli.py              # pp-ocr 命令：编排
│   ├── convert.py          # ① 各种格式 → 页图
│   ├── ocr_stage.py        # ② 多进程并行 OCR
│   ├── render.py           # ③ 页图 → 白底 HTML
│   ├── fontconfig/         # LibreOffice 中文字体兜底（仅类 Unix）
│   └── print.css           # Markdown 打印样式
├── bin/                    # 源码启动脚本（.sh / .cmd）
├── models/                 # PP-OCRv6 Tiny 模型（ONNX + Paddle 两套）
├── PP-OCR-API-DOC.md       # 面向 LLM / Agent 的详细 API 文档
└── pyproject.toml
```

---

## 许可证

模型来自 PaddlePaddle（Apache-2.0）。**本仓库尚未添加 LICENSE**，如需开源请自行补充
（例如 MIT 或 Apache-2.0）。
