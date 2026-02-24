# B站分P上传（按场次）幂等与稳定性设计

> 日期：2026-02-24  
> 目标：在**不改数据库结构**、尽量只改 `uploader.py` 的前提下，解决“同一场次重复创建稿件”、分P 起始 P 号不连续、以及 BVID 回填不稳定等问题。

## 1. 背景与现状

当前 `uploader.py` 的整体流程是：

1. 扫描 `UPLOAD_FOLDER` 的视频文件（MP4 或 FLV），从文件名解析录制时间戳
2. 查询近 3 天 `stream_sessions`（含正在直播中的场次），按 `start_time ~ end_time` 将文件分组到 `session_id`
3. 对每个 `session_id`：
   - 若该时间范围内数据库存在已回填的 `bvid`，则追加分P（`append_video_entry`）
   - 否则上传该场次第一个文件创建新稿件（`upload_video_entry`），并尝试从 B 站 Feed 获取 BVID，回填数据库

> 说明：此处“分P上传”指的是 **B站稿件分P**（先建稿件拿 BVID，再追加分P），不是上传协议层面的字节分片/断点续传。

## 2. 问题定义

### 2.1 关键问题：同一场次重复创建稿件

当“首段上传成功，但短时间拿不到 BVID”时：

- 会写入一条 `uploaded_videos` 记录：`bvid = NULL`
- 下次定时任务运行，如果 `update_video_bvids()` 仍未回填 BVID，`upload_to_bilibili()` 会继续把该场次视为“无 BVID”，从而**再次创建新稿件**，造成同一场次多个稿件（多个 BVID）。

用户期望策略：**一旦发现该场次存在 `bvid=NULL` 的记录，整场次暂停上传，等待后续 BVID 回填后再追加分P**。

### 2.2 其他稳定性问题

1. **分P 起始 P 号计算不可靠**：当前按 `bvid` 过滤计数，但分P记录写入 `bvid=NULL` 且 `bvid` 在表中 `unique`，导致计数几乎总为 1，分P 经常从 `P2` 开始，P 号可能重复。
2. **BVID 回填不稳定**：当前仅查询 `is_pubing` 状态，视频可能很快进入 `pubed`，导致“实际上已有 BVID 但查不到”。
3. **异步阻塞**：在 `async` 函数里使用 `time.sleep()` 会阻塞事件循环，影响定时任务与 API 响应。
4. **时间戳漂移导致误分配**：场次分配使用严格的 `start_time <= ts <= end_time`，监控时间与文件时间存在偏差时会落入“未分配列表”。
5. **分P标题未真正生效**：代码生成了 `part_title`，但没有传给 `append_video_entry(video_name=...)`，标题可能不符合预期。

## 3. 目标与非目标

### 3.1 目标（MVP）

1. 同一场次**最多创建一个稿件**（避免重复建稿件）
2. 若场次处于“已上传首段但 BVID 未回填”，该场次**暂停上传**（策略 1）
3. 追加分P时 P 号连续、不会从 `P2` 反复开始
4. BVID 回填更稳定（同时覆盖 `pubed/is_pubing`）
5. 异步流程不阻塞事件循环（移除 `time.sleep`）
6. 时间窗容错更好（减少未分配视频）

### 3.2 非目标

- 改造为多主播强隔离（本次场景为单主播）
- 变更数据库 schema（不加字段、不建新表、不做迁移）
- 改造 `bilitool` 上传协议层实现

## 4. 设计方案（推荐）：轻量“场次上传状态机”

采用“状态模式（State pattern）”的轻量实现：对每个 `StreamSession` 计算一个上传状态并采取对应动作。

### 4.1 场次归属判定（加时间窗 buffer）

对每个场次构造时间范围：

- `range_start = session.start_time - buffer`
- `range_end = (session.end_time or now) + buffer`

其中 `buffer` 先复用 `config.STREAM_START_TIME_ADJUSTMENT`（分钟）以最小化配置改动。

```python
# Use a buffer to tolerate drift between recorded filenames and session times.
buffer_minutes = config.STREAM_START_TIME_ADJUSTMENT
range_start = session.start_time - timedelta(minutes=buffer_minutes)
range_end = (session.end_time or local_now()) + timedelta(minutes=buffer_minutes)
```

