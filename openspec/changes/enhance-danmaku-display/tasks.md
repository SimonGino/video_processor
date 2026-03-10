## 1. 配置项

- [x] 1.1 在 `config.py` 的 `--- 弹幕转换配置 ---` 区块新增 `DANMAKU_DISPLAY_AREA = 1.0`（float，0.0-1.0，弹幕显示区域比例）
- [x] 1.2 在 `config.py` 新增 `DANMAKU_OPACITY = 0.8`（float，0.0-1.0，弹幕透明度）
- [x] 1.3 在 `config.py` 新增 `DANMAKU_COLOR_ENABLED = True`（bool，是否保留彩色弹幕）

## 2. 后处理模块

- [x] 2.1 创建 `danmaku_postprocess.py` 模块，定义 `postprocess_ass(ass_file, resolution_y, display_area, opacity, color_enabled)` 函数
- [x] 2.2 实现显示区域裁剪：解析 ASS Events 中 R2L 样式行的 `\move(x1,y,x2,y)` Y 坐标，移除 Y > `resolution_y * display_area` 的行
- [x] 2.3 实现透明度修改：修改 `[V4+ Styles]` 中 R2L/L2R/TOP/BTM 样式的 PrimaryColour alpha 通道为 `hex(int((1 - opacity) * 255))`
- [x] 2.4 实现彩色弹幕开关：当 `color_enabled=False` 时，移除 Events 行中所有 `{\c&H......}` 标签

## 3. 集成到弹幕转换流程

- [x] 3.1 修改 `danmaku.py` 的 `convert_danmaku()` 函数：在 `convert_xml_to_ass()` 调用后，调用 `postprocess_ass()` 进行后处理
- [x] 3.2 将 `config.DANMAKU_DISPLAY_AREA`、`config.DANMAKU_OPACITY`、`config.DANMAKU_COLOR_ENABLED` 及 `resolution_y` 传入后处理函数

## 4. 测试

- [x] 4.1 编写 `tests/unit/test_danmaku_postprocess.py`，覆盖显示区域裁剪的三种场景（全屏 1.0、半屏 0.5、1/4 屏 0.25）
- [x] 4.2 编写透明度修改的测试用例（0.8 默认、1.0 不透明、0.5 半透明）
- [x] 4.3 编写彩色弹幕开关的测试用例（启用保留 `\c` 标签、禁用移除 `\c` 标签）
- [x] 4.4 编写边界测试：BTM/SP/message_box 样式的行不受显示区域和透明度影响
- [x] 4.5 运行完整测试套件确认无回归
