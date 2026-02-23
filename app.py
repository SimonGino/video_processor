import os
import uvicorn
import logging
import argparse
from datetime import datetime, timedelta
from typing import Optional, List, AsyncGenerator
from urllib.parse import urlparse
from functools import partial

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from uploader import (
    load_yaml_config,
    get_timestamp_from_filename,
)
from models import Base, StreamSession, UploadedVideo, local_now
from stream_monitor import StreamStatusMonitor
from scheduler import (
    scheduled_video_pipeline,
    scheduled_log_stream_end,
    clean_stale_sessions,
    run_processing_sync,
    run_upload_async,
)

# =================== Database Setup ===================

DATABASE_URL = f"sqlite+aiosqlite:///{os.path.dirname(os.path.abspath(__file__))}/app_data.db"

engine = create_async_engine(DATABASE_URL, echo=False, future=True)

AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        try:
            await conn.run_sync(Base.metadata.create_all)
            logger.info("数据库表结构已创建或已存在")
        except Exception as e:
            logger.error(f"初始化数据库结构时出错: {e}", exc_info=True)

# =================== Pydantic Models ===================


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

# =================== FastAPI App ===================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("app")

app = FastAPI(
    title="视频处理 API",
    description="提供主播下播记录、视频处理和上传功能",
    version="0.2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Scheduler and Monitors ---
scheduler = AsyncIOScheduler()
stream_monitors: dict[str, StreamStatusMonitor] = {}

# =================== Startup / Shutdown ===================


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

        for name, monitor in stream_monitors.items():
            scheduler.add_job(
                partial(scheduled_log_stream_end, name),
                'interval',
                minutes=config.STREAM_STATUS_CHECK_INTERVAL,
                id=f'log_stream_end_{name}',
                replace_existing=True
            )
            logger.info(f"定时任务调度器：已添加主播 {name} 的状态检测任务，每 {config.STREAM_STATUS_CHECK_INTERVAL} 分钟执行一次")

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


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("正在关闭定时任务调度器...")
    if scheduler.running:
        scheduler.shutdown()
        logger.info("定时任务调度器已关闭。")
    else:
        logger.info("定时任务调度器未运行。")

# =================== API Endpoints ===================


@app.post("/log_stream_end", response_model=StreamSessionResponse)
async def log_stream_end(
    request: StreamEndRequest,
    db: AsyncSession = Depends(get_db)
):
    try:
        end_time = request.end_time or local_now()

        new_session = StreamSession(
            streamer_name=request.streamer_name,
            start_time=request.start_time,
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


@app.post("/record_upload", response_model=UploadedVideoResponse)
async def record_upload(
    title: str,
    first_part_filename: str,
    bvid: str = None,
    db: AsyncSession = Depends(get_db)
):
    try:
        if bvid:
            query = select(UploadedVideo).filter(UploadedVideo.bvid == bvid)
            result = await db.execute(query)
            existing = result.scalars().first()

            if existing:
                logger.warning(f"尝试记录已存在的视频 BVID: {bvid}")
                raise HTTPException(status_code=400, detail=f"视频 BVID {bvid} 已存在")

        query = select(UploadedVideo).filter(UploadedVideo.first_part_filename == first_part_filename)
        result = await db.execute(query)
        file_exists = result.scalars().first()

        if file_exists:
            if bvid and not file_exists.bvid:
                file_exists.bvid = bvid
                await db.commit()
                await db.refresh(file_exists)
                logger.info(f"已更新视频记录的BVID: {title} (BVID: {bvid})")
                return file_exists

            logger.warning(f"尝试记录已存在的文件: {first_part_filename}")
            raise HTTPException(status_code=400, detail=f"文件 {first_part_filename} 已存在记录")

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


@app.put("/update_video_bvid/{video_id}", response_model=UploadedVideoResponse)
async def update_video_bvid(
    video_id: int,
    bvid: str,
    db: AsyncSession = Depends(get_db)
):
    try:
        if not bvid or not bvid.startswith('BV'):
            raise HTTPException(status_code=400, detail="无效的BVID格式")

        bvid_query = select(UploadedVideo).filter(
            UploadedVideo.bvid == bvid,
            UploadedVideo.id != video_id
        )
        bvid_result = await db.execute(bvid_query)
        bvid_exists = bvid_result.scalars().first()

        if bvid_exists:
            logger.warning(f"BVID {bvid} 已存在于记录 ID: {bvid_exists.id}")
            raise HTTPException(status_code=400, detail=f"BVID {bvid} 已存在于其他记录中")

        query = select(UploadedVideo).filter(UploadedVideo.id == video_id)
        result = await db.execute(query)
        video = result.scalars().first()

        if not video:
            raise HTTPException(status_code=404, detail=f"未找到ID为 {video_id} 的视频记录")

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
            end_time=None
        )

        db.add(new_session)
        await db.commit()
        await db.refresh(new_session)

        logger.info(f"已手动记录主播 {request.streamer_name} 的上播时间: {start_time}")
        return new_session
    except Exception as e:
        logger.error(f"记录上播信息时出错: {e}")
        raise HTTPException(status_code=500, detail=f"记录上播信息失败: {str(e)}")

# =================== Task Trigger Endpoints ===================


@app.post("/run_processing_tasks", response_model=TaskResponse)
async def trigger_processing_tasks(background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Trigger background video processing (cleanup, convert, encode)."""
    if config.PROCESS_AFTER_STREAM_END:
        monitor = stream_monitors.get(config.STREAMER_NAME)
        if monitor and monitor.is_live():
            logger.info(f"手动触发：检测到主播 {config.STREAMER_NAME} 正在直播中，当前配置为仅下播后处理，拒绝执行压制任务")
            return {"message": f"主播 {config.STREAMER_NAME} 正在直播中，当前配置为仅下播后处理，无法执行压制任务"}
        logger.info(f"手动触发：主播 {config.STREAMER_NAME} 当前不在直播，将继续执行压制任务")

    is_skip_encoding = config.SKIP_VIDEO_ENCODING

    background_tasks.add_task(run_processing_sync)

    if is_skip_encoding:
        logger.info("已将视频处理任务添加到后台执行队列 (手动触发，跳过压制步骤)")
        return {"message": "视频处理任务已开始在后台执行 (手动触发，跳过压制步骤，直接处理FLV文件)"}
    else:
        logger.info("已将视频处理任务添加到后台执行队列 (手动触发)")
        return {"message": "视频处理任务已开始在后台执行 (手动触发，包含压制步骤)"}


@app.post("/run_upload_tasks", response_model=TaskResponse)
async def trigger_upload_tasks(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Trigger background BVID update and upload tasks."""
    if config.PROCESS_AFTER_STREAM_END:
        monitor = stream_monitors.get(config.STREAMER_NAME)
        if monitor and monitor.is_live():
            logger.info(f"手动触发：检测到主播 {config.STREAMER_NAME} 正在直播中，当前配置为仅下播后处理，拒绝执行上传任务")
            return {"message": f"主播 {config.STREAMER_NAME} 正在直播中，当前配置为仅下播后处理，无法执行上传任务"}
        logger.info(f"手动触发：主播 {config.STREAMER_NAME} 当前不在直播，将继续执行上传任务")

    is_skip_encoding = config.SKIP_VIDEO_ENCODING
    file_type = "FLV" if is_skip_encoding else "MP4"

    background_tasks.add_task(run_upload_async, db)

    logger.info(f"已将BVID更新和上传任务添加到后台执行队列 (手动触发，将上传{file_type}文件)")
    return {"message": f"BVID更新和上传任务已开始在后台执行 (手动触发，将上传{file_type}文件)"}

# =================== Server Startup ===================


def start_api_server():
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

    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        reload=args.reload
    )

if __name__ == "__main__":
    start_api_server()
