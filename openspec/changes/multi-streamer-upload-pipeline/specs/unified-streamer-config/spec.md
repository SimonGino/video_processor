## ADDED Requirements

### Requirement: config.yaml 按主播分组配置
`config.yaml` 须使用 `streamers` 顶层 key，每个子 key 为主播名称，包含 `room_id` 和 `upload` 子对象。`upload` 子对象须包含 `title`、`tid`、`tag`、`desc`、`source`、`cover`、`dynamic` 字段。

#### Scenario: 多主播配置加载
- **WHEN** `config.yaml` 包含多个主播配置
- **THEN** `load_yaml_config()` 须解析所有主播条目，每个主播的上传元数据可通过主播名索引访问

#### Scenario: 单主播配置向后兼容
- **WHEN** `config.yaml` 仅包含一个主播配置
- **THEN** 系统行为与改动前完全一致

### Requirement: STREAMERS 列表从 YAML 派生
`config.py` 中的 `STREAMERS` 列表须从 `config.yaml` 的 `streamers` 部分自动生成，格式为 `[{"name": "<主播名>", "room_id": "<房间号>"}, ...]`，下游录制和监控代码无需改动。

#### Scenario: 启动时自动生成 STREAMERS
- **WHEN** 应用启动并调用 `load_yaml_config()`
- **THEN** `STREAMERS` 列表须包含 `config.yaml` 中所有主播的 `name` 和 `room_id`

#### Scenario: 录制服务使用派生的 STREAMERS
- **WHEN** 录制服务读取 `config.STREAMERS`
- **THEN** 获取到的列表与 `config.yaml` 中的主播配置一致，录制行为不变

### Requirement: config.yaml 校验
`load_yaml_config()` 须校验 `config.yaml` 结构的完整性，缺少必要字段时须记录明确错误信息并返回失败。

#### Scenario: 缺少主播上传必要字段
- **WHEN** 某主播配置中缺少 `title` 或 `tid` 或 `tag` 字段
- **THEN** 须记录包含主播名和缺失字段名的错误日志，`load_yaml_config()` 返回 `False`

#### Scenario: 缺少 room_id
- **WHEN** 某主播配置中缺少 `room_id`
- **THEN** 须记录错误日志，`load_yaml_config()` 返回 `False`

### Requirement: 全局上传配置
`config.yaml` 须支持 `upload` 顶层 key 存放全局上传配置（如 `max_concurrent`、`rate_limit_cooldown`），这些配置不属于特定主播。

#### Scenario: 读取全局上传配置
- **WHEN** `config.yaml` 包含 `upload.max_concurrent: 2`
- **THEN** 上传并发数须使用该值，而非默认值
