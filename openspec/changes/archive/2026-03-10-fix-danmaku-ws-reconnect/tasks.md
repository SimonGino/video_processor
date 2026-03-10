## 1. 配置项

- [x] 1.1 在 `config.py` 的 `--- 弹幕采集配置 ---` 区块新增 `DANMAKU_WS_MAX_RECONNECTS = 5`（int，最大重连次数，0 表示不重连）
- [x] 1.2 在 `config.py` 新增 `DANMAKU_WS_RECONNECT_BASE_DELAY = 2`（int，重连初始退避秒数）

## 2. 重连逻辑实现

- [x] 2.1 重构 `DouyuDanmakuCollector.collect()`：将 WebSocket 连接 + 消息循环用外层 `while` 重连循环包裹，接受 `max_reconnects` 和 `reconnect_base_delay` 参数
- [x] 2.2 实现指数退避等待：`delay = min(base_delay * 2^attempt, 30)`，退避时间超过剩余录制时间时直接结束
- [x] 2.3 重连后重新发送 `loginreq` 和 `joingroup` 请求，创建新的 heartbeat task
- [x] 2.4 确保 `start` 时间戳在重连后不重置，弹幕 offset 保持连续
- [x] 2.5 添加重连过程的日志记录（断线警告、退避等待、重连成功/失败）

## 3. 集成配置

- [x] 3.1 修改 `segment_pipeline.py` 或 `recording_service.py` 中 `DouyuDanmakuCollector` 的调用处，将 `config.DANMAKU_WS_MAX_RECONNECTS` 和 `config.DANMAKU_WS_RECONNECT_BASE_DELAY` 传入

## 4. 测试

- [x] 4.1 编写测试：WebSocket 中途断开后重连成功，验证弹幕继续写入且 offset 连续
- [x] 4.2 编写测试：达到最大重连次数后停止重连，返回已采集数量
- [x] 4.3 编写测试：`max_reconnects=0` 时行为与修改前一致（不重连）
- [x] 4.4 编写测试：退避时间超过剩余录制时间时直接结束
- [x] 4.5 运行完整测试套件确认无回归
