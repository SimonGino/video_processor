import os

# --- 路径配置 ---
# 获取项目根目录
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# Use project-local folders by default (kept out of git via .gitignore)
PROCESSING_FOLDER = os.path.join(PROJECT_ROOT, "data", "processing")
UPLOAD_FOLDER = os.path.join(PROJECT_ROOT, "data", "upload")
# Bilibili 配置文件路径
YAML_CONFIG_PATH = "config.yaml"
# Bilibili Cookies 文件路径
COOKIES_PATH = "cookies.json"


# --- 文件清理配置 ---
# 删除小于此大小的 FLV 文件 (MB)
MIN_FILE_SIZE_MB = 10


# --- 弹幕转换配置 ---
# ASS 弹幕字体大小
FONT_SIZE = 40
# ASS 弹幕描边/阴影字体大小 (通常比 FONT_SIZE 小一点)
SC_FONT_SIZE = 38


# --- FFmpeg/FFprobe 配置 ---
# FFprobe 可执行文件路径 (如果不在 PATH 中，请指定完整路径)
FFPROBE_PATH = "ffprobe"
# FFmpeg 可执行文件路径 (如果不在 PATH 中，请指定完整路径)
FFMPEG_PATH = "ffmpeg"
# QSV 初始化时使用的 render 节点（留空表示使用 ffmpeg 默认自动探测）
FFMPEG_QSV_INIT_DEVICE = ""
# QSV/VAAPI 运行库与驱动兼容性覆盖（留空表示不覆盖）
FFMPEG_QSV_LD_LIBRARY_PATH = ""
FFMPEG_QSV_LIBVA_DRIVERS_PATH = ""
FFMPEG_QSV_LIBVA_DRIVER_NAME = ""

# --- 视频处理配置 ---
# 是否跳过视频压制步骤 (True: 跳过压制直接上传FLV, False: 压制为MP4后上传)
SKIP_VIDEO_ENCODING = False
# 无弹幕版本视频的标题后缀 (当跳过压制时使用)
NO_DANMAKU_TITLE_SUFFIX = "【无弹幕版】"
# 弹幕版本视频的标题后缀 (当不跳过压制时使用)
DANMAKU_TITLE_SUFFIX = "【弹幕版】"


# --- 调度配置 ---
# 定时任务执行间隔 (分钟)
SCHEDULE_INTERVAL_MINUTES = 60
# 检测主播状态的时间间隔 (分钟)
STREAM_STATUS_CHECK_INTERVAL = 10
# 检测主播状态时，开播时间向前调整的时间量 (分钟)
STREAM_START_TIME_ADJUSTMENT = 10

# --- 录制配置 ---
# 是否启用内建录制服务 (recording_service.py)
RECORDING_ENABLED = True
# 单段录制时长 (分钟)
RECORDING_SEGMENT_MINUTES = 60
# 录制失败/断流后的重试等待 (秒)
RECORDING_RETRY_DELAY_SECONDS = 10

# --- 斗鱼取流配置 ---
DOUYU_CDN = "hw-h5"
DOUYU_RATE = 0
DOUYU_DID = "10000000000000000000000000001501"

# --- 弹幕采集配置 ---
DANMAKU_WS_URL = "wss://danmuproxy.douyu.com:8506/"
DANMAKU_HEARTBEAT_SECONDS = 30

# --- 上传后文件处理 ---
# 上传成功后是否删除本地 MP4 文件 (True: 删除, False: 保留)
DELETE_UPLOADED_FILES = True
# 是否启用定时任务中的 BVID 更新与上传 (不影响手动 /run_upload_tasks)
SCHEDULED_UPLOAD_ENABLED = False

# --- 处理时机控制 ---
# 是否仅在主播下播后处理视频 (True: 仅下播后处理, False: 按定时任务处理)
PROCESS_AFTER_STREAM_END = False

# --- API 配置 ---
# API 服务器基础 URL
API_BASE_URL = "http://localhost:50009"
# API 服务器是否已启动 (如果为 False，将跳过依赖 API 的功能)
API_ENABLED = True

# --- 主播配置 ---
# 主播列表 (支持多主播监控)
STREAMERS = [
    {"name": "洞主", "room_id": "138243"},
]
# Backward compatibility
DEFAULT_STREAMER_NAME = STREAMERS[0]["name"]
STREAMER_NAME = STREAMERS[0]["name"]
DOUYU_ROOM_ID = STREAMERS[0]["room_id"]

# --- 其他 ---
# 确保处理和上传目录存在
os.makedirs(PROCESSING_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True) 
