import time
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import desc, select

import config
from danmaku import cleanup_small_files, convert_danmaku
from encoder import encode_video
from uploader import load_yaml_config, upload_to_bilibili, update_video_bvids
from models import StreamSession, local_now
from sqlalchemy.ext.asyncio import AsyncSession

scheduler_logger = logging.getLogger("scheduler")
logger = logging.getLogger("app")


def _get_app_deps():
    """Late import to avoid circular dependency with app module.

    Returns (AsyncSessionLocal, scheduler, stream_monitors).
    """
    from app import AsyncSessionLocal, scheduler, stream_monitors
    return AsyncSessionLocal, scheduler, stream_monitors


async def scheduled_video_pipeline():
    """Complete video processing and upload pipeline (scheduled task)."""
    AsyncSessionLocal, _, stream_monitors = _get_app_deps()

    scheduler_logger.info("定时任务：开始执行视频处理和上传流程...")
    loop = asyncio.get_running_loop()
    start_time = time.time()

    # Check if "process only after stream ends" is enabled
    if config.PROCESS_AFTER_STREAM_END:
        monitor = stream_monitors.get(config.STREAMER_NAME)
        if monitor and monitor.is_live():
            scheduler_logger.info(
                f"定时任务：检测到主播 {config.STREAMER_NAME} 正在直播中，"
                f"当前配置为仅下播后处理，跳过压制和上传任务"
            )
            return
        scheduler_logger.info(f"定时任务：主播 {config.STREAMER_NAME} 当前不在直播，将继续执行压制和上传任务")

    # Check skip encoding config
    is_skip_encoding = config.SKIP_VIDEO_ENCODING
    if is_skip_encoding:
        scheduler_logger.info("定时任务：检测到 SKIP_VIDEO_ENCODING=True 配置，将跳过弹幕压制步骤，直接处理 FLV 文件")

    # --- 1. Sync processing tasks (run in thread pool to avoid blocking) ---
    try:
        scheduler_logger.info("定时任务：执行文件清理...")
        await loop.run_in_executor(None, cleanup_small_files)

        if not is_skip_encoding:
            scheduler_logger.info("定时任务：执行弹幕转换...")
            await loop.run_in_executor(None, convert_danmaku)
        else:
            scheduler_logger.info("定时任务：已配置跳过压制，不执行弹幕转换")

        scheduler_logger.info("定时任务：处理视频文件...")
        await loop.run_in_executor(None, encode_video)

        scheduler_logger.info("定时任务：同步处理任务完成。")
    except asyncio.CancelledError:
        scheduler_logger.info("定时任务：同步处理任务在应用关闭过程中被取消")
        return
    except Exception as e:
        scheduler_logger.error(f"定时任务：同步处理任务执行过程中出错: {e}", exc_info=True)

    # --- 2. Async upload and BVID update tasks ---
    if not getattr(config, "SCHEDULED_UPLOAD_ENABLED", True):
        scheduler_logger.info("定时任务：已禁用定时上传，跳过 BVID 更新和视频上传任务")
    else:
        async with AsyncSessionLocal() as db:
            try:
                if not load_yaml_config():
                    scheduler_logger.error("定时任务：无法加载 YAML 配置，跳过异步任务。")
                else:
                    scheduler_logger.info("定时任务：执行 BVID 更新...")
                    await update_video_bvids(db)
                    scheduler_logger.info("定时任务：执行视频上传...")
                    await upload_to_bilibili(db)
                    scheduler_logger.info("定时任务：异步上传和BVID更新任务完成。")
            except asyncio.CancelledError:
                scheduler_logger.info("定时任务：异步上传/BVID 更新任务在应用关闭过程中被取消")
                return
            except Exception as e:
                scheduler_logger.error(f"定时任务：异步上传/BVID更新任务执行过程中出错: {e}", exc_info=True)
            finally:
                await db.close()

    end_time = time.time()
    scheduler_logger.info(f"定时任务：视频处理和上传流程执行完毕。总耗时: {end_time - start_time:.2f} 秒。")


