## ADDED Requirements

### Requirement: 进程异常退出后自动重启
`service.sh` 的守护循环 SHALL 在被管理的 Python 进程（主服务或录制服务）异常退出后自动重启该进程。

#### Scenario: 主服务进程崩溃后自动恢复
- **WHEN** 主服务进程（uvicorn）异常退出（退出码非 0）
- **THEN** 守护循环 SHALL 等待 5 秒后自动重新启动主服务进程，并在日志中记录重启事件

#### Scenario: 录制服务进程崩溃后自动恢复
- **WHEN** 录制服务进程（recording_service.py）异常退出
- **THEN** 守护循环 SHALL 等待 5 秒后自动重新启动录制服务进程，并在日志中记录重启事件

#### Scenario: 正常停止时不重启
- **WHEN** 用户通过 `service.sh stop` 发送停止信号
- **THEN** 守护循环 SHALL 收到信号后终止子进程并退出，不触发自动重启

### Requirement: 快速崩溃保护
守护循环 SHALL 检测连续快速崩溃并停止自动重启，防止日志膨胀和资源浪费。

#### Scenario: 连续快速崩溃触发保护
- **WHEN** 被管理的进程在 60 秒内连续崩溃超过 5 次
- **THEN** 守护循环 SHALL 停止自动重启，在日志中记录错误信息，并退出

#### Scenario: 间隔正常的崩溃不触发保护
- **WHEN** 进程崩溃后成功运行超过 60 秒再次崩溃
- **THEN** 守护循环 SHALL 重置崩溃计数器，正常执行自动重启
