## ADDED Requirements

### Requirement: 服务启动时主播已在线自动创建 session

当 `scheduled_log_stream_end` 定时任务检测到主播当前在线（`monitor.is_live() == True`），但 `detect_change()` 返回 None（无状态变化），且数据库中该主播不存在 open session（`start_time IS NOT NULL AND end_time IS NULL`）时，系统 SHALL 自动创建一条新的 `StreamSession` 记录，`start_time` 使用当前时间减去 `STREAM_START_TIME_ADJUSTMENT` 分钟，`end_time` 为 NULL。

#### Scenario: 服务启动时主播已在直播且无 open session
- **WHEN** 服务启动后首次执行 `scheduled_log_stream_end`，监控器状态为 live，数据库中无该主播的 open session
- **THEN** 系统创建一条 `StreamSession`（start_time = 当前时间 - STREAM_START_TIME_ADJUSTMENT，end_time = NULL），并记录日志

#### Scenario: 主播在线但已有 open session
- **WHEN** `scheduled_log_stream_end` 检测到主播在线，且数据库中已存在该主播的 open session
- **THEN** 系统不创建新 session，不做任何修改

#### Scenario: 主播不在线
- **WHEN** `scheduled_log_stream_end` 检测到主播离线，`detect_change()` 返回 None
- **THEN** 系统不创建任何 session
