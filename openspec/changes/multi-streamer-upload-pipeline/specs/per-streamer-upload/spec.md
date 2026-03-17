## ADDED Requirements

### Requirement: 按主播分组上传
`upload_to_bilibili()` 须遍历所有已配置主播，对每个主播独立执行文件匹配、场次匹配和上传流程。

#### Scenario: 两个主播各有待上传文件
- **WHEN** 上传文件夹中有主播 A 和主播 B 的视频文件
- **THEN** 须分别为主播 A 和主播 B 执行独立的场次匹配和上传，使用各自在 `config.yaml` 中的上传元数据（标题、标签、分区等）

#### Scenario: 仅一个主播有待上传文件
- **WHEN** 上传文件夹中仅有主播 A 的视频文件
- **THEN** 仅处理主播 A 的上传，不报错

#### Scenario: 无待上传文件
- **WHEN** 上传文件夹为空
- **THEN** 正常结束，不报错

### Requirement: 文件按主播名匹配
待上传文件须通过文件名前缀（`{主播名}录播`）匹配到对应主播。

#### Scenario: 文件名包含主播名前缀
- **WHEN** 文件名为 `洞主录播2024-01-01T12_00_00.mp4`
- **THEN** 该文件须匹配到主播 `洞主`

#### Scenario: 文件名不匹配任何主播
- **WHEN** 文件名前缀不包含任何已配置主播的名称
- **THEN** 该文件须被跳过，记录 warning 日志

### Requirement: 每主播独立场次匹配
每个主播的视频须与该主播自己的 `StreamSession` 记录进行场次匹配，不得跨主播匹配。

#### Scenario: 两个主播同时段直播
- **WHEN** 主播 A 和主播 B 在同一时间段都有直播场次
- **THEN** 主播 A 的视频须匹配主播 A 的场次，主播 B 的视频须匹配主播 B 的场次，不得交叉

### Requirement: 每主播独立投稿
每个主播的视频须创建独立的 B 站投稿（独立 BVID），同一主播同一场次的多个视频追加为分 P。

#### Scenario: 主播 A 首次上传
- **WHEN** 主播 A 的某场次尚无上传记录
- **THEN** 须为主播 A 创建新投稿，使用主播 A 的标题模板、标签、分区等元数据

#### Scenario: 主播 A 追加分 P
- **WHEN** 主播 A 的某场次已有上传记录（已有 BVID）
- **THEN** 须将新视频以分 P 形式追加到该 BVID

### Requirement: UploadedVideo 记录主播归属
`UploadedVideo` 模型须新增 `streamer_name` 字段，上传记录创建时须填入对应主播名称。

#### Scenario: 新上传记录
- **WHEN** 上传一个视频并创建 `UploadedVideo` 记录
- **THEN** 记录的 `streamer_name` 字段须填入该视频所属主播的名称

#### Scenario: 旧记录兼容
- **WHEN** 查询 `UploadedVideo` 且记录的 `streamer_name` 为 NULL
- **THEN** 不得报错，该记录视为历史数据正常处理

### Requirement: 调度器多主播适配
`scheduler.py` 的 `PROCESS_AFTER_STREAM_END` 逻辑须检查所有主播的直播状态，而非仅首位主播。手动触发端点（`/run_processing_tasks`、`/run_upload_tasks`）须处理所有主播。

#### Scenario: PROCESS_AFTER_STREAM_END 多主播
- **WHEN** `PROCESS_AFTER_STREAM_END` 启用且主播 A 下播但主播 B 仍在直播
- **THEN** 须触发主播 A 的视频处理流水线，不等待主播 B 下播

#### Scenario: 手动触发上传处理所有主播
- **WHEN** 调用 `/run_upload_tasks` 端点
- **THEN** 须处理所有主播的待上传视频，不仅限于首位主播
