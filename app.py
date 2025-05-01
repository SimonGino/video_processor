import os
import uvicorn
import requests
import logging
import argparse
import threading
import time
from datetime import datetime
from typing import Dict, Any, Optional, List, AsyncGenerator
from urllib.parse import urlparse

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, DateTime, desc, select
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

import config
# 导入 video_processor.py 中的函数
from video_processor import (
    load_yaml_config, 
    cleanup_small_files, 
    convert_danmaku, 
    encode_video, 
    update_video_bvids, 
    upload_to_bilibili
)
# 导入模型
from models import Base, StreamSession, UploadedVideo

# =================== 数据库设置 ===================

# 使用 SQLite，数据库文件将存储在项目根目录
DATABASE_URL = f"sqlite+aiosqlite:///{os.path.dirname(os.path.abspath(__file__))}/app_data.db"

# 创建异步引擎
engine = create_async_engine(DATABASE_URL, echo=True, future=True)

# 创建异步会话工厂
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

# 依赖注入函数，用于 FastAPI
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

# 数据库初始化函数
async def init_db():
    async with engine.begin() as conn:
        # 使用从 models.py 导入的 Base
        await conn.run_sync(Base.metadata.create_all)

# =================== 数据模型 ===================

# class StreamSession(Base): ... # <- 移除模型定义

# class UploadedVideo(Base): ... # <- 移除模型定义

# =================== FastAPI 模型 ===================

class StreamEndRequest(BaseModel):
    streamer_name: str
    end_time: Optional[datetime] = None

class StreamSessionResponse(BaseModel):
    id: int
    streamer_name: str
    end_time: datetime
    created_at: datetime

    class Config:
        orm_mode = True

class UploadedVideoResponse(BaseModel):
    id: int
    bvid: Optional[str] = None
    title: str
    first_part_filename: str
    upload_time: datetime

    class Config:
        orm_mode = True
        
class TaskResponse(BaseModel):
    message: str

# =================== FastAPI 应用 ===================

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("app")

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

# 应用启动时初始化数据库和加载配置
@app.on_event("startup")
async def startup_event():
    logger.info("正在初始化数据库...")
    await init_db()
    logger.info("数据库初始化完成")
    
    logger.info("正在加载 YAML 配置...")
    if not load_yaml_config():
        logger.error("无法加载或验证配置文件 config.yaml，部分 API 可能无法正常工作")
    else:
        logger.info("YAML 配置加载完成")

# =================== API 端点 ===================

# 记录主播下播的端点
@app.post("/log_stream_end", response_model=StreamSessionResponse)
async def log_stream_end(
    request: StreamEndRequest,
    db: AsyncSession = Depends(get_db)
):
    try:
        end_time = request.end_time or datetime.now()
        
        new_session = StreamSession(
            streamer_name=request.streamer_name,
            end_time=end_time
        )
        
        db.add(new_session)
        await db.commit()
        await db.refresh(new_session)
        
        logger.info(f"已记录主播 {request.streamer_name} 的下播时间: {end_time}")
        return new_session
    except Exception as e:
        logger.error(f"记录下播信息时出错: {e}")
        raise HTTPException(status_code=500, detail=f"记录下播信息失败: {str(e)}")

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
            
        new_upload = UploadedVideo(
            bvid=bvid,
            title=title,
            first_part_filename=first_part_filename
        )
        
        db.add(new_upload)
        await db.commit()
        await db.refresh(new_upload)
        
        if bvid:
            logger.info(f"已记录视频上传: {title} (BVID: {bvid})")
        else:
            logger.info(f"已记录视频上传: {title} (暂无BVID)")
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
        
        upload_query = select(UploadedVideo).order_by(desc(UploadedVideo.upload_time)).limit(1)
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

# =================== 任务触发端点 ===================

def run_processing_sync():
    """同步执行处理任务，用于后台线程"""
    logger.info("后台任务：开始执行视频处理（清理、转换、压制）...")
    try:
        cleanup_small_files()
        convert_danmaku()
        encode_video()
        logger.info("后台任务：视频处理执行完成")
    except Exception as e:
        logger.error(f"后台任务：视频处理执行过程中出错: {e}")

@app.post("/run_processing_tasks", response_model=TaskResponse)
async def trigger_processing_tasks(background_tasks: BackgroundTasks):
    """触发后台执行清理、转换、压制任务"""
    background_tasks.add_task(run_processing_sync)
    logger.info("已将视频处理任务添加到后台执行队列")
    return {"message": "视频处理任务已开始在后台执行"}

async def run_upload_async(db: AsyncSession):
    """异步执行上传任务，用于后台任务"""
    logger.info("后台任务：开始执行BVID更新和视频上传...")
    try:
        await update_video_bvids(db)
        await upload_to_bilibili(db) # 传递 db 会话
        logger.info("后台任务：BVID更新和视频上传执行完成")
    except Exception as e:
        logger.error(f"后台任务：BVID更新和视频上传执行过程中出错: {e}")

@app.post("/run_upload_tasks", response_model=TaskResponse)
async def trigger_upload_tasks(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """触发后台执行BVID更新和上传任务"""
    background_tasks.add_task(run_upload_async, db)
    logger.info("已将BVID更新和上传任务添加到后台执行队列")
    return {"message": "BVID更新和上传任务已开始在后台执行"}

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