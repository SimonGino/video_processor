## 1. 创建包目录结构

- [x] 1.1 创建 `src/douyu2bilibili/` 目录及 `__init__.py`
- [x] 1.2 将根目录业务模块移入包内（config.py、models.py、danmaku.py、danmaku_postprocess.py、encoder.py、uploader.py、stream_monitor.py、scheduler.py、app.py、recording_service.py）
- [x] 1.3 将 `recording/` 子包移入 `src/douyu2bilibili/recording/`

## 2. 更新包内 import

- [x] 2.1 将 config.py 中的路径解析适配新目录层级（`__file__` 基准从根目录变为 `src/douyu2bilibili/`）
- [x] 2.2 将 danmaku.py 的 import 改为相对导入（`from . import config`、`from .danmaku_postprocess import ...`）
- [x] 2.3 将 encoder.py 的 import 改为相对导入
- [x] 2.4 将 uploader.py 的 import 改为相对导入
- [x] 2.5 将 stream_monitor.py 的 import 检查（该模块无项目内 import，确认无需修改）
- [x] 2.6 将 scheduler.py 的 import 改为相对导入，包括 late import 中的 `from .app import ...`
- [x] 2.7 将 app.py 的 import 改为相对导入
- [x] 2.8 将 recording_service.py（根入口）的 import 改为相对导入
- [x] 2.9 将 recording/ 子包内所有模块的 import 适配新包路径（如有引用上层模块）

## 3. 更新项目配置

- [x] 3.1 更新 pyproject.toml：添加 src 布局配置、更新 pytest pythonpath
- [x] 3.2 在根目录创建薄入口脚本 `app.py`（转发到 `douyu2bilibili.app`）
- [x] 3.3 在根目录创建薄入口脚本 `recording_service.py`（转发到 `douyu2bilibili.recording_service`）
- [x] 3.4 更新 service.sh 中的启动命令

## 4. 更新测试

- [x] 4.1 更新所有测试文件的 import 路径（`import config` → `from douyu2bilibili import config` 等）
- [x] 4.2 运行 `uv sync` 确认包安装正确
- [x] 4.3 运行 `uv run pytest` 确认所有测试通过（5个预先存在的失败保持不变，无新增回归）

## 5. 收尾

- [x] 5.1 更新 CLAUDE.md 中的项目结构说明
- [x] 5.2 验证 `python -m douyu2bilibili.app` 可正常启动
