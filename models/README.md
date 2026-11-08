# 模型目录

全部模型随仓库一起提交，开箱即用，总共约 26 MB。

## PP-OCRv6 Tiny（`ocr` 与 `pp-ocr` 默认使用）

| 目录 | 作用 | 格式 | 大小 |
|---|---|---|---|
| `PP-OCRv6_tiny_det_onnx/` | 文字检测 | ONNX | 1.9 MB |
| `PP-OCRv6_tiny_rec_onnx/` | 文字识别 | ONNX | 4.4 MB |
| `PP-OCRv6_tiny_det/` | 文字检测 | Paddle | 1.9 MB |
| `PP-OCRv6_tiny_rec/` | 文字识别 | Paddle | 4.4 MB |

ONNX 版给 onnxruntime 用（默认、最快）；Paddle 版在开 `--orient` 时使用，因为方向分类模型
只有 Paddle 格式，同一进程里必须统一引擎。

## 方向矫正模型（`--orient` 使用）

| 目录 | 作用 | 大小 |
|---|---|---|
| `official_models/PP-LCNet_x1_0_doc_ori/` | 文档方向分类（0°/90°/180°/270°） | 6.6 MB |
| `official_models/PP-LCNet_x1_0_textline_ori/` | 文本行方向分类 | 6.6 MB |

## 重新下载

模型来自 PaddlePaddle 官方仓库（Apache-2.0）。若目录损坏或需要重新获取：

```bash
PADDLE_PDX_CACHE_HOME="$PWD/models" PADDLE_PDX_MODEL_SOURCE=modelscope \
python -c "
from paddlex.inference.utils.official_models import official_models
for name in ['PP-LCNet_x1_0_doc_ori', 'PP-LCNet_x1_0_textline_ori']:
    print(name, official_models[name])
"
```

PP-OCRv6 Tiny 的四个目录需要从 PaddleOCR 官方发布页获取对应的 ONNX / Paddle 推理模型。

## 许可

模型版权归 PaddlePaddle 所有，遵循 Apache-2.0 许可证。
