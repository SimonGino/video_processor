## 1. 进程守护机制

- [x] 1.1 在 `service.sh` 中实现守护循环函数 `run_with_supervisor()`，包裹进程启动命令，异常退出后等待 5 秒自动重启
- [x] 1.2 实现快速崩溃保护逻辑：60 秒内连续崩溃超过 5 次则停止重启并记录错误
- [x] 1.3 实现信号转发：守护进程捕获 TERM/INT 信号后转发给子进程，确保 `stop` 时不触发自动重启

## 2. 统一服务管理

- [x] 2.1 重写 `start_service()` 函数：同时启动主服务和录制服务的守护循环（两个后台子 shell），记录守护进程 PID
- [x] 2.2 重写 `stop_service()` 函数：通过守护进程 PID 停止所有服务，优雅退出超时后强制终止
- [x] 2.3 更新 `restart_service()` 函数：调用新的 stop + start
- [x] 2.4 重写 `status_service()` 函数：同时显示主服务和录制服务的运行状态
- [x] 2.5 重写 `logs_service()` 函数：分段显示主服务日志和录制服务日志
- [x] 2.6 移除所有独立的录制服务命令（`start-recording`、`stop-recording`、`restart-recording`、`status-recording`、`logs-recording`）及相关函数
- [x] 2.7 更新 `show_help()` 和 case 分支，只保留 start/stop/restart/status/logs

## 3. 日志管理

- [x] 3.1 实现 `clean_old_logs()` 函数：从 `config.py` 读取 `DELETE_UPLOADED_FILES_DELAY_HOURS`，用 `find -mtime` 清理超期日志文件，读取失败时 fallback 为 24 小时
- [x] 3.2 实现日志按日期轮转：守护循环每次重启时检查当前日志文件日期，非今日则重命名为带日期后缀的归档文件
- [x] 3.3 在 `start_service()` 中调用 `clean_old_logs()`，在守护循环每次重启子进程前也调用

## 4. 更新文档与配置

- [x] 4.1 更新 `CLAUDE.md` 中 service.sh 相关的命令说明，移除独立录制服务命令的文档
- [x] 4.2 在 `config.py` 的 `DELETE_UPLOADED_FILES_DELAY_HOURS` 注释中说明该值也用于日志保留时间

## 5. 测试验证

- [ ] 5.1 手动测试 `service.sh start`：确认主服务和录制服务同时启动
- [ ] 5.2 手动测试进程守护：`kill` 子进程后确认自动重启
- [ ] 5.3 手动测试 `service.sh stop`：确认所有进程干净退出
- [ ] 5.4 验证日志清理：创建过期日志文件，执行 start 后确认被清理
