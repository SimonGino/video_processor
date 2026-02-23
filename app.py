import os
import uvicorn
import logging
import argparse
import time
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, AsyncGenerator
from urllib.parse import urlparse
from functools import partial

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, DateTime, desc, select, inspect
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from encoder import encode_video
from video_processor import (
    load_yaml_config,
    update_video_bvids,
    upload_to_bilibili,
    get_timestamp_from_filename
)
from danmaku import cleanup_small_files, convert_danmaku
from models import Base, StreamSession, UploadedVideo, local_now
from stream_monitor import StreamStatusMonitor

# =================== 数据库设置 ===================

# 使用 SQLite，数据库文件将存储在项目根目录
DATABASE_URL = f"sqlite+aiosqlite:///{os.path.dirname(os.path.abspath(__file__))}/app_data.db"

# 创建异步引擎
engine = create_async_engine(DATABASE_URL, echo=False, future=True)

# 创建异步会话工厂
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

# 依赖注入函数，用于 FastAPI
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

# 数据库初始化函数
async def init_db():
    async with engine.begin() as conn:
        try:
            # 直接创建表 - 如果表已存在会跳过
            await conn.run_sync(Base.metadata.create_all)
            logger.info("数据库表结构已创建或已存在")
        except Exception as e:
            logger.error(f"初始化数据库结构时出错: {e}", exc_info=True)

# =================== 数据模型 ===================

# class StreamSession(Base): ... # <- 移除模型定义

# class UploadedVideo(Base): ... # <- 移除模型定义

# =================== FastAPI 模型 ===================

class StreamEndRequest(BaseModel):
    streamer_name: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

class StreamSessionResponse(BaseModel):
    id: int
    streamer_name: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    created_at: datetime

    class Config:
        orm_mode = True

class UploadedVideoResponse(BaseModel):
    id: int
    bvid: Optional[str] = None
    title: str
    first_part_filename: str
    upload_time: Optional[datetime] = None
    created_at: datetime

    class Config:
        orm_mode = True
        
class TaskResponse(BaseModel):
    message: str

class StreamStartRequest(BaseModel):
    streamer_name: str
    start_time: Optional[datetime] = None

# =================== FastAPI 应用 ===================

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("app")
scheduler_logger = logging.getLogger("scheduler")

# 创建 FastAPI 应用
app = FastAPI(
    title="视频处理 API",
    description="提供主播下播记录、视频处理和上传功能",
    version="0.2.0" # 版本更新
)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 定时任务逻辑 ---
scheduler = AsyncIOScheduler()
stream_monitors: dict[str, StreamStatusMonitor] = {}

async def scheduled_video_pipeline():
    """定时执行的完整视频处理和上传流程"""
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

    # 检查是否配置了跳过视频压制
    is_skip_encoding = hasattr(config, 'SKIP_VIDEO_ENCODING') and config.SKIP_VIDEO_ENCODING
    if is_skip_encoding:
        scheduler_logger.info("定时任务：检测到 SKIP_VIDEO_ENCODING=True 配置，将跳过弹幕压制步骤，直接处理 FLV 文件")

    # --- 1. 同步处理任务 (在线程池中运行避免阻塞) ---
    try:
        scheduler_logger.info("定时任务：执行文件清理...")
        await loop.run_in_executor(None, cleanup_small_files) # None 使用默认 ThreadPoolExecutor
        
        # 如果不跳过压制，先执行弹幕转换
        if not is_skip_encoding:
            scheduler_logger.info("定时任务：执行弹幕转换...")
            await loop.run_in_executor(None, convert_danmaku)
        else:
            scheduler_logger.info("定时任务：已配置跳过压制，不执行弹幕转换")
            
        # 无论是否跳过压制，都调用 encode_video 函数
        # encode_video 函数已被修改为在 SKIP_VIDEO_ENCODING=True 时直接复制 FLV 文件到上传目录
        scheduler_logger.info("定时任务：处理视频文件...")
        await loop.run_in_executor(None, encode_video)
        
        scheduler_logger.info("定时任务：同步处理任务完成。")
    except Exception as e:
        scheduler_logger.error(f"定时任务：同步处理任务执行过程中出错: {e}", exc_info=True)
        # 即使同步任务出错，仍然尝试执行异步任务

    # --- 2. 异步上传和BVID更新任务 ---
    # 需要创建独立的 DB Session
    async with AsyncSessionLocal() as db:
        try:
            # 首先加载最新的YAML配置，以防手动修改过
            if not load_yaml_config():
                scheduler_logger.error("定时任务：无法加载 YAML 配置，跳过异步任务。")
            else:
                scheduler_logger.info("定时任务：执行 BVID 更新...")
                await update_video_bvids(db)
                scheduler_logger.info("定时任务：执行视频上传...")
                await upload_to_bilibili(db)
                scheduler_logger.info("定时任务：异步上传和BVID更新任务完成。")
        except Exception as e:
            scheduler_logger.error(f"定时任务：异步上传/BVID更新任务执行过程中出错: {e}", exc_info=True)
        finally:
            await db.close() # 确保会话关闭

    end_time = time.time()
    scheduler_logger.info(f"定时任务：视频处理和上传流程执行完毕。总耗时: {end_time - start_time:.2f} 秒。")

