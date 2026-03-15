## Tasks

- [x] 1. `config.py` — 新增配置常量
  - `FAILED_FOLDER = os.path.join(PROJECT_ROOT, "data", "failed")`
  - `MAX_RETRY_COUNT = 3`（文件连续失败阈值）
  - `os.makedirs(FAILED_FOLDER, exist_ok=True)`

- [x] 2. `encoder.py` — global_quality 32 → 35
  - 修改 QSV 命令中的 `-global_quality 32` 为 `-global_quality 35`

- [x] 3. `encoder.py` — 失败文件退避与隔离
  - 新增模块级 `_failure_counts: dict[str, int]`
  - 编码失败时递增计数，成功时清零
  - 达到 `MAX_RETRY_COUNT` 时将 FLV + ASS 移入 `data/failed/` 并记录告警日志
  - 跳过已在 `_failure_counts` 中达到阈值的文件（防止移动失败后仍在 processing 目录的情况）

- [x] 4. `danmaku.py` — 失败文件退避与隔离
  - 同 task 3 的逻辑应用到 `convert_danmaku()`
  - XML 转换失败时递增计数，成功时清零
  - 达到阈值时将 XML + 对应 FLV 移入 `data/failed/`

- [x] 5. `encoder.py` — 无弹幕 FLV 直通处理
  - 新增模块级 `_orphan_seen: set[str]` 追踪已见过一轮的孤立 FLV
  - 在 `encode_video()` 的非 SKIP_ENCODING 路径中，遍历完 ASS 文件后，额外扫描 `*.flv`
  - 筛选条件：无 `.flv.part`、无同名 `.xml`、无同名 `.ass`、不在 upload 目录中已存在
  - 第一次见到 → 加入 `_orphan_seen`，跳过
  - 第二次见到 → 走无弹幕编码（FFmpeg 不加 subtitles filter）或直接 move（取决于 SKIP_ENCODING）
  - 成功处理后从 `_orphan_seen` 中移除

- [x] 6. 测试
  - 失败计数器递增、清零、阈值触发隔离
  - 孤立 FLV 的两轮检测逻辑
  - `data/failed/` 目录的文件移动
