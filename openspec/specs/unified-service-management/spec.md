## ADDED Requirements

### Requirement: 统一 start 命令同时启动所有服务
`service.sh start` SHALL 同时启动主服务和录制服务，用户无需分别执行启动命令。

#### Scenario: 首次启动
- **WHEN** 用户执行 `service.sh start` 且无服务在运行
- **THEN** SHALL 同时启动主服务和录制服务的守护循环，输出两个服务的启动状态

#### Scenario: 服务已在运行
- **WHEN** 用户执行 `service.sh start` 且服务已在运行
- **THEN** SHALL 提示服务已在运行，不重复启动

### Requirement: 统一 stop 命令同时停止所有服务
`service.sh stop` SHALL 同时停止主服务和录制服务。

#### Scenario: 正常停止
- **WHEN** 用户执行 `service.sh stop` 且服务在运行
- **THEN** SHALL 向守护进程发送 TERM 信号，守护进程转发信号给所有子进程，等待优雅退出（超时后强制终止），清理 PID 文件

#### Scenario: 服务未运行
- **WHEN** 用户执行 `service.sh stop` 且服务未运行
- **THEN** SHALL 提示服务未运行

### Requirement: 统一 restart 命令
`service.sh restart` SHALL 先停止再启动所有服务。

#### Scenario: 正常重启
- **WHEN** 用户执行 `service.sh restart`
- **THEN** SHALL 先执行 stop 逻辑，成功后执行 start 逻辑

#### Scenario: 服务未运行时重启
- **WHEN** 用户执行 `service.sh restart` 且服务未运行
- **THEN** SHALL 直接执行 start 逻辑

### Requirement: 统一 status 命令显示所有服务状态
`service.sh status` SHALL 显示主服务和录制服务的运行状态。

#### Scenario: 所有服务运行中
- **WHEN** 用户执行 `service.sh status` 且所有服务在运行
- **THEN** SHALL 显示主服务和录制服务的 PID、运行时间等信息

#### Scenario: 部分服务异常
- **WHEN** 用户执行 `service.sh status` 且某个子服务未运行
- **THEN** SHALL 分别显示各服务的状态，标识出异常的服务

### Requirement: 统一 logs 命令查看日志
`service.sh logs [N]` SHALL 显示所有服务的日志。

#### Scenario: 查看日志
- **WHEN** 用户执行 `service.sh logs` 或 `service.sh logs 100`
- **THEN** SHALL 显示主服务和录制服务的日志（默认各 50 行），日志按服务分段显示

## REMOVED Requirements

### Requirement: 独立录制服务管理命令
**Reason**: 统一为 start/stop/restart/status/logs 五个命令，不再需要单独的 `start-recording`/`stop-recording`/`restart-recording`/`status-recording`/`logs-recording` 命令
**Migration**: 使用 `service.sh start` 替代 `service.sh start-recording`，其他命令类推
