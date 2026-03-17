## ADDED Requirements

### Requirement: biliup 子进程自动加入 cgroup
当使用 biliup CLI 后端时，启动 biliup 子进程后须尝试将其 PID 写入 `/sys/fs/cgroup/biliup-limit/cgroup.procs`，以配合 OS 层 tc 限速。

#### Scenario: cgroup 存在时写入 PID
- **WHEN** `/sys/fs/cgroup/biliup-limit/cgroup.procs` 文件存在
- **THEN** 须将 biliup 子进程的 PID 写入该文件，记录 info 日志

#### Scenario: cgroup 不存在时优雅降级
- **WHEN** `/sys/fs/cgroup/biliup-limit/` 目录不存在
- **THEN** 须静默跳过 cgroup 写入，不影响上传功能，不记录 warning

#### Scenario: cgroup 写入失败（权限不足）
- **WHEN** cgroup 目录存在但写入失败（如权限不足）
- **THEN** 须记录 warning 日志，不阻断上传流程

### Requirement: biliup 调用改用 Popen
biliup CLI 的调用方式须从 `subprocess.run`（同步等待）改为 `subprocess.Popen`（先启动再等待），以便在进程启动后、执行上传前写入 cgroup。

#### Scenario: Popen 启动后写入 cgroup 再等待结果
- **WHEN** 调用 biliup CLI 上传视频
- **THEN** 须先用 `Popen` 启动进程，写入 cgroup PID，然后 `communicate()` 等待完成

#### Scenario: 上传结果解析不变
- **WHEN** biliup CLI 通过 Popen 执行完成
- **THEN** 返回值解析逻辑（成功判断、BVID 提取、限流检测）须与 `subprocess.run` 时完全一致

### Requirement: 限速脚本独立运维
上传带宽限速的 tc + cgroup 规则须通过独立的运维脚本（`scripts/upload-bandwidth-limit.sh`）管理，应用代码不负责创建或销毁 tc/cgroup 规则。

#### Scenario: 未设置限速时正常运行
- **WHEN** 未执行限速脚本（cgroup 不存在）
- **THEN** 上传功能完全正常，无任何性能影响

#### Scenario: 限速脚本与应用独立操作
- **WHEN** 运维人员执行 `sudo ./scripts/upload-bandwidth-limit.sh setup 10mbit`
- **THEN** 后续 biliup 进程自动受限速约束，无需重启应用