async def scheduled_log_stream_end(streamer_name: str):
    """Scheduled task: check streamer status and record start/end times.

    Uses StreamStatusMonitor.detect_change() for state tracking
    instead of function-attribute caching.
    """
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
    """定时任务：检查并清理长时间未正常结束的直播会话
    
    在某些情况下，如程序崩溃或重启，可能会导致直播会话只有start_time而没有end_time。
    此任务检查那些开始时间超过24小时但尚未结束的会话，自动标记为已结束。
    """
    logger.info("开始检查长时间未结束的直播会话...")
    
    try:
        async with AsyncSessionLocal() as db:
            # 查找所有开始时间超过24小时但没有结束时间的会话
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
                
            # 处理每个未结束的会话
            for session in stale_sessions:
                # 设置结束时间为开始时间后12小时（假设最长直播12小时）
                suggested_end_time = session.start_time + timedelta(hours=12)
                # 如果建议的结束时间超过当前时间，使用当前时间
                if suggested_end_time > local_now():
                    suggested_end_time = local_now()
                    
                session.end_time = suggested_end_time
                logger.info(f"已清理长时间未结束的会话 ID:{session.id}，设置结束时间为 {suggested_end_time}")
            
            # 提交所有变更
            await db.commit()
            logger.info(f"成功清理 {len(stale_sessions)} 个未正常结束的直播会话")
            
    except Exception as e:
        logger.error(f"清理未结束直播会话时出错: {e}", exc_info=True)

# 应用启动时初始化数据库、加载配置并启动定时任务
@app.on_event("startup")
async def startup_event():
    logger.info("正在初始化数据库...")
    await init_db()
    logger.info("数据库初始化完成")

    logger.info("正在加载 YAML 配置...")
    if not load_yaml_config():
        logger.error("无法加载或验证配置文件 config.yaml，部分 API 和定时任务可能无法正常工作")
    else:
        logger.info("YAML 配置加载完成")

    # Initialize stream monitors from config
    for streamer_cfg in config.STREAMERS:
        name = streamer_cfg["name"]
        room_id = streamer_cfg["room_id"]
        monitor = StreamStatusMonitor(room_id, name)
        await monitor.initialize()
        stream_monitors[name] = monitor
        logger.info(f"已初始化主播 {name} (房间号: {room_id}) 的状态监控")

    logger.info("正在启动定时任务调度器...")
    try:
        interval_minutes = config.SCHEDULE_INTERVAL_MINUTES
        scheduler.add_job(
            scheduled_video_pipeline,
            'interval',
            minutes=interval_minutes,
            id='video_pipeline_job',
            replace_existing=True,
            next_run_time=local_now()
        )

        # Add per-streamer status check jobs
        for name, monitor in stream_monitors.items():
            scheduler.add_job(
                partial(scheduled_log_stream_end, name),
                'interval',
                minutes=config.STREAM_STATUS_CHECK_INTERVAL,
                id=f'log_stream_end_{name}',
                replace_existing=True
            )
            logger.info(f"定时任务调度器：已添加主播 {name} 的状态检测任务，每 {config.STREAM_STATUS_CHECK_INTERVAL} 分钟执行一次")

        # Stale session cleanup job
        if stream_monitors:
            scheduler.add_job(
                clean_stale_sessions,
                'interval',
                hours=12,
                id='clean_stale_sessions_job',
                replace_existing=True
            )
            logger.info("定时任务调度器：已添加 'clean_stale_sessions_job'，每12小时执行一次")

        scheduler.start()
        logger.info(f"定时任务调度器已启动，每 {interval_minutes} 分钟执行一次 'video_pipeline_job'")
    except Exception as e:
        logger.error(f"启动定时任务调度器失败: {e}", exc_info=True)

