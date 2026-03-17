## Context

当前系统架构中，录制服务（`recording_service.py`）已通过 `asyncio.create_task` 为 `config.STREAMERS` 列表中每个主播创建独立并发任务，直播状态监控（`stream_monitor.py`）也为每个主播创建独立 `StreamStatusMonitor` 实例。但上传流水线存在以下硬编码单主播问题：

1. `uploader.py` 的 `upload_to_bilibili()` 使用 `config.DEFAULT_STREAMER_NAME`（第一个主播）进行场次匹配，其他主播的视频被忽略
2. `config.yaml` 是扁平结构，只有一份上传元数据（标题/标签/简介都写死了特定主播信息）
3. `UploadedVideo` 模型无 `streamer_name` 字段，依赖文件名推断主播归属
4. `scheduler.py` 的 `PROCESS_AFTER_STREAM_END` 和手动触发端点仅检查首位主播的直播状态

此外，多主播同时上传会挤占上传通道，需要并发控制和可选的 OS 层带宽限速。

## Goals / Non-Goals

**Goals:**
- 将主播录制配置和上传元数据统一到 `config.yaml`，一处配置管全部
- 上传流水线按主播分组处理，每个主播独立匹配场次和投稿
- 上传任务引入并发控制，防止多主播同时上传触发 B 站限流
- 支持将 biliup 子进程加入 cgroup，配合 OS 层 tc 限速脚本
- 单主播场景向后兼容，无需用户改动即可继续工作

**Non-Goals:**
- 不做多 B 站账号支持（所有主播上传到同一个 B 站账号）
- 不在应用层实现带宽限速（限速由 OS 层 tc + cgroup 负责）
- 不拆分上传队列为独立微服务
- 不做 Web UI 配置管理

## Decisions

### 决策 1：配置统一到 `config.yaml`，按主播分组

**选择**：`config.yaml` 采用 `streamers` 顶层 key，每个主播下挂 `room_id` + `upload` 子对象

```yaml
streamers:
  洞主:
    room_id: "138243"
    upload:
      title: "洞主直播录像{time}弹幕版"
      tid: 171
      tag: "洞主,凯哥,直播录像,游戏实况"
      desc: "..."
      source: "https://www.douyu.com/138243"
      cover: ""
      dynamic: ""

upload:
  max_concurrent: 1
  rate_limit_cooldown: 300
```

**替代方案**：
- 每个主播一个独立 YAML 文件 → 文件散落，管理成本高
- 保持 `config.py` STREAMERS + 独立 `config.yaml` → 两处配置不一致容易出错

**理由**：一个文件管所有主播，结构清晰，`config.py` 中的 `STREAMERS` 改为从 YAML 加载后只保留运行时常量。

### 决策 2：`config.py` STREAMERS 从 YAML 派生

**选择**：启动时 `load_yaml_config()` 解析 `config.yaml` 的 `streamers` 部分，生成 `STREAMERS` 列表（`[{"name": "洞主", "room_id": "138243"}, ...]`），保持下游代码（录制、监控）无需改动。

**理由**：录制服务和状态监控只需 `name` + `room_id`，不关心上传元数据。通过 YAML → STREAMERS 列表的桥接，下游代码完全无感知。

### 决策 3：上传流水线按主播迭代

**选择**：`upload_to_bilibili()` 改为遍历所有主播，对每个主播：
1. 从文件名前缀匹配该主播的待上传文件
2. 查询该主播的 `StreamSession` 进行场次匹配
3. 使用该主播在 `config.yaml` 中的上传元数据创建/追加投稿

**替代方案**：
- 先收集所有文件，再按文件名分组 → 逻辑类似但主播配置查找不够直观
- 每个主播独立调度上传任务 → 过度并行，增加复杂度

**理由**：串行按主播处理最简单，配合信号量控制并发即可满足需求。

### 决策 4：并发控制使用 `asyncio.Semaphore`

**选择**：在 `uploader.py` 模块级创建 `asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)`，每次调用 biliup/bilitool 上传时 acquire。`MAX_CONCURRENT_UPLOADS` 默认为 1。

**替代方案**：
- asyncio.Queue → 需要额外的 worker 协程，架构更复杂
- 保持串行，不做显式控制 → 当前已是串行，但未来多主播并行处理时会失控

**理由**：Semaphore 最轻量，一行 `async with semaphore:` 即可，且可通过配置调节并发数。

### 决策 5：`UploadedVideo` 新增 `streamer_name` 列

**选择**：新增 `streamer_name = Column(String, nullable=True, index=True)`，`nullable=True` 兼容旧数据，新记录写入时必填。

**替代方案**：
- 继续依赖文件名解析 → 脆弱，文件名格式变化即失效
- 新建表关联 → 过度设计

**理由**：简单加一列，查询时可按主播过滤，旧数据 NULL 不影响功能。

### 决策 6：cgroup 集成方式

**选择**：`uploader.py` 中 biliup CLI 调用改用 `subprocess.Popen`，启动后立即将子进程 PID 写入 `/sys/fs/cgroup/biliup-limit/cgroup.procs`（如果 cgroup 存在）。cgroup 不存在时静默跳过，不影响上传功能。

**替代方案**：
- 用 `cgexec` 包装启动命令 → 需要安装 cgroup-tools，增加依赖
- 在 service.sh 中将整个服务进程加入 cgroup → 会限制所有流量，不仅仅是上传

**理由**：只对 biliup 子进程限速，精确度最高。cgroup 不存在时优雅降级，开发环境（macOS）无影响。

## Risks / Trade-offs

- **[配置迁移]** `config.yaml` 结构变更需要用户手动迁移 → 提供迁移说明文档，校验逻辑兼容旧格式并给出明确错误提示
- **[文件名解析依赖]** 按主播名匹配文件依赖文件名前缀格式（`{主播名}录播...`） → 录制服务已固定此格式，风险可控
- **[cgroup 权限]** 写入 cgroup.procs 需要 root 或适当权限 → 失败时仅 log warning，不阻断上传
- **[数据库迁移]** SQLite 加列简单，但如果已有大量 `UploadedVideo` 记录，旧记录 `streamer_name` 为 NULL → 查询时需处理 NULL 情况
- **[单点串行]** `MAX_CONCURRENT_UPLOADS=1` 意味着多主播上传完全串行，高峰期可能排队较长 → 可调高并发数，由用户根据实际情况权衡