### 4.2 状态定义

对每个场次（`session_id`），基于 `uploaded_videos` 在时间窗内的记录，定义三种状态：

1. `READY_APPEND`：存在 `bvid IS NOT NULL` 的记录  
   - 行为：`append_video_entry` 追加分P
2. `PENDING_BVID`：不存在 `bvid IS NOT NULL`，但存在 `bvid IS NULL` 的记录  
   - 行为：**整场次跳过**，等待 `update_video_bvids()` 回填后再追加
3. `NEW_UPLOAD`：时间窗内不存在任何上传记录  
   - 行为：上传首段创建稿件，然后尝试回填 BVID；若本轮拿不到，则保持 `bvid=NULL`，下次进入 `PENDING_BVID`

### 4.3 状态判定查询（数据库幂等锚点）

以时间窗作为“同一场次”的幂等锚点（单主播假设成立）：

- `existing_bvid_record`：`upload_time BETWEEN range_start AND range_end AND bvid IS NOT NULL`
- `pending_record`：`upload_time BETWEEN range_start AND range_end AND bvid IS NULL`

判定顺序：

1. 若 `existing_bvid_record` 存在 → `READY_APPEND`
2. 否则若 `pending_record` 存在 → `PENDING_BVID`
3. 否则 → `NEW_UPLOAD`

### 4.4 追加分P策略（修 P 号 + 确保标题生效）

1. **P 号起点**：用“该时间窗内所有 `UploadedVideo` 记录数”计算：

- `already_uploaded_count = COUNT(uploaded_videos WHERE upload_time BETWEEN range_start AND range_end)`
- `start_part_number = already_uploaded_count + 1`

2. **分P标题**：生成 `part_title` 后传给 bilitool：

- `append_video_entry(video_path, bvid, cdn=..., video_name=part_title)`

> 说明：`bilitool.UploadController.append_video_entry` 支持 `video_name` 参数；不传时默认使用文件名。

### 4.5 新建稿件策略（更稳拿 BVID + 不阻塞）

1. 首段上传成功后写入 `UploadedVideo(bvid=NULL, title=title, first_part_filename=..., upload_time=...)`
2. 回填 BVID 时，Feed 查询改为覆盖发布/审核中两类：

```python
# Include both "is_pubing" and "pubed" to reduce missing BVID after fast publish.
video_list_data = feed_controller.get_video_dict_info(size=20, status_type="pubed,is_pubing")
```

3. 所有等待改为 `await asyncio.sleep()`，避免阻塞：

```python
# Do not block the event loop inside async functions.
await asyncio.sleep(15)
```

4. 本轮拿不到 BVID：记录保持 `bvid=NULL`，下次运行会进入 `PENDING_BVID`（整场次暂停），避免重复创建稿件。

## 5. 变更范围（最小化）

### 5.1 修改文件

- `uploader.py`
  - 场次时间窗：加 buffer
  - 状态机：新增 `PENDING_BVID` 分支（发现 `bvid=NULL` 记录则跳过）
  - 追加分P：按时间窗计数计算 P 号；传 `video_name=part_title`
  - BVID 获取：查询 `pubed,is_pubing`；替换 `time.sleep` 为 `await asyncio.sleep`

### 5.2 不修改文件

- `models.py`：不改表结构
- `scheduler.py`：流程不变（先 `update_video_bvids` 再 `upload_to_bilibili`）
- `config.py`：优先复用已有 `STREAM_START_TIME_ADJUSTMENT`，不新增配置项（若后续需要更精细再加）

## 6. 验收标准（手工验证即可）

1. 场次 A 首段上传成功但本轮拿不到 BVID：数据库存在 `bvid=NULL` 记录
2. 下一轮运行：
   - 不会再次创建新稿件（日志提示 `PENDING_BVID` 跳过该场次）
3. 当 `update_video_bvids()` 回填 BVID 后：
   - 追加分P从正确的 `P{n}` 开始（不会反复 `P2`）
   - B站分P标题与 `part_title` 一致（不是文件名）
4. 定时任务运行期间 API 不出现明显卡顿（验证 `async` 内无阻塞 sleep）
5. 文件时间戳与场次时间存在几分钟偏差时，仍能正确分配到该场次（未分配数量明显下降）

