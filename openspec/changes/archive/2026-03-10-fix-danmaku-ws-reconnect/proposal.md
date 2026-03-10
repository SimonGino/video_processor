## Why

弹幕采集器 `DouyuDanmakuCollector` 在 WebSocket 连接中途断开时（网络抖动、服务端断开），直接退出采集循环，导致该段剩余时间内的弹幕全部丢失。视频录制不受影响，但弹幕 XML 会出现大段空白。在长时间录制（1 小时 segment）中，一次短暂的网络中断就可能丢失数十分钟的弹幕。

## What Changes

- `DouyuDanmakuCollector` 增加 WebSocket 断线自动重连机制：连接断开后在剩余录制时间内尝试重新连接，重连成功后继续采集弹幕
- 重连策略使用指数退避（exponential backoff），避免频繁重试消耗资源
- 新增可配置的最大重连次数限制，防止无限重试

## Capabilities

### New Capabilities
- `danmaku-ws-reconnect`: 弹幕 WebSocket 断线自动重连——连接中断后自动尝试重新建立连接并继续采集，使用指数退避策略，支持配置最大重连次数

### Modified Capabilities

（无）

## Impact

- **recording/danmaku_collector.py**：`DouyuDanmakuCollector` 的 `collect()` 方法需要重构消息循环，增加外层重连循环
- **config.py**：可选新增重连相关配置常量（最大重连次数、初始退避时间）
- **tests/**：需新增重连场景的测试用例
