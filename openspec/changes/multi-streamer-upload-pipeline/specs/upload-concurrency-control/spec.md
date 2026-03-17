## ADDED Requirements

### Requirement: 上传并发数限制
系统须通过 `asyncio.Semaphore` 限制同时执行的上传任务数量，默认最大并发数为 1。

#### Scenario: 默认并发数
- **WHEN** 未在 `config.yaml` 中配置 `upload.max_concurrent`
- **THEN** 同一时刻最多只有 1 个上传任务在执行

#### Scenario: 自定义并发数
- **WHEN** `config.yaml` 中配置 `upload.max_concurrent: 2`
- **THEN** 同一时刻最多有 2 个上传任务在执行

#### Scenario: 并发上传排队
- **WHEN** 并发上传数已达上限，有新的上传任务需要执行
- **THEN** 新任务须等待已有任务完成后再执行，不得丢弃

### Requirement: 信号量覆盖所有上传调用
所有调用 biliup CLI 或 bilitool 的上传操作（创建新稿件和追加分 P）须在信号量保护下执行。

#### Scenario: 创建新稿件受信号量控制
- **WHEN** 调用 `_biliup_upload_video_entry` 或 bilitool 上传
- **THEN** 须先 acquire 信号量，上传完成后 release

#### Scenario: 追加分 P 受信号量控制
- **WHEN** 调用 `_biliup_append_video_entry` 或 bilitool 追加
- **THEN** 须先 acquire 信号量，操作完成后 release