async def scheduled_log_stream_end(streamer_name: str):
    """Scheduled task: check streamer status and record start/end times.

    Uses StreamStatusMonitor.detect_change() for state tracking
    instead of function-attribute caching.
    """
    AsyncSessionLocal, scheduler, stream_monitors = _get_app_deps()

    monitor = stream_monitors.get(streamer_name)
    if not monitor:
        scheduler_logger.error(f"定时任务(log_stream_end): 未找到主播 {streamer_name} 的监控实例")
        return

    current_time = local_now()
    change = await monitor.detect_change()

    if change is None:
        # No change or API error — skip
        scheduler_logger.debug(f"主播 {streamer_name} 状态未变化，仍为: {'直播中' if monitor.is_live() else '未直播'}")
        return

    old_status, new_status = change
    scheduler_logger.info(
        f"检测到主播 {streamer_name} 状态变化: "
        f"{'未直播→直播中' if new_status else '直播中→未直播'}"
    )

    async with AsyncSessionLocal() as db:
        try:
            if new_status:
                # Went live — record start time (adjusted backward)
                adjusted_start_time = current_time - timedelta(minutes=config.STREAM_START_TIME_ADJUSTMENT)
                new_session = StreamSession(
                    streamer_name=streamer_name,
                    start_time=adjusted_start_time,
                    end_time=None
                )
                db.add(new_session)
                scheduler_logger.info(
                    f"已记录主播 {streamer_name} 的上播时间: {adjusted_start_time} "
                    f"(已自动调整-{config.STREAM_START_TIME_ADJUSTMENT}分钟)"
                )
            else:
                # Went offline — find open session and set end_time
                query = select(StreamSession).filter(
                    StreamSession.streamer_name == streamer_name,
                    StreamSession.start_time.is_not(None),
                    StreamSession.end_time.is_(None)
                ).order_by(desc(StreamSession.start_time))

                result = await db.execute(query)
                recent_session = result.scalars().first()

                if recent_session:
                    recent_session.end_time = current_time
                    scheduler_logger.info(f"已记录主播 {streamer_name} 的下播时间: {current_time}")
                else:
                    new_session = StreamSession(
                        streamer_name=streamer_name,
                        start_time=None,
                        end_time=current_time
                    )
                    db.add(new_session)
                    scheduler_logger.info(f"创建新记录并添加主播 {streamer_name} 的下播时间: {current_time}")

            await db.commit()

            # If streamer went offline and PROCESS_AFTER_STREAM_END is enabled,
            # schedule a delayed pipeline run instead of blocking with sleep
            if not new_status and config.PROCESS_AFTER_STREAM_END:
                scheduler_logger.info("检测到主播下播，且已启用'仅下播后处理'选项，3分钟后触发视频处理和上传流程")
                scheduler.add_job(
                    scheduled_video_pipeline,
                    'date',
                    run_date=local_now() + timedelta(minutes=3),
                    id=f'post_stream_pipeline_{streamer_name}',
                    replace_existing=True
                )

        except Exception as e:
            scheduler_logger.error(f"定时任务(log_stream_end): 记录直播状态时出错: {e}", exc_info=True)
            await db.rollback()


async def clean_stale_sessions():
    """Clean up stale stream sessions that started 24h+ ago but never got an end_time."""
    AsyncSessionLocal, _, _ = _get_app_deps()

    logger.info("开始检查长时间未结束的直播会话...")

    try:
        async with AsyncSessionLocal() as db:
            yesterday = local_now() - timedelta(hours=24)
            query = select(StreamSession).filter(
                StreamSession.start_time.is_not(None),
                StreamSession.start_time < yesterday,
                StreamSession.end_time.is_(None)
            )

            result = await db.execute(query)
            stale_sessions = result.scalars().all()

            if not stale_sessions:
                logger.info("没有发现长时间未结束的直播会话")
                return

            for session in stale_sessions:
                suggested_end_time = session.start_time + timedelta(hours=12)
                if suggested_end_time > local_now():
                    suggested_end_time = local_now()

                session.end_time = suggested_end_time
                logger.info(f"已清理长时间未结束的会话 ID:{session.id}，设置结束时间为 {suggested_end_time}")

            await db.commit()
            logger.info(f"成功清理 {len(stale_sessions)} 个未正常结束的直播会话")

    except Exception as e:
        logger.error(f"清理未结束直播会话时出错: {e}", exc_info=True)


def run_processing_sync():
    """Synchronous video processing for background thread execution."""
    logger.info("后台任务：开始执行视频处理（清理、转换、压制）...")
    try:
        cleanup_small_files()

        is_skip_encoding = config.SKIP_VIDEO_ENCODING

        if not is_skip_encoding:
            logger.info("后台任务：执行弹幕转换...")
            convert_danmaku()
        else:
            logger.info("后台任务：已配置跳过压制，不执行弹幕转换")

        logger.info("后台任务：处理视频文件...")
        encode_video()

        logger.info("后台任务：视频处理执行完成")
    except Exception as e:
        logger.error(f"后台任务：视频处理执行过程中出错: {e}")


async def run_upload_async(db: AsyncSession):
    """Async upload task for background execution."""
    logger.info("后台任务：开始执行BVID更新和视频上传 (手动触发)...")
    try:
        if not load_yaml_config():
             logger.error("手动触发：无法加载 YAML 配置，跳过异步任务。")
             return
        await update_video_bvids(db)
        await upload_to_bilibili(db)
        logger.info("后台任务：BVID更新和视频上传执行完成 (手动触发)")
    except Exception as e:
        logger.error(f"后台任务：BVID更新和视频上传执行过程中出错 (手动触发): {e}", exc_info=True)
