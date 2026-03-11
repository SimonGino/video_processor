## Context

当前项目根目录包含 11 个 Python 模块文件（app.py、config.py、models.py、scheduler.py 等），与配置文件、脚本、数据目录混在一起。`recording/` 子包已经独立成包结构，但主应用模块仍然平铺在根目录。

pytest 通过 `pythonpath = ["."]` 让测试能 `import config` 等，但这种扁平结构缺乏包边界，不符合 Python 项目惯例。

主要依赖关系：
- `config.py` 和 `models.py` 是基础模块，被其他所有模块引用
- `scheduler.py` ↔ `app.py` 存在循环依赖（通过 late import 解决）
- `danmaku.py` → `danmaku_postprocess.py` 单向依赖
- `recording_service.py`（根目录）是录制服务入口，仅 import `recording.recording_service`

## Goals / Non-Goals

**Goals:**
- 将所有业务 Python 模块收进 `src/douyu2bilibili/` 包，根目录只保留入口脚本和配置文件
- 所有模块间 import 改为包内相对导入或绝对导入 `douyu2bilibili.xxx`
- 测试、service.sh、pyproject.toml 同步适配
- 重构后所有现有测试通过

**Non-Goals:**
- 不做任何功能变更或业务逻辑修改
- 不拆分或合并现有模块（保持文件名和模块职责不变）
- 不引入新的依赖
- 不改变 recording 子包内部结构（只移动其位置）

## Decisions

### 决策 1：使用 `src/` 布局

采用 `src/douyu2bilibili/` 目录结构（src layout），而非直接在根目录建包。

**理由**：src layout 是 Python 社区推荐的最佳实践，避免根目录包与安装后的包混淆，确保测试始终 import 安装后的版本。

**备选**：直接在根目录创建 `douyu2bilibili/` 包——更简单但容易导致 import 歧义。

### 决策 2：使用包内相对导入

包内模块间使用相对导入（`from . import config`、`from .danmaku import cleanup_small_files`）。

**理由**：相对导入明确表示模块属于同一个包，重命名包时无需修改内部 import。

**备选**：全部使用绝对导入 `from douyu2bilibili.xxx import yyy`——更显式但冗长，且包名变更时需要全部修改。

### 决策 3：保留根目录薄入口脚本

根目录保留一个薄 `app.py` 作为向后兼容入口（仅一行 `from douyu2bilibili.app import main; main()`），同时支持 `python -m douyu2bilibili`。

**理由**：避免对 service.sh 和现有部署方式造成过大变动，平滑过渡。

**备选**：完全删除根目录入口，只用 `python -m douyu2bilibili`——更干净但对现有部署影响大。

### 决策 4：pyproject.toml 配置 src 布局

在 pyproject.toml 中添加 `[tool.setuptools.package-dir]` 或等效配置，并更新 pytest 的 `pythonpath`。

### 决策 5：config.yaml 和数据文件保持根目录

`config.yaml`、`cookies.json`、`data/`、`logs/` 等运行时文件保持在项目根目录。`config.py` 中的路径解析使用项目根目录（`Path(__file__).resolve().parent.parent.parent`）或通过环境变量/运行时检测。

## Risks / Trade-offs

- **[路径解析变化]** → config.py 中所有基于 `__file__` 的路径解析需要适配新目录层级。逐一检查并修正所有路径常量。
- **[循环依赖]** → scheduler.py 的 late import 路径需从 `import app` 改为 `from douyu2bilibili import app`，需验证相对导入在 late import 场景下正常工作。
- **[部署中断]** → service.sh 启动命令变更可能导致已部署实例启动失败。保留根目录薄入口作为缓冲。
- **[测试 import 路径]** → 所有测试文件的 import 需要批量修改。通过 IDE 或脚本批量替换 + pytest 全量验证。
