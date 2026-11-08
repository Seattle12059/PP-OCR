# PP-OCR CLI API 文档（供 LLM / Agent 调用）

## 1. 这是什么

`PP-OCR` 是一个本机 OCR 命令行工具，基于 PaddleOCR 3.7 的 PP-OCRv6 Tiny 模型（检测 0.43M + 识别 1.1M 参数），
使用 ONNX Runtime 在本地 CPU 上识别图片中的文字。**不需要联网，不需要 GPU。**

两个命令：

| 命令 | 作用 | 入口 |
|---|---|---|
| `ocr` | 单张/多张图片识别 | `main:run` |
| `pp-ocr` | 文档流水线：PDF/Word/PPT/HTML/图片 → 白底 HTML | `pipeline.cli:run` |

## 2. 安装与卸载

该工具的模型保存在源码仓库中，因此使用 editable 模式安装（macOS / Linux / Windows 通用）：

```bash
uv tool install --editable /Users/seattle/workspace/tools/PP-OCR
```

处理 Office 文件需要 LibreOffice，处理 html/md 需要 Chrome 或 Edge；PDF 与图片不需要额外依赖
（Windows 上没有 poppler 时会自动用 pypdfium2）。详见仓库根目录的 `README.md`。

安装后可以在任意目录直接调用（两个命令都会装上）：

```bash
ocr --help       # 图片识别
pp-ocr --help    # 文档流水线
```

两个命令都位于 `~/.local/bin`，独立依赖环境由 uv 放在
`~/.local/share/uv/tools/pp-ocr`。源码修改会直接生效；修改
`pyproject.toml` 中的依赖后，重新执行：

```bash
uv tool install --editable --force /Users/seattle/workspace/tools/PP-OCR
```

查看或卸载：

```bash
uv tool list
uv tool uninstall pp-ocr
```

## 3. 调用方式（`ocr` 图片识别）

> 文档流水线 `pp-ocr` 用法见第 10 节。

```bash
ocr <图片路径> [更多图片路径...] [选项]
```

支持一张或多张图片；图片可以是绝对路径或相对路径，支持常见图片格式
（PNG / JPG / JPEG / WEBP / BMP 等 PIL 能解码的格式）。

### 选项

| 选项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `images` | 位置参数 | 必填 | 一个或多个图片文件路径 |
| `--engine` | `onnxruntime` / `paddle` | `onnxruntime` | 推理引擎；`paddle` 需要额外安装 paddlepaddle |
| `--json` | flag | 关 | 输出结构化 JSON（推荐 LLM 使用） |
| `--boxes` | flag | 关 | 在 JSON 中输出坐标（行级四边形 + 字级/词级框） |
| `--orient` | flag | 关 | 启用方向分类，修正倒置/竖排页面；会强制走 `paddle` 引擎，耗时约 +30% |
| `--time` | flag | 关 | 输出每张图的识别耗时 |
| `--save-viz DIR` | 路径 | 无 | 把带检测框的标注图保存到指定目录 |

## 4. 输出格式

### 4.1 普通模式（不带 `--json`）

```text

/path/to/image.png
  上海斯格威铂尔大酒店 (0.999)
  打浦路15号 (0.997)
  绿洲仕格维花园公寓 (0.996)
  耗时: 0.104s          # 仅在 --time 时出现
```

未识别到文字时输出：

```text

/path/to/image.png
  (未识别到文字)
```

### 4.2 JSON 模式（推荐 LLM 使用）

```bash
ocr --json /path/to/image.png
```

输出顶层对象：

