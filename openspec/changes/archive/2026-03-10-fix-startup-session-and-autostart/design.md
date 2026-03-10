## Context

当前直播场次（StreamSession）记录依赖 `scheduled_log_stream_end` 中的 `detect_change()` 方法，该方法只在状态发生**转换**时返回结果。如果服务启动时主播已在直播，监控器初始化为 `live`，后续轮询都是 `live → live`，不触发转换，永远不会创建 session 记录。

`service.sh` 目前仅支持 `start/stop/restart/status/logs` 命令，没有系统级自启注册能力。服务器重启后需要人工登录执行 `./service.sh start`。

## Goals / Non-Goals

**Goals:**
- 服务启动后，首次状态检测发现主播在线时，自动在数据库中创建一条 open session
- 提供 `./service.sh install` 和 `./service.sh uninstall` 命令管理 systemd 开机自启

**Non-Goals:**
- 不修改 `StreamStatusMonitor` 或 `detect_change()` 的核心逻辑
- 不支持 systemd 以外的 init 系统（如 SysVinit、OpenRC）
- 不处理 `.flv.part` 孤儿文件清理（属于另一个问题）

## Decisions

### 1. 在 `scheduled_log_stream_end` 中增加首次在线检测

**方案**：在 `detect_change()` 返回 None（无变化）时，额外检查：如果当前状态为 live，且数据库中该主播没有 open session（`end_time IS NULL`），则自动创建一条。

**为什么不在 `app.py` 启动时创建**：启动逻辑在 `app.py` 的 `startup` 事件中，和调度器初始化耦合在一起。放在 `scheduled_log_stream_end` 中更自然——它本身就是负责 session 管理的任务，且在首次 10 分钟轮询时就会触发。

**为什么不修改 `detect_change()`**：`detect_change()` 是纯状态检测方法，加入数据库逻辑会破坏职责分离。

### 2. systemd unit 文件由 `service.sh install` 动态生成

**方案**：`install` 命令根据当前脚本路径动态生成 `/etc/systemd/system/douyu-bilibili.service`，使用 `Type=forking`（因为 `service.sh start` 启动后台守护进程后立即返回）。

**为什么不提供静态 unit 文件**：项目可能部署在不同路径，动态生成避免用户手动修改路径。

## Risks / Trade-offs

- **重复 session 风险** → 创建前先查询是否已有 open session，有则跳过。这个查询本身已在现有逻辑中使用，成本极低。
- **start_time 精度** → 首次检测创建的 session，其 start_time 可能比实际开播时间晚最多 10 分钟（一个轮询周期）。使用与现有逻辑相同的 `STREAM_START_TIME_ADJUSTMENT` 回调来缓解。
- **systemd 权限** → `install` 命令需要 root 权限写入 `/etc/systemd/system/`，命令中会检查并提示。
