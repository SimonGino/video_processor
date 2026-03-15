## Why

当前视频处理管线的循环逻辑存在几个实际问题：

1. **失败文件无退避**：一个损坏的 FLV 或 ASS 文件会在每轮定时任务中反复尝试、反复失败，永远不会被跳过，日志被无意义的重复错误淹没
2. **无弹幕 FLV 成为死文件**：在非 `SKIP_VIDEO_ENCODING` 模式下，`encode_video()` 只遍历 `*.ass` 文件。如果弹幕采集失败导致没有 XML/ASS，对应的 FLV 会永远滞留在 `data/processing/`，不会被处理
3. **压制质量偏高**：`-global_quality 32` 对低性能处理器来说质量过高，编码速度偏慢

## What Changes

- **失败退避 + 隔离机制**：在 `encode_video()` 和 `convert_danmaku()` 中引入内存级失败计数器，同一文件连续失败超过阈值（3 次）后，自动移入 `data/failed/` 目录并记录明确日志。文件一旦在 `failed/` 目录中就不会被重新拉回处理，需人工干预恢复
- **无弹幕 FLV 直通处理**：当 FLV 文件存在、无 `.flv.part`（非录制中）、且无对应 XML/ASS 时，在第二轮管线检测到后（避免竞态），将其作为无弹幕版本直接编码（不烧字幕）或移入上传目录
- **降低压制质量参数**：QSV 的 `-global_quality` 从 32 调整到 35，在低性能机器上提升编码速度、减小文件体积

## Capabilities

### New Capabilities

- `failed-file-quarantine`: 失败文件退避与隔离机制。内存计数器追踪连续失败次数，超过阈值后移入 `data/failed/`，避免无限重试
- `orphan-flv-passthrough`: 无弹幕 FLV 直通处理。缺失 XML/ASS 的 FLV 文件在等待一轮后自动作为无弹幕版本处理，不再成为死文件

### Modified Capabilities

- 编码质量参数调整（`encoder.py` 中 `-global_quality` 32 → 35）

## Impact

- **修改文件**：`encoder.py`、`danmaku.py`、`config.py`
- **新增目录**：`data/failed/`（运行时自动创建）
- **风险**：低。失败隔离和无弹幕直通都是增量逻辑，不影响正常处理路径。质量参数调整只影响新压制的视频
- **回滚**：改回 `global_quality = 32`，删除失败计数逻辑即可恢复原行为
