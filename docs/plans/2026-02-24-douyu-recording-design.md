# 斗鱼录制（FLV + B站XML弹幕）设计方案

> 日期：2026-02-24  
> 目标：补齐“监控 → 录制 → 弹幕 → 处理 → 上传”的完整链路，上游新增内建录制能力；下游处理/上传模块尽量不改。

## 1. 背景与现状

当前项目已经具备：

- 直播状态监控：`stream_monitor.py` 轮询斗鱼 `betard` API，判断开播/下播并记录场次（`stream_sessions`）
- 处理链路：`danmaku.py`（XML→ASS）→ `encoder.py`（FLV+ASS→MP4）→ `uploader.py`（分场次上传B站）
- 文件交接约定：处理阶段会跳过正在录制的 `*.part` 文件

缺口是最上游“录制”（产出 `FLV + XML`）目前依赖外部工具。我们要把这个能力内建，形成闭环。

## 2. 目标与非目标

### 2.1 目标（MVP）

1. 自动录制斗鱼直播流，产出 `.flv`
2. 同步采集弹幕，产出 **B站XML**（`<d p="...">text</d>`，可被 `dmconvert` 消费）
3. 按固定时长切片：**默认 60 分钟**，且可配置
4. 文件落盘遵守 `.part` 原子完成：录制中 `*.part`，完成后 `rename` 为无后缀版本，保证下游稳定消费
5. 录制服务与现有 `app` 进程解耦：新增独立进程 `recording_service`（用户选择 B）

### 2.2 非目标（后续迭代）

- 弹幕渲染进画面（DanmakuRender 风格）
- 自研斗鱼复杂签名/流地址解析算法（优先交给 yt-dlp 社区维护）
- 多平台统一录制框架（只做斗鱼）

## 3. 核心技术选择

### 3.1 流 URL 获取：`yt-dlp --get-url`

原因：

- 避免自研斗鱼签名/参数生成的高维护成本
- yt-dlp 社区维护活跃，规则更新快

设计要求：

- 支持多行 URL 输出：按优先级选择（优先 `flv`/RTMP，其次 HLS `m3u8`）
- 对 yt-dlp 调用做超时控制与重试
- 依赖新增：`yt-dlp`（作为 Python 依赖安装，或系统命令均可）

### 3.2 录制：FFmpeg 子进程，`-c copy`

命令形态（示意）：

```bash
ffmpeg -hide_banner -y \
  -i "<STREAM_URL>" \
  -c copy \
  -t <SEGMENT_SECONDS> \
  "<OUTPUT>.flv.part"
```

- `-c copy` 几乎无 CPU 开销（不转码）
- 每个切片启动一个子进程，切片边界由我们控制，退出后再 `rename` 文件
- 失败恢复：FFmpeg 退出后若主播仍在线，等待固定间隔重启录制（新文件名新时间戳，自然成为新分P）

### 3.3 弹幕采集：`aiohttp` WebSocket + STT 协议编解码

目标端点：

- `wss://danmuproxy.douyu.com:8506/`

要点：

- 通过 STT 二进制封包发送 `loginreq` / `joingroup` / `keeplive`
- 解析服务端消息，提取 `type=chatmsg`，转换为 B站 XML `<d p="...">`
- 心跳：30 秒一次
- 异常：断线重连（5 秒间隔，最多 3 次），超过则本片“放弃弹幕但不中断视频录制”，并保证 XML 能正常封口落盘

## 4. 总体架构

### 4.1 进程划分

- `app`（现有）：API + 定时任务 + 场次记录 + 处理/上传触发
- `recording_service`（新增）：只负责“从斗鱼拿到实时数据 → 产出稳定落盘文件”

两个进程共享：

- `config.py`（主播列表、目录、切片长度等）
- `stream_monitor.py`（开播/下播检测逻辑，可复用）

### 4.2 目录与文件规范

输出目录：

- 视频与弹幕落盘到：`config.PROCESSING_FOLDER`

命名规则（对齐现有 `uploader.get_timestamp_from_filename()`）：

- `base = "{streamer_name}录播{YYYY-MM-DDTHH_mm_ss}"`
- 视频：`{base}.flv.part` → 完成后 `rename` 为 `{base}.flv`
- 弹幕：`{base}.xml.part` → 完成后 `rename` 为 `{base}.xml`

原子完成约定：

