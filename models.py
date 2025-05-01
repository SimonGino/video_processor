from sqlalchemy import Column, Integer, String, DateTime, Boolean, desc, select
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base
from typing import Optional
from datetime import datetime

# 创建 Declarative Base
Base = declarative_base()

# =================== 数据模型 ===================

class StreamSession(Base):
    __tablename__ = 'stream_sessions'

    id = Column(Integer, primary_key=True, index=True)
    streamer_name = Column(String, index=True, nullable=False)
    end_time = Column(DateTime, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<StreamSession(streamer='{self.streamer_name}', end_time='{self.end_time}')>"

class UploadedVideo(Base):
    __tablename__ = 'uploaded_videos'

    id = Column(Integer, primary_key=True, index=True)
    bvid = Column(String, unique=True, index=True, nullable=True)  # 允许为空
    title = Column(String, nullable=False)
    first_part_filename = Column(String, nullable=False)
    upload_time = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<UploadedVideo(bvid='{self.bvid}', title='{self.title}')>" 