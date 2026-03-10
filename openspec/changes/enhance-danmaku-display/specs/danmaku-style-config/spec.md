## ADDED Requirements

### Requirement: 弹幕透明度可配置
系统 SHALL 支持通过 `DANMAKU_OPACITY` 配置项控制普通弹幕的透明度。该值为 0.0 到 1.0 之间的浮点数，0.0 表示完全透明，1.0 表示完全不透明。默认值为 `0.8`。

#### Scenario: 默认透明度
- **WHEN** `DANMAKU_OPACITY` 为 `0.8`（默认值）
- **THEN** ASS 样式 R2L、L2R、TOP、BTM 的 PrimaryColour alpha 通道 SHALL 被设为 `33`（十六进制，对应 20% 透明 = 80% 不透明）

#### Scenario: 完全不透明
- **WHEN** `DANMAKU_OPACITY` 设为 `1.0`
- **THEN** ASS 样式 PrimaryColour alpha 通道 SHALL 被设为 `00`（完全不透明）

#### Scenario: 半透明
- **WHEN** `DANMAKU_OPACITY` 设为 `0.5`
- **THEN** ASS 样式 PrimaryColour alpha 通道 SHALL 被设为 `80`（十六进制，对应 50% 透明）

### Requirement: 彩色弹幕开关
系统 SHALL 支持通过 `DANMAKU_COLOR_ENABLED` 配置项控制是否保留弹幕原始颜色。默认值为 `True`。

#### Scenario: 启用彩色弹幕（默认）
- **WHEN** `DANMAKU_COLOR_ENABLED` 为 `True`
- **THEN** ASS 事件行中 dmconvert 写入的 `\c&H......` 颜色标签 SHALL 原样保留

#### Scenario: 禁用彩色弹幕
- **WHEN** `DANMAKU_COLOR_ENABLED` 设为 `False`
- **THEN** ASS 事件行中所有 `\c&H......` 颜色标签 SHALL 被移除，弹幕统一使用样式定义的默认颜色（白色）

### Requirement: 样式配置项位于 config.py
`DANMAKU_OPACITY` 和 `DANMAKU_COLOR_ENABLED` SHALL 定义在 `config.py` 的 `--- 弹幕转换配置 ---` 区块中。

#### Scenario: 配置项存在且有默认值
- **WHEN** 用户未修改任何新配置项
- **THEN** `DANMAKU_OPACITY` 为 `0.8`，`DANMAKU_COLOR_ENABLED` 为 `True`，弹幕显示行为与当前版本基本一致

### Requirement: 仅影响普通弹幕样式
透明度修改 SHALL 仅应用于 R2L、L2R、TOP、BTM 四种弹幕样式。SuperChat 相关样式（SP、message_box、price） SHALL NOT 受透明度配置影响。

#### Scenario: SuperChat 透明度不变
- **WHEN** `DANMAKU_OPACITY` 设为 `0.5`
- **THEN** SP、message_box、price 样式的 PrimaryColour SHALL 保持 dmconvert 原始输出值不变
