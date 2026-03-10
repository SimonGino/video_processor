## Context

当前弹幕处理流程：`danmaku.py` 的 `convert_danmaku()` 遍历 processing 目录中的 XML 文件，调用 `dmconvert.convert_xml_to_ass()` 生成 ASS 字幕文件，然后在 `encoder.py` 中通过 FFmpeg 将 ASS 烧录到视频中。

`dmconvert` v0.0.4 的 Python API 仅接受 6 个参数（font_size, sc_font_size, resolution_x, resolution_y, xml_file, ass_file）。库内部硬编码了：
- 显示区域：滚动弹幕行数 = `resolution_y / font_size`，占满全屏
- 透明度：ASS header 中 PrimaryColour 的 alpha 通道固定为 `4B`（约 70% 不透明度）
- 字体：固定 `Microsoft YaHei`
- 滚动时间：固定 12 秒

彩色弹幕方面，`normal_handler.py` 已正确从 XML `p` 属性的第 4 个字段解析颜色并写入 `\c&H{color_hex}`，端到端已可用。但斗鱼平台绝大多数弹幕颜色为白色（`16777215`）。

## Goals / Non-Goals

**Goals:**
- 支持通过 `DANMAKU_DISPLAY_AREA` 配置限制滚动弹幕显示区域（0.0-1.0，默认 1.0 全屏）
- 支持通过 `DANMAKU_OPACITY` 配置弹幕透明度（0.0-1.0，默认 0.8）
- 验证并确保彩色弹幕在整个流程中端到端正常工作
- 修改方案对 dmconvert 库侵入最小，易于维护

**Non-Goals:**
- 不实现弹幕密度控制（按时间窗口过滤过多弹幕）
- 不实现弹幕关键词过滤/屏蔽
- 不改变 SuperChat、礼物、舰长的显示逻辑（仅影响普通滚动弹幕）
- 不做实时弹幕预览 UI

## Decisions

### 决策 1：ASS 后处理 vs Fork dmconvert vs Monkey-patch

**选择：ASS 后处理**

| 方案 | 优点 | 缺点 |
|------|------|------|
| ASS 后处理 | 零侵入、不依赖上游版本、实现简单 | 需要解析 ASS 文本，先生成全屏再裁剪有少量浪费 |
| Fork dmconvert | 完整控制、性能最优 | 维护成本高、需同步上游更新 |
| Monkey-patch | 不需 fork | 脆弱、依赖内部实现细节 |

ASS 后处理方案的具体做法：
1. `dmconvert` 正常生成完整 ASS 文件
2. 新模块 `danmaku_postprocess.py` 读取 ASS，对 `[Events]` 部分逐行处理：
   - **显示区域**：解析 `\move(x1,y,x2,y)` 中的 Y 坐标，丢弃 Y > `resolution_y * display_area` 的滚动弹幕行
   - **透明度**：修改 `[V4+ Styles]` 中 R2L/L2R/TOP/BTM 样式的 PrimaryColour alpha 通道
3. 将处理后的内容写回 ASS 文件

### 决策 2：配置位置

**选择：config.py 中新增常量**

与项目现有配置模式保持一致（所有配置常量集中在 `config.py`），放在 `--- 弹幕转换配置 ---` 区块下方。

### 决策 3：彩色弹幕处理策略

**选择：确认现有流程可用 + 配置开关**

经代码审查，彩色弹幕已在 `dmconvert` 中端到端工作。新增 `DANMAKU_COLOR_ENABLED` 配置项（默认 `True`），当设为 `False` 时在后处理阶段移除所有 `\c` 标签，强制所有弹幕为白色。这为用户提供了选择权。

## Risks / Trade-offs

- **[性能]** ASS 后处理需要读写完整文件 → 弹幕文件通常很小（几百 KB），性能影响可忽略
- **[ASS 格式解析脆弱性]** 依赖正则匹配 ASS 事件行格式 → dmconvert 输出格式固定且简单，风险低；添加单元测试覆盖
- **[上游更新]** dmconvert 未来版本可能原生支持这些参数 → 后处理方案与上游解耦，不冲突；若上游支持可直接切换
- **[显示区域裁剪]** 丢弃超出区域的弹幕可能导致信息丢失 → 这是预期行为，用户通过配置主动选择
