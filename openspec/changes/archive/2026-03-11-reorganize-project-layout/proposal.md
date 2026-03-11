## Why

根目录堆了 11 个 Python 模块文件，项目结构扁平且混乱：配置、模型、业务逻辑、入口脚本全部平铺在根目录，没有清晰的包边界。随着功能增长（recording 子包已独立），主应用代码也应收进一个正式的 Python 包中，让项目结构一目了然、import 路径规范。

## What Changes

- 新建 `src/douyu2bilibili/` 包，将根目录所有业务模块移入：
  - `app.py` → `src/douyu2bilibili/app.py`（FastAPI 入口）
  - `config.py` → `src/douyu2bilibili/config.py`
  - `models.py` → `src/douyu2bilibili/models.py`
  - `scheduler.py` → `src/douyu2bilibili/scheduler.py`
  - `danmaku.py` → `src/douyu2bilibili/danmaku.py`
  - `danmaku_postprocess.py` → `src/douyu2bilibili/danmaku_postprocess.py`
  - `encoder.py` → `src/douyu2bilibili/encoder.py`
  - `uploader.py` → `src/douyu2bilibili/uploader.py`
  - `stream_monitor.py` → `src/douyu2bilibili/stream_monitor.py`
  - `recording_service.py` → `src/douyu2bilibili/recording_service.py`（录制入口）
  - `recording/` → `src/douyu2bilibili/recording/`（录制子包）
- 更新 `pyproject.toml` 的 `[project.scripts]` 或 `[tool.uv]` 配置，使 `python -m douyu2bilibili` 可运行
- 更新所有 import 路径（模块间互引、测试 import）
- 更新 `service.sh` 中的启动命令
- **BREAKING**: 所有 `import config` / `import scheduler` 等顶层 import 路径变为 `from douyu2bilibili import ...`

## Capabilities

### New Capabilities
- `package-layout`: 将扁平根目录模块重组为 `src/douyu2bilibili/` 标准 Python 包布局

### Modified Capabilities

（无已有 spec 的需求变更）

## Impact

- **代码**：所有 `.py` 文件的 import 语句需要更新；`scheduler.py` 中的循环引用 late import 路径也需同步调整
- **测试**：`tests/` 下所有 `import config`、`import scheduler` 等需改为包内路径
- **部署**：`service.sh` 启动命令需改为 `python -m douyu2bilibili.app` 或等效方式
- **配置文件**：`config.yaml`、`cookies.json` 等数据文件保持在项目根目录，不移入包内
- **pyproject.toml**：需配置 `packages` 或 `package-dir` 指向 `src/`
