## Why

当前系统的录制服务和直播状态监控已支持多主播并发，但**上传流水线硬编码为仅处理第一个主播**（`DEFAULT_STREAMER_NAME`），`UploadedVideo` 模型缺少 `streamer_name` 字段，`config.yaml` 也只有一份扁平的上传元数据（标题、标签、简介都写死了"洞主"）。一旦配置多个主播，非首位主播的录播无法匹配场次、无法上传。

同时，多主播同时下播时视频会同时涌入上传队列，容易触发 B 站速率限制（code 21540），需要并发控制。此外还需要支持 OS 层面的上传带宽限速（tc + cgroup），避免上传占满服务器带宽。

## What Changes

- **统一配置结构**：将主播信息（`config.py` STREAMERS）和上传元数据（`config.yaml`）合并到 `config.yaml`，按主播分组，每个主播有独立的标题模板、标签、分区、简介等上传参数
- **上传流水线多主播适配**：`uploader.py` 从按文件名识别主播，改为按主播分组处理，每个主播独立匹配场次、独立创建/追加 B 站投稿
- **数据模型扩展**：`UploadedVideo` 表新增 `streamer_name` 字段，建立主播维度的上传记录追踪
- **上传并发控制**：引入信号量机制限制同时上传的任务数，防止多主播视频同时上传时触发 B 站限流
- **上传带宽限速**：通过 tc + cgroup v2 限制 biliup 进程的上传带宽，代码层面在启动 biliup 子进程时自动将其 PID 写入 cgroup，配合 OS 层限速脚本使用
- **调度器与 API 端点适配**：`scheduler.py` 的 `PROCESS_AFTER_STREAM_END` 逻辑和手动触发端点（`/run_processing_tasks`、`/run_upload_tasks`）支持所有主播

## Capabilities

### New Capabilities

- `per-streamer-upload`: 上传流水线按主播分组处理，每个主播的视频独立匹配场次、独立创建/追加 B 站投稿，上传元数据从 `config.yaml` 按主播读取
- `upload-concurrency-control`: 上传并发控制机制，通过信号量限制同时进行的上传任务数量，防止触发 B 站速率限制
- `unified-streamer-config`: 统一主播配置结构，将主播录制信息和上传元数据合并到 `config.yaml`，`config.py` 中的 STREAMERS 改为从 YAML 加载
- `upload-bandwidth-limit`: 上传带宽限速支持，代码层面集成 cgroup 进程归类，配合 OS 层 tc + cgroup v2 限速脚本

### Modified Capabilities

（无现有 spec 需要修改）

## Impact

- **代码变更**：`uploader.py`（核心重构 + cgroup 集成）、`scheduler.py`（调度适配）、`app.py`（端点适配、主播加载）、`models.py`（模型扩展）、`config.py`（STREAMERS 改为从 YAML 加载、新增并发配置项）
- **配置文件**：`config.yaml` 结构变更（扁平 → 按主播分组嵌套），需要用户迁移现有配置
- **运维工具**：新增 `scripts/upload-bandwidth-limit.sh` 限速脚本（Linux only，已完成）
- **数据库**：`uploaded_videos` 表新增 `streamer_name` 列，旧数据使用默认值兼容
- **API**：手动触发端点行为变更（处理所有主播而非单个），接口签名不变
- **向后兼容**：单主播场景行为不变
