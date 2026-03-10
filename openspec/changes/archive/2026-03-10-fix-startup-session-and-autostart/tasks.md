## 1. 启动时自动创建 StreamSession

- [x] 1.1 修改 `scheduler.py` 的 `scheduled_log_stream_end` 函数：在 `detect_change()` 返回 None 且 `monitor.is_live()` 为 True 时，查询数据库是否存在该主播的 open session（`end_time IS NULL`），不存在则创建一条新 session
- [x] 1.2 为新增逻辑编写单元测试：覆盖"启动时在线无 session → 创建"、"已有 open session → 跳过"、"主播离线 → 不创建"三个场景

## 2. service.sh 新增 systemd 自启命令

- [x] 2.1 在 `service.sh` 中新增 `install_service` 函数：检查 root 权限，动态生成 systemd unit 文件到 `/etc/systemd/system/douyu-bilibili.service`，执行 `daemon-reload` 和 `enable`
- [x] 2.2 在 `service.sh` 中新增 `uninstall_service` 函数：停止服务、`disable`、删除 unit 文件、`daemon-reload`
- [x] 2.3 在 `service.sh` 的 `case` 分支和 `show_help` 中注册 `install` / `uninstall` 命令
