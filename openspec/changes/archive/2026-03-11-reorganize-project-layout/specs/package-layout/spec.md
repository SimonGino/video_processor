## ADDED Requirements

### Requirement: 标准 Python 包目录结构

项目必须（SHALL）将所有业务 Python 模块组织在 `src/douyu2bilibili/` 包内，根目录不得存在业务逻辑模块文件。

#### Scenario: 根目录仅保留入口和配置

- **WHEN** 检查项目根目录的 `.py` 文件
- **THEN** 根目录仅存在薄入口脚本（如 `app.py`），不存在 `config.py`、`models.py`、`scheduler.py`、`danmaku.py`、`encoder.py`、`uploader.py`、`stream_monitor.py`、`danmaku_postprocess.py` 等业务模块

#### Scenario: 包目录包含所有业务模块

- **WHEN** 检查 `src/douyu2bilibili/` 目录
- **THEN** 该目录包含 `__init__.py`、`app.py`、`config.py`、`models.py`、`scheduler.py`、`danmaku.py`、`danmaku_postprocess.py`、`encoder.py`、`uploader.py`、`stream_monitor.py`、`recording_service.py` 及 `recording/` 子包

### Requirement: 包内模块可正确互相导入

包内模块必须（SHALL）使用相对导入引用同包模块，且不存在导入错误。

#### Scenario: 相对导入正常工作

- **WHEN** 启动应用（`python -m douyu2bilibili.app`）
- **THEN** 所有模块加载成功，无 ImportError 或 ModuleNotFoundError

#### Scenario: 循环依赖仍通过 late import 解决

- **WHEN** scheduler 模块需要访问 app 模块的依赖
- **THEN** 通过函数内 late import（`from douyu2bilibili.app import ...` 或相对导入）正常获取，无循环导入错误

### Requirement: pyproject.toml 正确配置 src 布局

pyproject.toml 必须（SHALL）正确声明包源目录为 `src/`，使 `uv sync` 安装后包可正常导入。

#### Scenario: uv sync 后包可导入

- **WHEN** 执行 `uv sync` 后在项目环境中运行 `python -c "import douyu2bilibili"`
- **THEN** 导入成功，无报错

#### Scenario: pytest 可发现并运行所有测试

- **WHEN** 执行 `uv run pytest`
- **THEN** 所有现有测试被发现并通过（不计入因外部依赖缺失而 skip 的测试）

### Requirement: 运行时文件路径正确解析

config.py 中的路径常量必须（SHALL）正确解析到项目根目录下的 `data/`、`logs/` 等目录，而非 `src/douyu2bilibili/` 内部。

#### Scenario: 数据目录路径指向项目根目录

- **WHEN** 应用读取 `PROCESSING_FOLDER`、`UPLOAD_FOLDER` 等路径常量
- **THEN** 路径指向项目根目录下的 `data/processing/`、`data/upload/` 等位置

#### Scenario: config.yaml 可正常加载

- **WHEN** 应用调用 `load_yaml_config()`
- **THEN** 正确读取项目根目录下的 `config.yaml` 文件

### Requirement: 服务启动脚本适配

service.sh 必须（SHALL）使用适配后的启动命令，能正确启动主服务和录制服务。

#### Scenario: service.sh 正常启动服务

- **WHEN** 执行 `./service.sh start`
- **THEN** 主服务和录制服务正常启动，进程存活