# 应用关闭时停止调度器
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("正在关闭定时任务调度器...")
    if scheduler.running:
        scheduler.shutdown()
        logger.info("定时任务调度器已关闭。")
    else:
        logger.info("定时任务调度器未运行。")

# =================== API 端点 ===================

# 记录主播下播的端点
@app.post("/log_stream_end", response_model=StreamSessionResponse)
async def log_stream_end(
    request: StreamEndRequest,
    db: AsyncSession = Depends(get_db)
):
    try:
        end_time = request.end_time or local_now()
        
        new_session = StreamSession(
            streamer_name=request.streamer_name,
            start_time=request.start_time,  # 现在支持手动设置上播时间
            end_time=end_time
        )
        
        db.add(new_session)
        await db.commit()
        await db.refresh(new_session)
        
        logger.info(f"已手动记录主播 {request.streamer_name} 的直播会话 (上播: {request.start_time}, 下播: {end_time})")
        return new_session
    except Exception as e:
        logger.error(f"记录直播会话信息时出错: {e}")
        raise HTTPException(status_code=500, detail=f"记录直播会话信息失败: {str(e)}")

# 获取主播最近下播记录的端点
@app.get("/stream_sessions/{streamer_name}", response_model=List[StreamSessionResponse])
async def get_stream_sessions(
    streamer_name: str,
    limit: int = 10,
    db: AsyncSession = Depends(get_db)
):
    try:
        query = select(StreamSession).filter(
            StreamSession.streamer_name == streamer_name
        ).order_by(desc(StreamSession.end_time)).limit(limit)
        
        result = await db.execute(query)
        sessions = result.scalars().all()
        
        if not sessions:
            logger.warning(f"未找到主播 {streamer_name} 的下播记录")
            return []
            
        return sessions
    except Exception as e:
        logger.error(f"获取下播记录时出错: {e}")
        raise HTTPException(status_code=500, detail=f"获取下播记录失败: {str(e)}")

# 记录已上传视频的端点 (由 video_processor 调用)
@app.post("/record_upload", response_model=UploadedVideoResponse)
async def record_upload(
    title: str,
    first_part_filename: str,
    bvid: str = None,  # 修改为可选参数
    db: AsyncSession = Depends(get_db)
):
    try:
        # 如果提供了BVID，先检查是否已存在
        if bvid:
            query = select(UploadedVideo).filter(UploadedVideo.bvid == bvid)
            result = await db.execute(query)
            existing = result.scalars().first()
            
            if existing:
                logger.warning(f"尝试记录已存在的视频 BVID: {bvid}")
                raise HTTPException(status_code=400, detail=f"视频 BVID {bvid} 已存在")
        
        # 检查文件名是否已存在
        query = select(UploadedVideo).filter(UploadedVideo.first_part_filename == first_part_filename)
        result = await db.execute(query)
        file_exists = result.scalars().first()
        
        if file_exists:
            # 如果是提供BVID的更新操作
            if bvid and not file_exists.bvid:
                file_exists.bvid = bvid
                await db.commit()
                await db.refresh(file_exists)
                logger.info(f"已更新视频记录的BVID: {title} (BVID: {bvid})")
                return file_exists
            
            logger.warning(f"尝试记录已存在的文件: {first_part_filename}")
            raise HTTPException(status_code=400, detail=f"文件 {first_part_filename} 已存在记录")
        
        # 从文件名解析上传时间    
        upload_time = get_timestamp_from_filename(first_part_filename)
        
        new_upload = UploadedVideo(
            bvid=bvid,
            title=title,
            first_part_filename=first_part_filename,
            upload_time=upload_time
        )
        
        db.add(new_upload)
        await db.commit()
        await db.refresh(new_upload)
        
        if bvid:
            logger.info(f"已记录视频上传: {title} (BVID: {bvid}, 视频时间: {upload_time})")
        else:
            logger.info(f"已记录视频上传: {title} (暂无BVID, 视频时间: {upload_time})")
        return new_upload
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"记录视频上传时出错: {e}")
        raise HTTPException(status_code=500, detail=f"记录视频上传失败: {str(e)}")

