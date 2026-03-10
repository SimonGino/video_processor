## ADDED Requirements

### Requirement: WebSocket 断线后自动重连
当弹幕 WebSocket 连接在录制过程中断开时，系统 SHALL 在剩余录制时间内自动尝试重新连接。重连成功后 SHALL 重新发送登录和加入房间请求，然后继续采集弹幕。

#### Scenario: 连接中途断开后重连成功
- **WHEN** 弹幕 WebSocket 连接在录制过程中断开，且剩余录制时间 > 0，且未超过最大重连次数
- **THEN** 系统 SHALL 等待退避时间后尝试重新连接，重连成功后继续采集弹幕写入同一个 XML 文件

#### Scenario: 重连后弹幕时间偏移量保持连续
- **WHEN** 弹幕 WebSocket 成功重连后收到新弹幕
- **THEN** 弹幕的 offset_seconds SHALL 相对于原始录制开始时间计算，而非重连时间

#### Scenario: 首次连接失败不重试
- **WHEN** 弹幕 WebSocket 首次连接即失败
- **THEN** 系统 SHALL 直接返回 0，不进行重连尝试（保持现有行为）

### Requirement: 指数退避策略
重连 SHALL 使用指数退避策略控制重试间隔，公式为 `delay = min(base_delay * 2^attempt, 30)`。

#### Scenario: 退避时间递增
- **WHEN** `DANMAKU_WS_RECONNECT_BASE_DELAY` 为 2 秒
- **THEN** 连续重连的等待时间 SHALL 依次为 2 秒、4 秒、8 秒、16 秒、30 秒（上限）

#### Scenario: 退避时间不超过剩余录制时间
- **WHEN** 退避等待时间大于剩余录制时间
- **THEN** 系统 SHALL 直接结束采集，不再等待重连

### Requirement: 最大重连次数限制
系统 SHALL 通过 `DANMAKU_WS_MAX_RECONNECTS` 配置项限制重连尝试次数。默认值为 5。

#### Scenario: 达到最大重连次数
- **WHEN** 重连尝试次数达到 `DANMAKU_WS_MAX_RECONNECTS`（默认 5 次）
- **THEN** 系统 SHALL 停止重连，结束采集并返回已采集的弹幕数量

#### Scenario: 禁用重连
- **WHEN** `DANMAKU_WS_MAX_RECONNECTS` 设为 0
- **THEN** 系统 SHALL 不进行任何重连尝试，行为与修改前完全一致

### Requirement: 配置项位于 config.py
`DANMAKU_WS_MAX_RECONNECTS` 和 `DANMAKU_WS_RECONNECT_BASE_DELAY` SHALL 定义在 `config.py` 的 `--- 弹幕采集配置 ---` 区块中。

#### Scenario: 配置项存在且有默认值
- **WHEN** 用户未修改重连相关配置
- **THEN** `DANMAKU_WS_MAX_RECONNECTS` 为 5，`DANMAKU_WS_RECONNECT_BASE_DELAY` 为 2

### Requirement: 重连过程记录日志
每次重连尝试 SHALL 记录日志，包含当前重连次数、退避等待时间和剩余录制时间。

#### Scenario: 断线时记录警告日志
- **WHEN** 弹幕 WebSocket 连接断开
- **THEN** 系统 SHALL 记录 WARNING 级别日志，包含断线原因

#### Scenario: 重连成功时记录信息日志
- **WHEN** 弹幕 WebSocket 重连成功
- **THEN** 系统 SHALL 记录 INFO 级别日志，包含重连次数和已采集弹幕数
