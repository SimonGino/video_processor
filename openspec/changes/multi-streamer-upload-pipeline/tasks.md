## 1. 统一配置结构

- [x] 1.1 重构 `config.yaml` 为按主播分组的嵌套结构，包含 `streamers` 和全局 `upload` 顶层 key
- [x] 1.2 重构 `uploader.py` 的 `load_yaml_config()`：解析新 YAML 结构，校验每个主播的必要字段（`room_id`、`title`、`tid`、`tag`、`desc`、`source`），加载全局 `upload` 配置
- [x] 1.3 修改 `config.py`：移除硬编码的 `STREAMERS` 列表，改为从 `yaml_config` 的 `streamers` 部分自动生成 `STREAMERS = [{"name": ..., "room_id": ...}, ...]`
- [x] 1.4 确保录制服务和状态监控读取 `config.STREAMERS` 的行为不变，编写单元测试验证 YAML → STREAMERS 派生逻辑

## 2. 数据模型扩展

- [x] 2.1 `models.py` 中 `UploadedVideo` 新增 `streamer_name = Column(String, nullable=True, index=True)`
- [x] 2.2 验证 SQLite `create_all()` 自动加列的行为，必要时编写迁移逻辑处理已有数据库

## 3. 上传流水线多主播适配

- [x] 3.1 重构 `upload_to_bilibili()`：从单主播硬编码改为遍历所有主播，按文件名前缀（`{主播名}录播`）匹配文件到对应主播
- [x] 3.2 每个主播独立查询 `StreamSession`（按 `streamer_name` 过滤）进行场次匹配
- [x] 3.3 每个主播使用各自在 `config.yaml` 中的上传元数据（`title`、`tid`、`tag`、`desc`、`source` 等）创建新稿件或追加分 P
- [x] 3.4 创建 `UploadedVideo` 记录时填入 `streamer_name` 字段
- [x] 3.5 处理文件名不匹配任何已配置主播的情况：跳过并记录 warning
- [x] 3.6 编写上传多主播分组逻辑的单元测试

## 4. 上传并发控制

- [x] 4.1 在 `uploader.py` 模块级创建 `asyncio.Semaphore`，并发数从全局 `upload.max_concurrent` 配置读取，默认为 1
- [x] 4.2 所有调用 biliup CLI / bilitool 的上传和追加操作包裹在 `async with semaphore:` 中
- [x] 4.3 编写测试验证信号量正确限制并发

## 5. 上传带宽限速（cgroup 集成）

- [x] 5.1 `uploader.py` 中 biliup CLI 调用从 `subprocess.run` 改为 `subprocess.Popen` + `communicate()`，确保返回值解析逻辑不变
- [x] 5.2 在 `Popen` 启动后、`communicate()` 之前，尝试将子进程 PID 写入 `/sys/fs/cgroup/biliup-limit/cgroup.procs`：cgroup 不存在时静默跳过，写入失败时记录 warning
- [x] 5.3 编写测试验证 cgroup 存在/不存在/写入失败三种场景的行为

## 6. 调度器与 API 端点适配

- [x] 6.1 `scheduler.py` 中 `scheduled_video_pipeline()` 的 `PROCESS_AFTER_STREAM_END` 逻辑：从检查 `config.STREAMER_NAME` 单个主播改为遍历所有主播，任一主播下播即触发处理
- [x] 6.2 `scheduler.py` 中下播触发的 one-shot pipeline job：按主播独立触发，不互相阻塞
- [x] 6.3 `app.py` 中 `/run_processing_tasks` 和 `/run_upload_tasks` 端点：移除对 `config.STREAMER_NAME` 的依赖，处理所有主播
- [x] 6.4 移除 `config.py` 中的 `DEFAULT_STREAMER_NAME`、`STREAMER_NAME`、`DOUYU_ROOM_ID` 向后兼容变量（确认无其他引用后）

## 7. 测试与验收

- [ ] 7.1 配置两个主播运行完整流程测试（录制、处理、上传），验证各主播视频独立投稿
- [ ] 7.2 验证并发控制：两个主播同时有待上传文件时，上传任务按信号量排队
- [ ] 7.3 在 Linux Debian 12 服务器上测试 `scripts/upload-bandwidth-limit.sh` 限速脚本 + cgroup 集成
- [ ] 7.4 验证单主播场景向后兼容：仅配置一个主播时行为与改动前一致