# 检查文件是否属于已上传视频的端点
@app.get("/check_uploaded/{filename}")
async def check_uploaded(
    filename: str,
    db: AsyncSession = Depends(get_db)
):
    try:
        query = select(UploadedVideo).filter(
            UploadedVideo.first_part_filename == filename
        )
        result = await db.execute(query)
        existing = result.scalars().first()
        
        if existing:
            return {"uploaded": True, "bvid": existing.bvid, "title": existing.title}
        else:
            return {"uploaded": False}
    except Exception as e:
        logger.error(f"检查文件 {filename} 是否已上传时出错: {e}")
        raise HTTPException(status_code=500, detail=f"检查文件上传状态失败: {str(e)}")

# 获取最新 BVID 的端点 (供 video_processor 使用)
@app.get("/latest_bvid/{streamer_name}")
async def get_latest_bvid(
    streamer_name: str,
    db: AsyncSession = Depends(get_db)
):
    try:
        sessions_query = select(StreamSession).filter(
            StreamSession.streamer_name == streamer_name
        ).order_by(desc(StreamSession.end_time)).limit(2)
        
        sessions_result = await db.execute(sessions_query)
        recent_sessions = sessions_result.scalars().all()
        
        if len(recent_sessions) < 2:
            logger.warning(f"主播 {streamer_name} 的下播记录不足，无法确定最近的完整直播场次")
            return {"found": False, "reason": "insufficient_sessions"}
        
        upload_query = select(UploadedVideo).order_by(desc(UploadedVideo.created_at)).limit(1)
        upload_result = await db.execute(upload_query)
        latest_upload = upload_result.scalars().first()
        
        if latest_upload and latest_upload.bvid:
            return {
                "found": True,
                "bvid": latest_upload.bvid, 
                "title": latest_upload.title
            }
        else:
            return {"found": False, "reason": "no_uploads"}
    except Exception as e:
        logger.error(f"获取最新 BVID 时出错: {e}")
        raise HTTPException(status_code=500, detail=f"获取最新 BVID 失败: {str(e)}")

# 获取所有没有BVID的视频记录 (供 video_processor 使用)
@app.get("/videos_without_bvid", response_model=List[UploadedVideoResponse])
async def get_videos_without_bvid(
    db: AsyncSession = Depends(get_db)
):
    try:
        query = select(UploadedVideo).filter(
            UploadedVideo.bvid.is_(None)
        ).order_by(desc(UploadedVideo.upload_time))
        
        result = await db.execute(query)
        videos = result.scalars().all()
        
        if not videos:
            logger.info("没有找到缺失BVID的视频记录")
            return []
            
        logger.info(f"找到 {len(videos)} 条缺失BVID的视频记录")
        return videos
    except Exception as e:
        logger.error(f"获取缺失BVID的视频记录时出错: {e}")
        raise HTTPException(status_code=500, detail=f"获取缺失BVID的视频记录失败: {str(e)}")

