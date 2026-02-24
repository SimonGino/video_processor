# Douyu Recording E2E Checklist (Manual)

> 说明：此目录为手工验收清单，不进入 CI。

## 1. 配置

编辑 `config.py`：

- `PROCESSING_FOLDER`：确保存在且可写
- `STREAMERS`：填入主播 `name` 与 `room_id`
- （可选）将 `RECORDING_SEGMENT_MINUTES = 1`，方便快速验证

## 2. 运行录制服务

方式一（前台）：

```bash
uv run python recording_service.py
```

方式二（后台脚本）：

```bash
./service.sh start-recording
./service.sh logs-recording 200
```

## 3. 观察输出

在 `PROCESSING_FOLDER` 里应出现：

- `xxx录播YYYY-MM-DDTHH_mm_ss.flv.part`（录制中）
- `xxx录播YYYY-MM-DDTHH_mm_ss.xml.part`（弹幕采集中）

单段结束后应自动 `rename` 为：

- `xxx录播YYYY-MM-DDTHH_mm_ss.flv`
- `xxx录播YYYY-MM-DDTHH_mm_ss.xml`

## 4. 下游链路验证（可选）

启动 API 服务并触发处理：

```bash
./service.sh start
curl -X POST http://localhost:50009/run_processing_tasks
```

确认：

- `danmaku.py` 能把 XML 转成 ASS
- `encoder.py` 能压制/或跳过压制移动文件
- `uploader.py` 能按场次上传（需 B 站登录）

