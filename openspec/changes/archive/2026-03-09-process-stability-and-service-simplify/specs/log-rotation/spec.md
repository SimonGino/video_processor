## ADDED Requirements

### Requirement: 日志按服务分文件记录
系统 SHALL 将主服务和录制服务的日志分别写入独立的日志文件。

#### Scenario: 主服务日志独立记录
- **WHEN** 主服务（uvicorn）产生日志输出
- **THEN** 日志 SHALL 写入主服务日志文件（`douyu-to-bilibili-suite.log`）

#### Scenario: 录制服务日志独立记录
- **WHEN** 录制服务（recording_service.py）产生日志输出
- **THEN** 日志 SHALL 写入录制服务日志文件（`douyu-to-bilibili-suite_recording.log`）

### Requirement: 日志自动清理
系统 SHALL 自动清理超过保留时间的日志文件，保留时间与 `DELETE_UPLOADED_FILES_DELAY_HOURS` 配置项一致。

#### Scenario: 启动时清理过期日志
- **WHEN** `service.sh start` 执行时
- **THEN** SHALL 清理修改时间超过 `DELETE_UPLOADED_FILES_DELAY_HOURS` 小时的旧日志文件

#### Scenario: 守护循环重启时清理过期日志
- **WHEN** 守护循环因子进程崩溃而执行重启
- **THEN** SHALL 在重启前清理过期的旧日志文件

#### Scenario: 配置读取失败时使用默认值
- **WHEN** 从 `config.py` 读取 `DELETE_UPLOADED_FILES_DELAY_HOURS` 失败
- **THEN** SHALL 使用默认值 24 小时作为日志保留时间

### Requirement: 日志按日期轮转
系统 SHALL 对日志文件进行按日期轮转，防止单个日志文件无限增长。

#### Scenario: 日期变更时轮转日志
- **WHEN** 守护循环检测到日期变更（或重启时发现当前日志文件的创建日期非今天）
- **THEN** SHALL 将当前日志文件重命名为带日期后缀的归档文件（如 `service.log.2024-01-01`），并创建新的日志文件继续写入