# 更新视频记录的BVID (供 video_processor 使用)
@app.put("/update_video_bvid/{video_id}", response_model=UploadedVideoResponse)
async def update_video_bvid(
    video_id: int,
    bvid: str,
    db: AsyncSession = Depends(get_db)
):
    try:
        if not bvid or not bvid.startswith('BV'):
            raise HTTPException(status_code=400, detail="无效的BVID格式")
            
        # 检查BVID是否已存在于其他记录
        bvid_query = select(UploadedVideo).filter(
            UploadedVideo.bvid == bvid,
            UploadedVideo.id != video_id
        )
        bvid_result = await db.execute(bvid_query)
        bvid_exists = bvid_result.scalars().first()
        
        if bvid_exists:
            logger.warning(f"BVID {bvid} 已存在于记录 ID: {bvid_exists.id}")
            raise HTTPException(status_code=400, detail=f"BVID {bvid} 已存在于其他记录中")
        
        # 查找目标记录
        query = select(UploadedVideo).filter(UploadedVideo.id == video_id)
        result = await db.execute(query)
        video = result.scalars().first()
        
        if not video:
            raise HTTPException(status_code=404, detail=f"未找到ID为 {video_id} 的视频记录")
            
        # 更新BVID
        video.bvid = bvid
        await db.commit()
        await db.refresh(video)
        
        logger.info(f"已更新视频记录 ID: {video_id} 的BVID为 {bvid}")
        return video
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新视频BVID时出错: {e}")
        raise HTTPException(status_code=500, detail=f"更新视频BVID失败: {str(e)}")

# 记录主播上播的端点
@app.post("/log_stream_start", response_model=StreamSessionResponse)
async def log_stream_start(
    request: StreamStartRequest,
    db: AsyncSession = Depends(get_db)
):
    try:
        start_time = request.start_time or local_now()
        
        new_session = StreamSession(
            streamer_name=request.streamer_name,
            start_time=start_time,
            end_time=None  # 上播时end_time为空
        )
        
        db.add(new_session)
        await db.commit()
        await db.refresh(new_session)
        
        logger.info(f"已手动记录主播 {request.streamer_name} 的上播时间: {start_time}")
        return new_session
    except Exception as e:
        logger.error(f"记录上播信息时出错: {e}")
        raise HTTPException(status_code=500, detail=f"记录上播信息失败: {str(e)}")

# =================== 任务触发端点 ===================

def run_processing_sync():
    """同步执行处理任务，用于后台线程"""
    logger.info("后台任务：开始执行视频处理（清理、转换、压制）...")
    try:
        # 清理小文件始终执行
        cleanup_small_files()
        
        # 检查是否配置了跳过视频压制
        is_skip_encoding = hasattr(config, 'SKIP_VIDEO_ENCODING') and config.SKIP_VIDEO_ENCODING
        
        # 根据配置决定是否执行弹幕转换
        if not is_skip_encoding:
            logger.info("后台任务：执行弹幕转换...")
            convert_danmaku()
        else:
            logger.info("后台任务：已配置跳过压制，不执行弹幕转换")
        
        # 处理视频文件 (encode_video 函数会根据配置决定是压制还是直接复制)
        logger.info("后台任务：处理视频文件...")
        encode_video()
        
        logger.info("后台任务：视频处理执行完成")
    except Exception as e:
        logger.error(f"后台任务：视频处理执行过程中出错: {e}")

