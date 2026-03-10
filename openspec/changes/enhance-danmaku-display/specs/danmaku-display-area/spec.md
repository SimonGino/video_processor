## ADDED Requirements

### Requirement: 滚动弹幕显示区域可配置
系统 SHALL 支持通过 `DANMAKU_DISPLAY_AREA` 配置项控制滚动弹幕的垂直显示范围。该值为 0.0 到 1.0 之间的浮点数，表示从屏幕顶部算起的可用区域比例。默认值为 `1.0`（全屏）。

#### Scenario: 默认全屏显示
- **WHEN** `DANMAKU_DISPLAY_AREA` 为 `1.0`（默认值）
- **THEN** 所有滚动弹幕保留，不进行区域裁剪，ASS 文件内容与 dmconvert 原始输出一致

#### Scenario: 上半屏显示
- **WHEN** `DANMAKU_DISPLAY_AREA` 设为 `0.5`，视频分辨率为 1920x1080
- **THEN** ASS 文件中 Y 坐标大于 540（1080 × 0.5）的滚动弹幕事件行 SHALL 被移除

#### Scenario: 上 1/4 屏显示
- **WHEN** `DANMAKU_DISPLAY_AREA` 设为 `0.25`，视频分辨率为 1920x1080
- **THEN** ASS 文件中 Y 坐标大于 270（1080 × 0.25）的滚动弹幕事件行 SHALL 被移除

### Requirement: 仅影响滚动弹幕
显示区域限制 SHALL 仅应用于 R2L（从右到左滚动）样式的弹幕。底部固定弹幕（BTM）、SuperChat（SP/message_box）、礼物和舰长弹幕 SHALL NOT 受影响。

#### Scenario: 底部固定弹幕不受影响
- **WHEN** `DANMAKU_DISPLAY_AREA` 设为 `0.5`
- **THEN** BTM 样式的弹幕事件行全部保留，无论其 Y 坐标如何

#### Scenario: SuperChat 不受影响
- **WHEN** `DANMAKU_DISPLAY_AREA` 设为 `0.25`
- **THEN** SP、message_box、price 样式的弹幕事件行全部保留

### Requirement: 配置项位于 config.py
`DANMAKU_DISPLAY_AREA` SHALL 定义在 `config.py` 的 `--- 弹幕转换配置 ---` 区块中，类型为 float，取值范围 0.0-1.0。

#### Scenario: 配置项存在且有默认值
- **WHEN** 用户未修改 `config.py` 中的 `DANMAKU_DISPLAY_AREA`
- **THEN** 其值为 `1.0`，弹幕显示行为与当前版本完全一致（向后兼容）

### Requirement: 后处理需要视频分辨率
后处理函数 SHALL 接收视频分辨率参数以计算实际像素阈值。分辨率 SHALL 复用 `danmaku.py` 中已有的 `get_video_resolution()` 获取结果。

#### Scenario: 按实际分辨率计算阈值
- **WHEN** 视频分辨率为 1280x720 且 `DANMAKU_DISPLAY_AREA` 为 `0.5`
- **THEN** Y 坐标阈值为 360（720 × 0.5），超出此值的滚动弹幕被移除
