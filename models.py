from sqlalchemy import Column, Integer, String, DateTime, Boolean, desc, select
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base
from typing import Optional
from datetime import datetime, timezone, timedelta

# 创建 Declarative Base
Base = declarative_base()

# 定义本地时区函数（中国为UTC+8）
def local_now():
    """返回当前的本地时间（UTC+8）"""
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)

# =================== 数据模型 ===================

class StreamSession(Base):
    __tablename__ = 'stream_sessions'

    id = Column(Integer, primary_key=True, index=True)
    streamer_name = Column(String, nullable=False, index=True)
    start_time = Column(DateTime, nullable=True)  # 新增上播时间字段，允许为空以兼容旧数据
    end_time = Column(DateTime, nullable=True)    # 下播时间，修改为可为空
    created_at = Column(DateTime, default=local_now)  # 使用本地时区函数

    def __repr__(self):
        return f"<StreamSession(streamer='{self.streamer_name}', end_time='{self.end_time}')>"

class UploadedVideo(Base):
    __tablename__ = 'uploaded_videos'

    id = Column(Integer, primary_key=True, index=True)
    bvid = Column(String, nullable=True, unique=True)
    title = Column(String, nullable=False)
    first_part_filename = Column(String, nullable=False, unique=True)
    upload_time = Column(DateTime, default=local_now)  # 使用本地时区函数

    def __repr__(self):
        return f"<UploadedVideo(bvid='{self.bvid}', title='{self.title}')>" 