@app.post("/run_processing_tasks", response_model=TaskResponse)
async def trigger_processing_tasks(background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """触发后台执行清理、转换、压制任务"""
    
    # Check if "process only after stream ends" is enabled
    if config.PROCESS_AFTER_STREAM_END:
        monitor = stream_monitors.get(config.STREAMER_NAME)
        if monitor and monitor.is_live():
            logger.info(f"手动触发：检测到主播 {config.STREAMER_NAME} 正在直播中，当前配置为仅下播后处理，拒绝执行压制任务")
            return {"message": f"主播 {config.STREAMER_NAME} 正在直播中，当前配置为仅下播后处理，无法执行压制任务"}
        logger.info(f"手动触发：主播 {config.STREAMER_NAME} 当前不在直播，将继续执行压制任务")
    
    # 检查是否配置了跳过视频压制
    is_skip_encoding = hasattr(config, 'SKIP_VIDEO_ENCODING') and config.SKIP_VIDEO_ENCODING
    
    background_tasks.add_task(run_processing_sync)
    
    if is_skip_encoding:
        logger.info("已将视频处理任务添加到后台执行队列 (手动触发，跳过压制步骤)")
        return {"message": "视频处理任务已开始在后台执行 (手动触发，跳过压制步骤，直接处理FLV文件)"}
    else:
        logger.info("已将视频处理任务添加到后台执行队列 (手动触发)")
        return {"message": "视频处理任务已开始在后台执行 (手动触发，包含压制步骤)"}

async def run_upload_async(db: AsyncSession):
    """异步执行上传任务，用于后台任务"""
    logger.info("后台任务：开始执行BVID更新和视频上传 (手动触发)...")
    try:
        if not load_yaml_config(): # 手动触发时也加载配置
             logger.error("手动触发：无法加载 YAML 配置，跳过异步任务。")
             return
        await update_video_bvids(db)
        await upload_to_bilibili(db) # 传递 db 会话
        logger.info("后台任务：BVID更新和视频上传执行完成 (手动触发)")
    except Exception as e:
        logger.error(f"后台任务：BVID更新和视频上传执行过程中出错 (手动触发): {e}", exc_info=True)
    # 注意：手动触发时，db session 由 FastAPI 管理，不需要手动 close

@app.post("/run_upload_tasks", response_model=TaskResponse)
async def trigger_upload_tasks(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db) # 手动触发时从依赖注入获取 db
):
    """触发后台执行BVID更新和上传任务"""
    
    # Check if "process only after stream ends" is enabled
    if config.PROCESS_AFTER_STREAM_END:
        monitor = stream_monitors.get(config.STREAMER_NAME)
        if monitor and monitor.is_live():
            logger.info(f"手动触发：检测到主播 {config.STREAMER_NAME} 正在直播中，当前配置为仅下播后处理，拒绝执行上传任务")
            return {"message": f"主播 {config.STREAMER_NAME} 正在直播中，当前配置为仅下播后处理，无法执行上传任务"}
        logger.info(f"手动触发：主播 {config.STREAMER_NAME} 当前不在直播，将继续执行上传任务")
    
    # 检查是否配置了跳过视频压制
    is_skip_encoding = hasattr(config, 'SKIP_VIDEO_ENCODING') and config.SKIP_VIDEO_ENCODING
    file_type = "FLV" if is_skip_encoding else "MP4"
    
    # 注意：这里传递的 db 是通过 Depends(get_db) 获取的 request-scoped session
    # run_upload_async 需要能处理这种 session (它目前应该可以)
    background_tasks.add_task(run_upload_async, db) 
    
    logger.info(f"已将BVID更新和上传任务添加到后台执行队列 (手动触发，将上传{file_type}文件)")
    return {"message": f"BVID更新和上传任务已开始在后台执行 (手动触发，将上传{file_type}文件)"}

# =================== 启动服务器 ===================

def start_api_server():
    # 从 config.py 解析端口
    api_url = urlparse(config.API_BASE_URL)
    default_port = api_url.port or 8000
    
    parser = argparse.ArgumentParser(description="运行视频处理 API 服务器")
    parser.add_argument(
        "-H", "--host", 
        default="0.0.0.0", 
        help="绑定的主机 IP (默认: 0.0.0.0)"
    )
    parser.add_argument(
        "-p", "--port", 
        type=int, 
        default=default_port, 
        help=f"监听端口 (默认: {default_port}，来自 config.py)"
    )
    parser.add_argument(
        "--reload", 
        action="store_true", 
        help="启用自动重载 (开发模式)"
    )
    args = parser.parse_args()
    
    print(f"启动 API 服务器: http://{args.host}:{args.port}")
    print(f"配置的 API_BASE_URL: {config.API_BASE_URL}")
    print("按 Ctrl+C 停止服务器")
    
    # 启动 FastAPI 应用
    uvicorn.run(
        "app:app", # 指向当前的 app 对象
        host=args.host, 
        port=args.port, 
        reload=args.reload
    )

if __name__ == "__main__":
    start_api_server() 