## Context

当前系统由两个独立进程组成：主服务 (`app.py` via uvicorn) 和录制服务 (`recording_service.py`)，通过 `service.sh` 分别管理。存在以下问题：

1. 进程偶尔自动挂掉，无自动恢复机制，需人工介入
2. `service.sh` 有 10 个命令（start/stop/restart/status/logs × 2），操作复杂
3. 日志文件持续增长，无自动清理机制
4. 进程重启后，中断的上传任务依赖定时任务重新触发，无主动恢复

## Goals / Non-Goals

**Goals:**
- 进程挂掉后自动重启，减少人工干预
- `service.sh` 简化为 start/stop/restart/status/logs 五个命令
- 日志按服务分文件记录，自动按配置时间清理
- 保持现有架构（两个 Python 进程），不做大规模重构

**Non-Goals:**
- 不引入 systemd/supervisord 等外部进程管理工具（保持项目自包含）
- 不合并主服务和录制服务为单进程（它们的生命周期和职责不同）
- 不改变现有的 DB 模型或 API 接口
- 不实现日志按大小轮转（只按时间清理，保持简单）

## Decisions

### 1. 进程守护：shell 层 while-loop 重启

**选择**: 在 `service.sh` 的 start 逻辑中用 `while true` 包裹进程启动，进程退出后自动重启（含退避延迟）。

**替代方案**:
- supervisord/systemd: 功能强大但引入外部依赖，部署环境不一定支持
- Python 内 multiprocessing 守护: 增加代码复杂度，且 Python 进程本身挂了就无法守护

**理由**: shell 层守护最轻量，不引入任何依赖，且能同时守护两个 Python 进程。重启间隔设 5 秒，避免快速崩溃循环。

### 2. 统一启停：start 同时拉起两个进程

**选择**: `start` 命令同时启动主服务和录制服务（各自独立的守护循环），`stop` 同时停止两者。用一个 PID 文件记录守护进程的 PID，守护进程内部管理子进程。

**替代方案**:
- 保留独立命令但简化名称: 仍然需要记忆多个命令
- 合并为单进程: 录制服务是 asyncio.run() 阻塞式，难以合并

**理由**: 用户只需关心「整个套件」的启停，不需要单独管理子服务。

### 3. 日志清理：启动时清理 + 定时清理

**选择**: 在 `service.sh` 启动时和每次守护循环重启时，清理超过 `DELETE_UPLOADED_FILES_DELAY_HOURS` 小时的日志文件。日志仍分两个文件（主服务 `.log` 和录制服务 `.log`）。

**实现方式**: `service.sh` 中读取 `config.py` 的 `DELETE_UPLOADED_FILES_DELAY_HOURS` 值，用 `find -mtime` 清理旧日志。日志采用日期后缀轮转（如 `service.log.2024-01-01`），当前日志始终写入 `service.log`。

**替代方案**:
- logrotate: 需要系统级配置，不够自包含
- Python logging RotatingFileHandler: 只能管理 Python 进程内的日志，无法管理 shell 输出

**理由**: 与现有文件清理配置复用，逻辑一致，用户只需维护一个时间参数。

### 4. 日志保留时间来源

**选择**: 从 `config.py` 中读取 `DELETE_UPLOADED_FILES_DELAY_HOURS` 的值。`service.sh` 通过 `python -c "from config import DELETE_UPLOADED_FILES_DELAY_HOURS; print(DELETE_UPLOADED_FILES_DELAY_HOURS)"` 获取。

**理由**: 避免在 shell 脚本中重复维护配置值，保持单一配置源。

## Risks / Trade-offs

- **[守护循环中快速崩溃]** → 设置最小重启间隔（5秒），并在日志中记录重启次数。连续快速崩溃超过阈值时不再重启，避免日志膨胀。
- **[stop 命令需同时停止多个进程]** → PID 文件记录守护进程 PID，守护进程收到 TERM 信号后转发给子进程，确保干净退出。
- **[config.py 读取失败]** → 日志清理使用默认值 24 小时作为 fallback。
- **[日志按天轮转可能单日过大]** → 当前阶段可接受，后续可按需增加大小限制。属于 Non-Goal。
