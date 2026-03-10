## Why

服务存在两个运维可靠性问题：
1. **上传全部卡死**：当服务启动时主播已在直播，`StreamStatusMonitor` 初始化为 `live` 状态，`detect_change()` 永远检测不到状态转换，导致数据库中无 `StreamSession` 记录，上传流程因找不到直播场次而跳过所有视频。
2. **服务器重启后服务丢失**：`service.sh` 没有提供系统级自启注册功能，服务器意外重启后（如 2026-03-10 05:08 的重启事件）服务不会自动恢复，需要人工介入。

## What Changes

- 在调度器的直播状态检测任务中，当首次检测到主播在线且数据库中无对应的 open session 时，自动创建一条 `StreamSession` 记录
- 在 `service.sh` 中新增 `install` / `uninstall` 子命令，用于注册/注销 systemd 开机自启服务

## Capabilities

### New Capabilities
- `startup-session-creation`: 服务启动后首次检测到主播已在直播时，自动补建 StreamSession 记录，确保上传流程能正确匹配视频到直播场次
- `systemd-autostart`: service.sh 新增 install/uninstall 命令，自动生成 systemd unit 文件并注册开机自启

### Modified Capabilities

（无）

## Impact

- `scheduler.py` — `scheduled_log_stream_end` 函数需增加首次在线时的 session 创建逻辑
- `service.sh` — 新增 `install` / `uninstall` 命令及 systemd unit 文件生成
- 不涉及数据库 schema 变更、API 变更或依赖变更
