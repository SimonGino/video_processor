## Why

进程偶尔会自动挂掉，导致录制中断、文件上传失败等异常情况无法自动恢复。同时 `service.sh` 命令过多（start/stop/restart 分别有主服务和录制服务两套），日志文件持续增长无清理机制。需要提升进程稳定性并简化运维体验。

## What Changes

- 合并主服务和录制服务为统一进程管理，`service.sh` 只保留 `start`、`stop`、`restart`、`status`、`logs` 五个命令
- 添加进程自动重启/守护机制，进程挂掉后自动拉起
- 日志按服务分开记录（主服务日志 + 录制服务日志），但增加日志自动清理，清理周期与 `DELETE_UPLOADED_FILES_DELAY_HOURS` 配置项保持一致
- 增强异常恢复能力：进程重启后能继续处理未完成的上传任务（基于现有 DB 状态恢复）

## Capabilities

### New Capabilities
- `process-supervisor`: 进程守护与自动重启机制，确保主服务和录制服务挂掉后自动恢复
- `log-rotation`: 日志自动清理机制，清理周期与已上传文件保留时间 (`DELETE_UPLOADED_FILES_DELAY_HOURS`) 一致
- `unified-service-management`: 统一的 service.sh，合并主服务和录制服务的启停管理，只暴露 start/stop/restart/status/logs 命令

### Modified Capabilities

## Impact

- **service.sh**: 完全重写，移除 `start-recording`/`stop-recording`/`restart-recording`/`status-recording`/`logs-recording` 等命令，**BREAKING**
- **recording_service.py**: 可能需要调整启动方式以适配统一进程管理
- **app.py**: 可能集成录制服务的启动逻辑，或由 supervisor 统一管理
- **config.py**: `DELETE_UPLOADED_FILES_DELAY_HOURS` 复用为日志保留时长
