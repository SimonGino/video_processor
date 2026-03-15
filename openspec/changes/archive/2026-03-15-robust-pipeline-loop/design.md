## Design Decisions

### 失败计数器：模块级 dict，不用数据库

用模块级 `_failure_counts: dict[str, int]` 记录每个文件路径的连续失败次数。

**为什么不用数据库**：处理管线的同步函数（`encode_video`、`convert_danmaku`）运行在 `run_in_executor` 的线程池中，引入 async DB 操作会大幅增加复杂度。文件系统状态（文件在 `processing/` 还是 `failed/`）本身就是持久化的，内存计数器只需要在运行期间保持就够了——重启后，之前已被移到 `failed/` 的文件不会回来，还在 `processing/` 的文件则重新给 3 次机会。

### 失败阈值：3 次

默认 `MAX_RETRY_COUNT = 3`，可在 `config.py` 中配置。3 次意味着一个坏文件最多浪费 3 轮管线周期（默认 3 小时）就会被隔离。

### 无弹幕 FLV 的等待机制："seen once" 标记

```
第 1 轮: 发现 FLV 无 XML/ASS，无 .part → 标记为 "orphan_seen"
第 2 轮: 仍然无 XML/ASS → 确认为无弹幕，走直通路径
```

用同一个模块级 dict（或独立 set）记录 "已见过一次的孤立 FLV"。等一轮是为了避免以下竞态：

```
                 时间线
录制完成 ─────────────┐
                       ├── FLV 写完，.part 删除
                       ├── XML 还在写最后几条弹幕  ← 如果这里管线恰好跑了
                       ├── XML 写完
```

等一轮（默认 60 分钟）后再处理，给了 XML 充足的写入时间。

### 无弹幕 FLV 的处理路径

```
orphan FLV
│
├── SKIP_VIDEO_ENCODING = True
│   └── 直接 move 到 upload/ （与现有逻辑一致）
│
└── SKIP_VIDEO_ENCODING = False
    └── FFmpeg 编码但不加 -vf subtitles（纯转码，无弹幕烧录）
        └── move 到 upload/
```

### 编码质量调整

仅改 `-global_quality 32` → `-global_quality 35`，其他参数不动。VideoToolbox 备用路径暂不调整（macOS 开发环境，不是生产压制机器）。

## Architecture

```
encode_video() / convert_danmaku()
│
├── 正常处理
│   ├── 成功 → 清零失败计数
│   └── 失败 → 失败计数 +1
│       ├── < MAX_RETRY → 下轮重试
│       └── >= MAX_RETRY → move 到 data/failed/ + 日志告警
│
└── 无弹幕 FLV 检测（仅 encode_video）
    ├── 第 1 次见 → 记入 orphan set，跳过
    └── 第 2 次见 → 走无弹幕编码路径
```

## File Changes

| File | Change |
|------|--------|
| `config.py` | 新增 `FAILED_FOLDER`、`MAX_RETRY_COUNT` 常量 |
| `encoder.py` | 失败计数 + 隔离逻辑；孤立 FLV 检测 + 无弹幕编码路径；`-global_quality` 32→35 |
| `danmaku.py` | `convert_danmaku()` 加失败计数 + 隔离逻辑 |
