# BiliBili 全自动录播上传套件

全自动处理直播录播文件并上传到哔哩哔哩的工具套件。

- **直播状态监控** — 定时检测斗鱼主播上下线，记录直播场次
- **内建录制（可选）** — FFmpeg 录制直播流为 FLV，同时采集弹幕 XML
- **视频处理** — XML 弹幕转 ASS 字幕，FFmpeg QSV 硬件加速压制 MP4
- **B站自动上传** — 按直播场次分组，智能创建稿件/追加分P，自动获取 BVID

## 架构

```
录制（二选一）
- 内建录制服务 recording_service.py (FLV + XML)
- 外部录制软件 (FLV + XML)
        ↓
┌─ PROCESSING_FOLDER ─────────────────────────┐
│  cleanup_small_files()  删除 <10MB 文件      │  danmaku.py
│  convert_danmaku()      XML → ASS 字幕       │
│  encode_video()         FLV+ASS → MP4 (QSV)  │  encoder.py
└──────────────────────────────────────────────┘
        ↓
┌─ UPLOAD_FOLDER ─────────────────────────────┐
│  upload_to_bilibili()   按场次上传到B站       │  uploader.py
│  update_video_bvids()   补全 BVID 信息        │
└──────────────────────────────────────────────┘
        ↓
    SQLite (app_data.db)  记录场次和上传信息
```

定时任务由 `scheduler.py` 管理，`app.py` 提供 FastAPI 接口和启动逻辑。

## 前置依赖

| 依赖 | 说明 | 安装方式 |
|------|------|----------|
| Python 3.13+ | 运行环境 | [python.org](https://www.python.org/) |
| uv | Python 包管理工具 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| FFmpeg (含 QSV) | 视频压制 | `apt install ffmpeg` 或从源码编译启用 QSV |
| FFprobe | 获取视频分辨率 | 随 FFmpeg 一起安装 |
| 录制工具 | 录制直播流为 FLV + XML | 内建 `recording_service.py` 或外部工具（如 StreamRecorder） |

Python 依赖（通过 `uv sync` 自动安装）：

| 包 | 用途 |
|----|------|
| bilitool | B站 API（登录、上传、获取视频信息） |
| dmconvert | 弹幕转换（XML → ASS 字幕） |
| fastapi + uvicorn | Web API 框架和 ASGI 服务器 |
| sqlalchemy + aiosqlite | 异步 ORM + SQLite 驱动 |
| apscheduler | 异步定时任务调度 |
| aiohttp | 斗鱼 API HTTP 客户端 |

## 安装

```bash
# 1. 克隆仓库
git clone https://github.com/SimonGino/video_processor.git
cd video_processor

# 2. 安装 Python 依赖
uv sync

# 3. 配置项目（见下方配置说明）
# 编辑 config.py 设置路径、主播信息、功能开关
# 编辑 config.yaml 设置B站投稿参数

# 4. 登录B站（二选一）
bilitool login                    # 交互式登录
# 或手动放置 cookies.json 到项目根目录

# 5. 启动服务
python app.py                     # 前台运行（开发）
./service.sh start                # 后台运行（生产）
```

## 内建录制服务（可选）

前台运行：

```bash
uv run python recording_service.py
```

后台运行：

```bash
./service.sh start-recording
./service.sh logs-recording 200
```

## 配置说明

### config.py — 运行参数

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `PROCESSING_FOLDER` | 录制文件处理目录 | `./data/processing` |
| `UPLOAD_FOLDER` | 处理后视频存放目录 | `./data/upload` |
| `MIN_FILE_SIZE_MB` | 最小有效文件大小 (MB) | `10` |
| `FONT_SIZE` | ASS 弹幕字体大小 | `40` |
| `SC_FONT_SIZE` | ASS SC弹幕字体大小 | `38` |
| `FFPROBE_PATH` | FFprobe 路径 | `ffprobe` |
| `FFMPEG_PATH` | FFmpeg 路径 | `ffmpeg` |
| `SKIP_VIDEO_ENCODING` | 跳过压制直接上传 FLV | `False` |
| `NO_DANMAKU_TITLE_SUFFIX` | 无弹幕版标题后缀 | `【无弹幕版】` |
| `DANMAKU_TITLE_SUFFIX` | 弹幕版标题后缀 | `【弹幕版】` |
| `SCHEDULE_INTERVAL_MINUTES` | 视频处理定时间隔（分钟） | `60` |
| `STREAM_STATUS_CHECK_INTERVAL` | 直播状态检测间隔（分钟） | `10` |
| `STREAM_START_TIME_ADJUSTMENT` | 开播时间向前调整（分钟） | `10` |
| `DELETE_UPLOADED_FILES` | 上传后删除本地文件 | `True` |
| `PROCESS_AFTER_STREAM_END` | 仅下播后处理 | `False` |
| `API_BASE_URL` | API 服务器地址 | `http://localhost:50009` |
| `API_ENABLED` | 启用 API 功能 | `True` |
| `STREAMERS` | 主播列表 `[{"name": "...", "room_id": "..."}]` | — |

### config.yaml — B站投稿参数

```yaml
title: "主播名直播录像{time}弹幕版"   # {time} 会替换为 YYYY年MM月DD日
tid: 171                            # B站分区 ID（171=单机游戏）
tag: "主播名,直播录像,游戏实况"        # 视频标签
source: "https://www.douyu.com/房间号" # 转载来源
desc: |                              # 视频简介
  主播的精彩直播录像！
  直播间：https://www.douyu.com/房间号
cover: ''                            # 封面图片路径（留空用B站默认）
dynamic: ''                          # 动态信息
```

## 使用方式

### 服务管理

```bash
./service.sh start        # 启动后台服务
./service.sh stop         # 停止服务
./service.sh restart      # 重启服务
./service.sh status       # 查看运行状态
./service.sh logs 100     # 查看最近 100 行日志
```

### API 端点

服务运行在端口 50009（可通过 `API_BASE_URL` 配置）。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/run_processing_tasks` | 手动触发视频处理（清理、转换、压制） |
| POST | `/run_upload_tasks` | 手动触发B站上传 |
| GET | `/stream_sessions/{streamer_name}` | 查询主播直播场次记录 |
| POST | `/log_stream_end` | 手动记录下播 |
| POST | `/log_stream_start` | 手动记录上播 |
| GET | `/videos_without_bvid` | 查询缺失 BVID 的视频 |
| PUT | `/update_video_bvid/{video_id}` | 更新视频 BVID |
| GET | `/check_uploaded/{filename}` | 检查文件是否已上传 |
| GET | `/latest_bvid/{streamer_name}` | 获取最新 BVID |

## 项目结构

```
video_processor/
├── app.py              — FastAPI 入口、路由、数据库初始化
├── scheduler.py        — APScheduler 定时任务函数
├── danmaku.py          — 弹幕清理和 XML→ASS 转换
├── encoder.py          — FFmpeg QSV 视频编码
├── uploader.py         — B站上传和 BVID 管理
├── stream_monitor.py   — 斗鱼直播状态监控
├── models.py           — SQLAlchemy 数据模型
├── config.py           — Python 配置常量
├── config.yaml         — B站投稿参数
├── service.sh          — Bash 服务管理脚本
├── pyproject.toml      — uv 项目配置
└── README.md           — 本文件
```

## 许可证

MIT
