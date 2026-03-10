## Why

当前弹幕转换流程直接调用 `dmconvert` 库的 `convert_xml_to_ass()`，仅传入字体大小和分辨率 6 个参数。库本身（v0.0.4）虽在 README 中列出了 `displayarea`、`alpha` 等高级参数，但 Python API 层面并未暴露。用户需要两项增强：

1. **显示区域控制**：弹幕默认铺满全屏，遮挡画面关键内容。需要支持将滚动弹幕限制在上半屏（0.5）或上 1/4 屏（0.25）等区域。
2. **彩色弹幕保留**：虽然斗鱼平台大部分弹幕为白色（`16777215`），但少数贵族/特权用户发送的彩色弹幕已在 XML 中携带颜色值，`dmconvert` 也已正确解析并写入 ASS `\c` 标签。需确保整个流程不会丢失颜色信息，并为未来颜色增强（如自定义默认颜色、透明度）预留配置入口。

## What Changes

- 对 `dmconvert` 库进行 fork 或补丁，为 `convert_xml_to_ass()` 新增 `display_area`、`opacity`、`bold`、`font_name` 等可选参数
- `config.py` 新增弹幕显示相关配置项：`DANMAKU_DISPLAY_AREA`（显示区域比例）、`DANMAKU_OPACITY`（透明度）
- `danmaku.py` 的 `convert_danmaku()` 将新配置传入转换函数
- 确认并验证彩色弹幕在现有流程中端到端正常工作（XML 采集 → 转换 → ASS 渲染）

## Capabilities

### New Capabilities
- `danmaku-display-area`: 弹幕显示区域控制——通过 `display_area` 参数（0.0-1.0）限制滚动弹幕只出现在屏幕上方指定比例的区域内
- `danmaku-style-config`: 弹幕样式配置——支持透明度、字体等样式参数的可配置化，包括确保彩色弹幕颜色信息端到端保留

### Modified Capabilities

（无需修改已有 spec）

## Impact

- **dmconvert 库**：需要 fork 或以 monkey-patch 方式扩展 `convert_xml_to_ass()` 函数签名及内部处理逻辑（`header.py`、`normal_handler.py`、`danmaku_array.py`）
- **config.py**：新增 2-3 个配置常量
- **danmaku.py**：`convert_danmaku()` 函数调用处需传入新参数
- **pyproject.toml**：如果 fork dmconvert，需更新依赖源
- **测试**：`tests/unit/test_dmconvert_contract.py` 需扩展覆盖新参数
