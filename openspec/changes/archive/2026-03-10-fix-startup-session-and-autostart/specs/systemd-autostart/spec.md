## ADDED Requirements

### Requirement: service.sh install 命令注册 systemd 开机自启

`service.sh` SHALL 提供 `install` 子命令，该命令动态生成 systemd unit 文件并注册开机自启。unit 文件 MUST 包含正确的 `WorkingDirectory`、`ExecStart`、`ExecStop` 路径（基于脚本实际位置）。命令 MUST 检查 root 权限，非 root 时提示用户使用 sudo。

#### Scenario: 以 root 身份执行 install
- **WHEN** 用户以 root 身份执行 `./service.sh install`
- **THEN** 系统生成 `/etc/systemd/system/douyu-bilibili.service`，执行 `systemctl daemon-reload` 和 `systemctl enable`，输出成功信息

#### Scenario: 非 root 执行 install
- **WHEN** 用户以非 root 身份执行 `./service.sh install`
- **THEN** 系统输出错误提示，要求使用 sudo 执行

### Requirement: service.sh uninstall 命令注销 systemd 开机自启

`service.sh` SHALL 提供 `uninstall` 子命令，该命令停止服务、禁用自启并删除 unit 文件。

#### Scenario: 以 root 身份执行 uninstall
- **WHEN** 用户以 root 身份执行 `./service.sh uninstall`
- **THEN** 系统执行 `systemctl stop`、`systemctl disable`，删除 unit 文件，执行 `daemon-reload`，输出成功信息

#### Scenario: unit 文件不存在时执行 uninstall
- **WHEN** 用户执行 `./service.sh uninstall` 但 unit 文件不存在
- **THEN** 系统输出提示信息，说明服务未安装