```json
{
  "engine": "onnxruntime",
  "init_elapsed_sec": 0.849,
  "results": [
    {
      "image": "/path/to/image.png",
      "texts": [
        "上海斯格威铂尔大酒店",
        "打浦路15号"
      ],
      "scores": [
        0.9992511868476868,
        0.9967785477638245
      ],
      "elapsed_sec": 0.091
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `engine` | string | 本次使用的推理引擎 |
| `init_elapsed_sec` | number | 模型初始化耗时（首次运行明显更长） |
| `results[]` | array | 每个输入图片对应一个元素，顺序与输入一致 |
| `results[].image` | string | 输入图片路径（原样返回） |
| `results[].texts` | array[string] | 识别出的文字行，按检测框顺序 |
| `results[].scores` | array[number] | 每行文字的置信度，0~1，与 `texts` 一一对应 |
| `results[].elapsed_sec` | number | 该张图片的识别耗时（不含初始化） |

### 4.3 带坐标模式（`--json --boxes`）

在普通 JSON 基础上，每张图多一个 `lines` 数组：

```json
{
  "text": "上海斯格威铂尔大酒店",
  "score": 0.9999,
  "box": [31.0, 41.0, 348.0, 73.0],      // 行级外接矩形
  "poly": [[31,41],[348,41],[348,73],[31,73]],  // 行级四边形（可能是倾斜的）
  "angle": 0.0,
  "segments": [                           // 字级/词级框：中文逐字，英文逐词
    {"t": "上", "box": [33.0, 41.0, 65.0, 73.0]},
    {"t": "海", "box": [65.0, 41.0, 97.0, 73.0]}
  ]
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `box` | 行级矩形 `[x1,y1,x2,y2]`，像素坐标，原点在图片左上角 |
| `poly` | 行级四点四边形，倾斜文字会呈现非矩形 |
| `segments` | 中文逐字、英文逐词；`return_word_box` 在这个模式下自动开启 |
| `segments[].box` | 该字/词的矩形。**注意**：模型不保证 `segments` 的个数与 `text` 字符数完全一致（空格、特殊符号会被跳过），消费方应做长度校验后再用 |

## 5. 退出码

| 退出码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 初始化失败（例如 `--engine paddle` 但未安装 paddlepaddle） |
| `2` | 参数/环境错误：模型目录缺失、依赖未安装、图片文件不存在 |

## 6. 示例

### 单张图片

```bash
ocr /Users/me/Documents/photo.png
```

### 批量图片 + JSON + 耗时

```bash
ocr --json --time a.png b.jpg c.webp
```

### 保存标注图

```bash
ocr --save-viz ./output a.png
```

### 在 Python 中调用并解析

```python
import json
import subprocess

proc = subprocess.run(
    ["ocr", "--json", "receipt.jpg"],
    capture_output=True,
    text=True,
)
if proc.returncode != 0:
    raise RuntimeError(proc.stderr)

data = json.loads(proc.stdout)
for item in data["results"]:
    for text, score in zip(item["texts"], item["scores"]):
        print(f"{text}\t{score:.3f}")
```

## 7. LLM / Agent 使用建议

1. **总是用 `--json`**：结构化输出比解析终端文本更可靠。
2. **只传文件路径，不要传图片二进制内容**：CLI 不接受 stdin 图片。
3. **先检查退出码**：非 0 时读取 stderr 判断原因（文件不存在、模型缺失等）。
4. **不要并发调用过多**：每次调用都会加载模型（首次约 0.5~1s），单张图推理约几十毫秒到几百毫秒；
   需要处理大量图片时，一次传入多张比多次调用更高效（模型只初始化一次）。
5. **置信度过滤**：`scores` 低于 0.5 的行通常是误识别，可按场景决定是否丢弃。
6. **文字方向**：默认关闭方向分类（速度优先）。文字倒置或竖排时加 `--orient`，
   它会自动切到 `paddle` 引擎并启用文档/文本行两级方向分类，耗时约 +30%。

## 8. 本地文件布局

```text
/Users/seattle/workspace/tools/PP-OCR/
  bin/ocr, bin/ocr.cmd        # 项目内启动脚本（.sh / Windows .cmd）
  bin/pp-ocr, bin/pp-ocr.cmd  # 流水线启动脚本
  main.py            # 单图 CLI 主程序
  pipeline/
    cli.py           # pp-ocr 入口与编排
    convert.py       # ① 各种格式 → 页图
    ocr_stage.py     # ② 多进程并行 OCR
    render.py        # ③ 页图 → 白底 HTML
    fontconfig/      # LibreOffice 中文字体兜底配置
    print.css        # Markdown → HTML 打印样式
  models/
    PP-OCRv6_tiny_det_onnx/    # ONNX 检测模型
    PP-OCRv6_tiny_rec_onnx/    # ONNX 识别模型
    PP-OCRv6_tiny_det/         # Paddle 检测模型（--orient 使用）
    PP-OCRv6_tiny_rec/         # Paddle 识别模型（--orient 使用）
    official_models/           # 方向分类模型（--orient 使用，约 13MB）
  pyproject.toml     # uv 项目配置
  uv.lock            # 依赖锁文件
  .venv/             # Python 虚拟环境
```

## 9. 常见问题

- `ocr` / `pp-ocr: command not found`：当前 shell 未刷新 PATH，执行 `hash -r` 或新开终端。
- 提示模型目录不存在：检查 `models/` 下是否有对应的目录，缺失时重新下载模型。
- `--engine paddle` 报初始化失败：未安装 paddlepaddle。
- 图片路径含空格：用引号包裹，如 `ocr "my photo.png"`。
- **docx/pptx 转换后中文整段空白**：LibreOffice headless 依赖 fontconfig 找字体，而
  `sans-serif:lang=zh` 默认会落到没有中文字形的 Verdana。`pipeline/convert.py` 会设置
  `FONTCONFIG_PATH` 指向 `pipeline/fontconfig/`，把中文兜底到 PingFang / Songti。
  自己手工调 `soffice` 时若遇到空白，同样要带上这个环境变量。
- **首次运行很慢**：模型初始化要 1~2 秒，属于正常现象；之后同一进程内不再重复加载。
- **Windows 上没有 poppler**：不需要装，PDF 会自动改用 pypdfium2 渲染，效果一致。
  LibreOffice / Chrome 的安装路径会按 `Program Files`、`Program Files (x86)`、
  `LOCALAPPDATA` 自动探测；没装 Chrome 时会用系统自带的 Edge。
- **Windows 控制台中文乱码**：CLI 启动时已把 stdout/stderr 强制为 UTF-8；
  若仍异常，先执行 `chcp 65001`。

## 10. 文档流水线 `pp-ocr`

把 PDF / Word / PPT / Excel / HTML / Markdown / 图片整份转成**可检索的白底 HTML**：
每页一张 SVG，画布坐标等于原图像素坐标，每个字按 OCR 检测框的位置和宽度摆放。

### 10.1 用法

```bash
pp-ocr <文件...> --out <目录> [选项]
pp-ocr --folder <目录...> --out <目录> [选项]
```

| 选项 | 默认 | 说明 |
|---|---|---|
| `--folder DIR [DIR...]` | 无 | 批量模式：递归扫描目录里所有可转换的文件 |
| `--out` | `./ocr-out` | 输出目录 |
| `--dpi` | `200` | 转图分辨率；小字号扫描件可提到 300 |
| `--jobs` | 自动 | 并行进程数，默认 min(8, 核数) |
| `--orient` | 关 | 方向分类，修正倒置/竖排 |
| `--font-k` | `0.78` | **无中文行**时的字号兜底系数；有中文的行自动按字的间隔估字号 |
| `--mark-low` | 关 | 置信度 < 0.5 的文字标红 |
| `--color` | 关 | 从原图采样文字颜色，保留彩色标题 |
| `--keep-work` | 关 | 保留中间产物（页图、单页 JSON） |

### 10.2 输出

每个源文件单独一个目录，目录名和三个产物都沿用原文件名，**扩展名的点换成下划线**
（避免产物目录和原文件同名），所以同名的 `a.docx` 和 `a.pdf` 不会互相覆盖：

```text
<out>/
  报告_pdf/
    报告_pdf.html   # 白底还原页（可直接浏览器打开 / 打印成 PDF）
    报告_pdf.json   # 全部页的行级 + 字级坐标
    报告_pdf.txt    # 纯文本，按阅读顺序
  论文_docx/
    论文_docx.html
    论文_docx.json
    论文_docx.txt
```

批量模式会镜像源目录的层级：

```bash
pp-ocr --folder ./资料 --out ./Out
```

```text
资料/                       Out/
├── 甲.docx        →         ├── 甲_docx/
└── 子目录/                  └── 子目录/
    ├── 乙.pdf         →         ├── 乙_pdf/
    └── notes.txt              └── （跳过，最后汇总列出）
```

批量模式的规则：

- 递归扫描，跳过隐藏文件（`.DS_Store` 等）；
- 自动排除 `--out` 目录，避免把产物再 OCR 一遍；
- 不支持的格式跳过，结束时汇总列出；
- 单个文件失败不中断，最后打印失败清单；有失败时退出码为 1；
- 文件之间串行、文件内部并行（`--jobs` 控制的是文件内部的进程数）。

### 10.3 阶段划分

```text
源文件 ──①转图──> pages/*.png + manifest.json
                        ├─②并行OCR──> ocr/page_XXXX.json（字级坐标）
                        └─③渲染────> out/<name>.html
```

①之后只认 PNG + 像素坐标，源格式差异全部隔离在第一步。

### 10.4 已知限制

- **只还原文字**，图表、图片、表格框线、分隔线不会重建（白底就是要丢掉它们）。
- 字宽靠 `<textLength lengthAdjust="spacingAndGlyphs">` 锁到检测框宽度，个别窄字形
  （如数字、字母）会被轻微压缩。
- 阅读顺序按"竖直中心聚类 + 行内 x 排序"，多栏文档可能串行。