- 只有当 FLV/XML 都封口并 `rename` 成无 `.part` 后，下游才会处理
- 录制过程中崩溃/断电：保留 `.part`，下游会自动跳过；后续可由清理策略处理（已有 `cleanup_small_files()`）

## 5. 模块拆分（最小可维护）

建议新增目录：`recording/`

- `recording/recording_service.py`
  - 主入口：读取 `config.STREAMERS`，为每个 streamer 启动一个录制协程
  - 负责整体生命周期（启动、停止、SIGTERM）
- `recording/url_resolver.py`
  - `YtDlpResolver`: `resolve(room_url) -> str`（选出一个可录制 URL）
  - 处理多行输出、超时、重试、日志
- `recording/ffmpeg_recorder.py`
  - `FfmpegRecorder`: `record(url, output_part_path, duration_seconds) -> exit_code`
  - 负责子进程启动/等待/超时/终止
- `recording/stt_codec.py`
  - STT 二进制帧 `pack(text) -> bytes` / `unpack(bytes) -> str`
  - 负责 `@A/@S` 转义处理
- `recording/danmaku_collector.py`
  - `DouyuDanmakuCollector`: `run(room_id, xml_part_path, segment_clock) -> stats`
  - 负责 WS 连接、登录入组、心跳、消息解析与写盘
- `recording/xml_writer.py`
  - `BilibiliXmlWriter`: `open() / write_danmaku(ts, text, color=...) / close()`
  - 负责写入 `<i> ... </i>`，定期 flush，最终封口

> 说明：把 “STT codec” 与 “XML writer” 单独抽出来，是为了把可测的纯逻辑从网络/进程 I/O 中剥离，降低圈复杂度并提升复用。

## 6. 状态机与时序

每个 streamer 一条状态机：

1. `OFFLINE`：轮询 `StreamStatusMonitor.check_is_streaming()`
2. `ONLINE_STARTING`：使用 yt-dlp 解析流 URL（失败重试）
3. `RECORDING_SEGMENT`：并行运行：
   - FFmpeg 录制到 `flv.part`（固定 60min 或配置）
   - WS 弹幕写到 `xml.part`（同一时间窗）
4. 段结束：先停止弹幕采集并封口 XML，再等待 FFmpeg 退出/终止，最后 `rename` 两个 `.part` 为正式文件
5. 若 FFmpeg 提前退出：
   - 再确认主播是否在线：在线则等待 10 秒进入 `ONLINE_STARTING`（新段）
   - 不在线则回到 `OFFLINE`

## 7. 错误恢复策略

- **流中断（FFmpeg 退出）**：检测仍在线 → 10 秒后重启新段；否则结束录制
- **弹幕断连**：5 秒重连，最多 3 次；失败则本段产出“空/不完整弹幕”，但仍保证 XML 结构完整并落盘
- **程序重启**：启动时为每个 streamer 立即 `initialize()` 检测状态；在播则马上开段
- **强制停止**：捕获 SIGTERM，优雅停止 FFmpeg，封口 XML，避免产生“无结尾 XML”

## 8. 配置项（建议新增）

在 `config.py` 增加（命名可调整）：

- `RECORDING_ENABLED: bool = True`
- `RECORDING_SEGMENT_MINUTES: int = 60`
- `RECORDING_RETRY_DELAY_SECONDS: int = 10`
- `YTDLP_PATH: str = "yt-dlp"`
- `DANMAKU_WS_URL: str = "wss://danmuproxy.douyu.com:8506/"`
- `DANMAKU_HEARTBEAT_SECONDS: int = 30`
- `DANMAKU_RECONNECT_DELAY_SECONDS: int = 5`
- `DANMAKU_RECONNECT_MAX: int = 3`

## 9. 服务管理（与现有 service.sh 集成）

由于采用“独立进程”方案，建议：

- 新增 `recording_service.py`（项目根目录入口），运行方式：`uv run python recording_service.py`
- 扩展 `service.sh`：
  - 增加第二套 PID/LOG：`video_processor_recording.pid` / `video_processor_recording.log`
  - 增加子命令：`start-recording|stop-recording|status-recording|logs-recording`
  - `start` 可选同时启动两个进程（或保持解耦，分别启动）

## 10. 测试策略（按阶段拆分）

目标：把可离线验证的逻辑尽量放到单元测试；网络/进程相关通过 stub 做集成测试；真实斗鱼环境仅保留手工 E2E。

