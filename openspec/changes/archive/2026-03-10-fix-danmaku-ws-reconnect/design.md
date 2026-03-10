## Context

当前 `DouyuDanmakuCollector.collect()` 的消息循环结构为单层：连接 WebSocket → 发送登录/加入房间 → 循环接收消息直到超时或连接关闭。当 WebSocket 连接因网络抖动或服务端主动断开而中断时，消息循环直接退出（`break`），剩余录制时间内不再采集弹幕。

弹幕采集与视频录制是 `segment_pipeline.py` 中通过 `asyncio.gather()` 并行运行的两个 task，各自独立。弹幕采集失败不影响视频录制，但会导致该 segment 的 XML 弹幕文件不完整。

## Goals / Non-Goals

**Goals:**
- WebSocket 断线后在剩余录制时间内自动重连并继续采集弹幕
- 使用指数退避策略避免频繁重试
- 重连后正确重新发送登录和加入房间请求
- 重连后弹幕时间偏移量（offset）保持连续正确（基于录制开始时间计算）
- 可配置最大重连次数

**Non-Goals:**
- 不处理初始连接失败的重试（首次连接失败仍返回 0）
- 不保证断线期间的弹幕不丢失（断线到重连成功之间的弹幕无法恢复）
- 不实现弹幕去重（重连后可能有极少量重复，但实际可能性很低）

## Decisions

### 决策 1：重连循环的结构

**选择：外层 while 循环包裹连接和消息处理**

在 `collect()` 方法中，将当前的 WebSocket 连接 + 消息循环用一个外层 `while` 循环包裹。当内层消息循环因连接断开退出时，外层循环检查是否还有剩余时间，如果有则尝试重新连接。

```
while 剩余时间 > 0 and 重连次数 < 最大次数:
    try:
        ws = connect()
        login + joingroup
        while 剩余时间 > 0:
            receive messages...
    except 连接异常:
        退避等待
        重连次数 += 1
```

| 方案 | 优点 | 缺点 |
|------|------|------|
| 外层 while 循环（选择） | 改动最小，逻辑清晰 | 无 |
| 独立重连管理器类 | 可复用 | 过度设计，当前只有一处使用 |

### 决策 2：退避策略

**选择：指数退避，初始 2 秒，上限 30 秒**

`delay = min(initial_delay * 2^attempt, max_delay)`

- 初始延迟 2 秒，足够应对短暂网络抖动
- 上限 30 秒，避免在长时间断线时等待过久浪费剩余录制时间
- 不添加随机抖动（jitter），因为只有单个客户端连接，无需防止惊群

### 决策 3：配置项位置

**选择：config.py 新增常量**

- `DANMAKU_WS_MAX_RECONNECTS = 5`：最大重连次数（0 表示不重连）
- `DANMAKU_WS_RECONNECT_BASE_DELAY = 2`：重连初始退避秒数

放在现有的 `--- 弹幕采集配置 ---` 区块下。

### 决策 4：时间偏移量处理

**选择：复用原始 start 时间戳**

`start = time.monotonic()` 在 `collect()` 入口处设置一次，重连后不重置。这确保弹幕的 `offset_seconds` 始终相对于录制开始时间计算，与视频流时间轴对齐。

## Risks / Trade-offs

- **[弹幕丢失]** 断线到重连成功之间的弹幕不可恢复 → 这是预期行为，比完全丢失剩余弹幕好得多
- **[heartbeat task 生命周期]** 每次重连需要取消旧 heartbeat task、创建新的 → 在重连循环内管理，确保 finally 清理
- **[aiohttp session 复用]** 重连时复用同一个 `aiohttp.ClientSession` → session 是连接池管理器，可安全复用