### 10.1 测试框架与目录约定

- 引入：`pytest`、`pytest-asyncio`
- 目录结构：

```
tests/
  unit/
  integration/
  e2e/
  bin/                 # stub binaries used by integration tests
  fixtures/
```

> 单元测试不依赖外网、不依赖 ffmpeg/yt-dlp；集成测试通过 stub 模拟子进程与 WS 服务。

### 10.2 阶段一：纯逻辑单元测试（unit）

1) STT 编解码

- 文件：`tests/unit/test_stt_codec.py`
- 覆盖：
  - `pack()/unpack()` roundtrip
  - `@A/@S` 转义与反转义
  - 边界：空字符串、包含 `/`、包含 `@`、包含中文

2) 消息解析（k-v 字典）

- 文件：`tests/unit/test_douyu_message_parser.py`
- 覆盖：
  - `type@=chatmsg/.../txt@=.../` 解析为 dict
  - 缺字段容错（无 txt、无 nn）

3) B站 XML 生成兼容性

- 文件：`tests/unit/test_xml_writer.py`
- 覆盖：
  - 生成的 XML 可被 `xml.etree.ElementTree` parse
  - `<d p="...">` 字段数量符合预期
  - 文本 XML 转义正确（`& < >` 等）

4) 与 `dmconvert` 的契约测试（推荐）

- 文件：`tests/unit/test_dmconvert_contract.py`
- 方法：写一份最小 XML（含 2 条 `<d>`），调用 `dmconvert.convert_xml_to_ass(...)`，断言输出 `.ass` 存在且包含弹幕文本。

### 10.3 阶段二：网络 I/O 集成测试（integration）

1) 弹幕采集器 + 本地 WS 服务器

- 文件：`tests/integration/test_danmaku_collector_ws.py`
- 做法：
  - 用 `aiohttp.web` 起一个本地 WS server
  - server 侧发送“已打包的 STT 二进制帧”，模拟 `chatmsg`
  - 断言 collector 写出了 `.xml.part`，内容包含对应 `<d>`

2) 心跳与重连

- 文件：`tests/integration/test_danmaku_collector_reconnect.py`
- 做法：server 主动断开连接 → collector 触发重连 → 断言重连次数与最终策略符合配置

### 10.4 阶段三：进程编排集成测试（integration）

1) URL 解析器（yt-dlp stub）

- 文件：`tests/integration/test_url_resolver_ytdlp_stub.py`
- stub：`tests/bin/yt-dlp`（可执行脚本，输出固定 URL；或返回非 0）
- 覆盖：
  - 多行输出的选择逻辑
  - 超时/非 0 返回码处理

2) FFmpeg 录制器（ffmpeg stub）

- 文件：`tests/integration/test_ffmpeg_recorder_stub.py`
- stub：`tests/bin/ffmpeg`（模拟写文件并 sleep 一小段时间退出）
- 覆盖：
  - 输出 `.flv.part` 是否生成
  - 超时终止逻辑（若实现）

3) 单切片端到端（离线）

- 文件：`tests/integration/test_segment_pipeline_offline.py`
- 串起来：resolver(stub) → recorder(stub) + collector(local ws) → 最终 `.part` rename 成正式文件
- 断言：最终目录存在 `.flv` 和 `.xml`，且 `.part` 不残留

### 10.5 阶段四：真实环境 E2E（手工）

目录：`tests/e2e/README.md`

建议步骤：

1. 配好 `STREAMERS` 与目录
2. 运行 `recording_service` 录 2 分钟（可临时把 `RECORDING_SEGMENT_MINUTES` 设为 1）
3. 观察生成文件、下游处理链路是否正常跑通

> E2E 不进 CI，只作为部署验收清单。

## 11. 风险与兜底

- yt-dlp 规则变化导致取流失败：快速升级依赖；必要时 fallback 到“手动提供流 URL”的配置项
- WS/STT 协议变更：通过 `stt_codec` 单点维护；单测/集成测能快速定位
- 仅有 m3u8 无 flv：优先选择 flv；如只剩 m3u8，先记录日志并跳过（避免下游强依赖 `.flv` 破坏链路）

---

## 12. 决策回顾

- 录制切片：60 分钟可配置（已确认）
- 架构：独立 `recording_service` 进程（已确认）
- URL：yt-dlp（已确认）
- 弹幕：aiohttp WS + STT（已确认）

