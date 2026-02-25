import asyncio
import os
import glob
import subprocess
import logging
import platform
import re
import shlex
import shutil
import yaml
from datetime import datetime, timedelta
from typing import Optional

# 从同一目录导入配置和 Bilibili 工具
import config
try:
    from bilitool import LoginController, UploadController, FeedController  # 假设需要这些
except Exception:  # pragma: no cover - optional when using biliup CLI backend
    LoginController = UploadController = FeedController = None

# 导入 API 客户端

# 导入数据库相关模块
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from models import UploadedVideo, StreamSession # 需要导入模型

# 配置日志记录
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 全局变量 --- 
yaml_config = {} # 用于存储从 config.yaml 读取的配置

_BILIUP_BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")


def _project_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _preferred_arch_tokens() -> list[str]:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return ["x86_64", "amd64"]
    if machine in {"aarch64", "arm64"}:
        return ["aarch64", "arm64"]
    if machine.startswith("arm"):
        return ["arm"]
    return [machine]


def _candidate_sort_key(path: str) -> tuple[int, int, str]:
    lowered = path.lower()
    arch_match = any(token in lowered for token in _preferred_arch_tokens())
    # On Debian/glibc, prefer the non-musl binary first.
    is_musl = "musl" in lowered
    return (0 if arch_match else 1, 1 if is_musl else 0, lowered)


def _resolve_biliup_bin_path() -> Optional[str]:
    configured = str(getattr(config, "BILIUP_BIN_PATH", "") or "").strip()
    if configured:
        configured_path = os.path.expanduser(configured)
        if not os.path.isabs(configured_path):
            configured_path = os.path.join(_project_root(), configured_path)
        if os.path.isfile(configured_path):
            return configured_path
        logging.warning(f"BILIUP_BIN_PATH 配置的文件不存在: {configured_path}，将尝试自动探测")

    path_bin = shutil.which("biliup")
    if path_bin:
        return path_bin

    repo_candidates = glob.glob(
        os.path.join(_project_root(), "third-party", "**", "biliup"),
        recursive=True,
    )
    repo_candidates = [p for p in repo_candidates if os.path.isfile(p)]
    if not repo_candidates:
        return None

    repo_candidates.sort(key=_candidate_sort_key)
    return repo_candidates[0]


def _resolve_biliup_cookies_path(biliup_bin_path: str) -> Optional[str]:
    configured = str(getattr(config, "BILIUP_COOKIES_PATH", "") or "").strip()
    fallback = configured or str(getattr(config, "COOKIES_PATH", "cookies.json"))
    cookie_path = os.path.expanduser(fallback)
    if not os.path.isabs(cookie_path):
        cookie_path = os.path.join(_project_root(), cookie_path)
    if os.path.isfile(cookie_path):
        return cookie_path

    sibling_cookie = os.path.join(os.path.dirname(biliup_bin_path), "cookies.json")
    if os.path.isfile(sibling_cookie):
        return sibling_cookie

    logging.warning(f"未找到 biliup cookies 文件，尝试路径: {cookie_path} / {sibling_cookie}")
    return None


def _get_biliup_runtime() -> dict[str, Optional[str]]:
    biliup_bin = _resolve_biliup_bin_path()
    if not biliup_bin:
        raise RuntimeError("未找到 biliup 可执行文件，请配置 BILIUP_BIN_PATH 或将 biliup 加入 PATH")

    cookies_path = _resolve_biliup_cookies_path(biliup_bin)
    if not cookies_path:
        raise RuntimeError("未找到 biliup 的 cookies.json，请配置 BILIUP_COOKIES_PATH")

    submit_mode = str(getattr(config, "BILIUP_SUBMIT_MODE", "app") or "app").strip()
    if submit_mode not in {"app", "b-cut-android"}:
        logging.warning(f"BILIUP_SUBMIT_MODE={submit_mode} 不受支持，回退为 app")
        submit_mode = "app"

    line = str(getattr(config, "BILIUP_LINE", "") or "").strip() or None
    return {
        "bin": biliup_bin,
        "cookies": cookies_path,
        "submit": submit_mode,
        "line": line,
    }


def _detect_uploader_backend() -> str:
    configured = str(getattr(config, "BILIBILI_UPLOADER_BACKEND", "auto") or "auto").strip().lower()
    if configured not in {"auto", "bilitool", "biliup_cli"}:
        logging.warning(f"BILIBILI_UPLOADER_BACKEND={configured} 无效，回退为 auto")
        configured = "auto"
    if configured != "auto":
        return configured

    try:
        _get_biliup_runtime()
        return "biliup_cli"
    except Exception:
        return "bilitool"


def _run_biliup_cli_command(cmd: list[str]):
    logging.info(f"执行 biliup 命令: {' '.join(shlex.quote(part) for part in cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.stdout:
        for line in result.stdout.splitlines():
            logging.info(f"[biliup] {line}")
    if result.stderr:
        for line in result.stderr.splitlines():
            logging.warning(f"[biliup stderr] {line}")
    if result.returncode != 0:
        logging.error(f"biliup 命令执行失败，退出码: {result.returncode}")
    return result


def _extract_biliup_bvid(output: str) -> Optional[str]:
    match = _BILIUP_BVID_RE.search(output or "")
    return match.group(0) if match else None


def _biliup_create_submit_succeeded(output: str, returncode: int) -> bool:
    if returncode != 0:
        return False
    return (
        "投稿成功" in output
        or "APP接口投稿成功" in output
        or '"code": Number(0)' in output
        or "code: 0" in output
    )


def _biliup_append_submit_succeeded(output: str, returncode: int) -> bool:
    if returncode != 0:
        return False
    return (
        "稿件修改成功" in output
        or "投稿成功" in output
        or '"code": Number(0)' in output
    )


def _normalize_tags(tag) -> str:
    if isinstance(tag, (list, tuple)):
        return ",".join(str(item) for item in tag if str(item).strip())
    return str(tag or "")


def _biliup_check_login() -> bool:
    try:
        runtime = _get_biliup_runtime()
    except Exception as e:
        logging.error(f"初始化 biliup 运行环境失败: {e}")
        return False

    result = _run_biliup_cli_command([
        runtime["bin"],
        "-u", runtime["cookies"],
        "renew",
    ])
    return result.returncode == 0


def _biliup_upload_video_entry(
    *,
    video_path: str,
    tid: int,
    title: str,
    desc: str,
    tag,
    source: str,
    cover: str,
    dynamic: str,
    copyright: int = 2,
) -> tuple[bool, Optional[str]]:
    runtime = _get_biliup_runtime()
    cmd = [
        runtime["bin"],
        "-u", runtime["cookies"],
        "upload",
        "--submit", runtime["submit"],
        "--tid", str(tid),
        "--title", str(title),
        "--desc", str(desc or ""),
        "--tag", _normalize_tags(tag),
        "--copyright", str(copyright),
    ]
    if runtime.get("line"):
        cmd.extend(["--line", str(runtime["line"])])
    if source:
        cmd.extend(["--source", str(source)])
    if cover:
        cmd.extend(["--cover", str(cover)])
    if dynamic:
        cmd.extend(["--dynamic", str(dynamic)])
    cmd.append(video_path)

    result = _run_biliup_cli_command(cmd)
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    success = _biliup_create_submit_succeeded(output, result.returncode)
    return success, _extract_biliup_bvid(output)


def _biliup_append_video_entry(*, video_path: str, bvid: str, part_title: Optional[str] = None) -> bool:
    runtime = _get_biliup_runtime()
    if part_title:
        logging.info("biliup append 当前版本未提供分P标题参数，将使用文件名作为分P标题")

    cmd = [
        runtime["bin"],
        "-u", runtime["cookies"],
        "append",
        "--submit", runtime["submit"],
        "--vid", str(bvid),
    ]
    if runtime.get("line"):
        cmd.extend(["--line", str(runtime["line"])])
    cmd.append(video_path)

    result = _run_biliup_cli_command(cmd)
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    return _biliup_append_submit_succeeded(output, result.returncode)

# 从文件名解析时间戳的函数
def get_timestamp_from_filename(filepath):
    """从文件名解析时间戳，适配 '银剑君录播YYYY-MM-DDTHH_mm_ss.mp4' 格式"""
    filename = os.path.basename(filepath)
    try:
        # 适配 '银剑君录播YYYY-MM-DDTHH_mm_ss.mp4' 格式
        timestamp_str = filename.split('录播')[-1].split('.')[0].replace('T', ' ')
        return datetime.strptime(timestamp_str, '%Y-%m-%d %H_%M_%S')
    except (IndexError, ValueError) as e:
        logging.warning(f"无法从文件名 {filename} 解析时间戳: {e}，将使用当前时间。")
        return datetime.now()

def load_yaml_config():
    """加载 config.yaml 文件，并验证必要键"""
    global yaml_config
    try:
        with open(config.YAML_CONFIG_PATH, 'r', encoding='utf-8') as f:
            yaml_config = yaml.safe_load(f)
            if not isinstance(yaml_config, dict):
                logging.error(f"读取 {config.YAML_CONFIG_PATH} 失败: 文件内容不是有效的 YAML 字典格式。")
                yaml_config = {} # 重置为空字典
                return False
            logging.info(f"成功加载配置文件: {config.YAML_CONFIG_PATH}")
            
            # --- 验证必要的键 ---
            # 确保这些键存在于 config.yaml 中
            required_keys = ['title', 'tid', 'tag', 'source', 'cover', 'dynamic', 'desc'] 
            missing_keys = [key for key in required_keys if key not in yaml_config]
            
            if missing_keys:
                logging.error(f"配置文件 {config.YAML_CONFIG_PATH} 中缺少以下必须的键: {', '.join(missing_keys)}。请补充完整。")
                # 可以在这里设置默认值，但最好是要求用户配置完整
                # 例如: yaml_config.setdefault('cdn', 'ws') 
                return False # 缺少必要配置，加载失败

            # --- 读取可选配置 (带默认值) ---
            # 检查 title 模板是否包含 {time} (可选检查)
            if '{time}' not in yaml_config.get('title', ''):
                 logging.warning(f"配置文件中的 'title' ('{yaml_config.get('title')}') 不包含 '{{time}}' 占位符。将使用固定标题。")

            return True
            
    except FileNotFoundError:
        logging.error(f"配置文件 {config.YAML_CONFIG_PATH} 未找到。请确保该文件存在。")
        yaml_config = {}
        return False
    except yaml.YAMLError as e:
        logging.error(f"解析配置文件 {config.YAML_CONFIG_PATH} 时出错: {e}")
        yaml_config = {}
        return False
    except Exception as e:
        logging.error(f"加载配置文件时发生未知错误: {e}")
        yaml_config = {}
        return False

async def upload_to_bilibili(db: AsyncSession):
    """上传 UPLOAD_FOLDER 中的 MP4 文件到 Bilibili
    
    逻辑说明:
    1. 获取待上传文件夹中的所有MP4文件
    2. 与数据库对比，筛选出尚未上传的视频
    3. 根据直播场次(时间段)对视频分组
    4. 对每个时间段：
       - 时间段内的第一个视频使用upload_video_entry创建新稿件
       - 获取到BVID后，才能对该时间段的其他视频使用append_video_entry追加分P
    5. 如果无法获取BVID，该时间段所有视频暂不上传，等待下次运行
    """
    global yaml_config
    if not yaml_config:
        logging.error("Bilibili 上传配置 (config.yaml) 未成功加载，跳过上传步骤。")
        return

    # 检查是否跳过视频压制
    is_skip_encoding = config.SKIP_VIDEO_ENCODING
    if is_skip_encoding:
        logging.info("检测到 SKIP_VIDEO_ENCODING=True 配置，将寻找并上传 FLV 文件")
        video_extension = "flv"
        title_suffix = config.NO_DANMAKU_TITLE_SUFFIX
    else:
        logging.info("将寻找并上传压制后的 MP4 文件")
        video_extension = "mp4"
        title_suffix = config.DANMAKU_TITLE_SUFFIX

    logging.info(f"开始检查并上传视频到 Bilibili (文件类型: {video_extension})...")
    uploaded_count = 0
    error_count = 0
    uploader_backend = _detect_uploader_backend()
    logging.info(f"B站上传后端: {uploader_backend}")
    
    # 1. 检查登录状态
    upload_controller = None
    feed_controller = None
    try:
        if uploader_backend == "biliup_cli":
            if not _biliup_check_login():
                logging.error("biliup 登录验证失败，请检查 cookies.json 文件是否有效。")
                return
            logging.info("biliup 登录验证成功。")
        else:
            if LoginController is None:
                logging.error("未安装 bilitool，且当前上传后端配置为 bilitool")
                return
            login_controller = LoginController()
            if not login_controller.check_bilibili_login():
                logging.error("Bilibili 登录验证失败，请检查 cookies.json 文件是否有效或已生成。")
                return
            logging.info("Bilibili 登录验证成功。")
    except Exception as e:
        logging.error(f"检查 Bilibili 登录状态时出错: {e}")
        return
    
    if not config.API_ENABLED:
        logging.error("API 功能未配置或明确禁用，无法执行上传")
        return
    
    if uploader_backend == "bilitool":
        upload_controller = UploadController()
        feed_controller = FeedController()
    
    # 2. 获取所有待上传的视频文件并按时间戳排序
    video_files = glob.glob(os.path.join(config.UPLOAD_FOLDER, f"*.{video_extension}"))
    if not video_files:
        logging.info(f"在上传目录中没有找到 {video_extension.upper()} 文件，无需上传。")
        return
    
    logging.info(f"上传目录中共找到 {len(video_files)} 个 {video_extension.upper()} 文件")
    
    try:
        # 对所有视频文件按时间戳排序
        video_files.sort(key=get_timestamp_from_filename)
    except Exception as e:
        logging.error(f"根据时间戳排序文件时出错: {e}，将按默认顺序处理。")
    
    # 3. 筛选出已上传的文件，构建文件信息列表
    video_info_list = []
    already_uploaded_files = []
    
    for file_path in video_files:
        file_name = os.path.basename(file_path)
        timestamp = get_timestamp_from_filename(file_path)
        
        try:
            # 检查数据库中是否有该文件的上传记录
            query = select(UploadedVideo).filter(UploadedVideo.first_part_filename == file_name)
            result = await db.execute(query)
            existing_record = result.scalars().first()
            
            if existing_record:
                logging.info(f"文件 {file_name} 已有上传记录 (BVID: {existing_record.bvid or '未获取'})，跳过")
                already_uploaded_files.append(file_path)
            else:
                video_info_list.append({
                    'path': file_path,
                    'filename': file_name,
                    'timestamp': timestamp
                })
        except Exception as e:
            logging.error(f"检查文件 {file_name} 是否已上传时出错: {e}")
    
    # 从待处理列表中移除已上传的文件
    logging.info(f"已从待处理列表中移除 {len(already_uploaded_files)} 个已上传的文件")
    if not video_info_list:
        logging.info("没有未上传的视频文件，结束上传流程")
        return
    
    logging.info(f"待上传的视频文件共 {len(video_info_list)} 个")
    
    # 4. 获取所有直播场次信息（近三天）
    try:
        streamer_name = config.DEFAULT_STREAMER_NAME
        
        # A. 获取近三天内有完整上下播记录的场次
        complete_sessions_query = select(StreamSession).filter(
            StreamSession.streamer_name == streamer_name,
            StreamSession.start_time.is_not(None),  # 必须有上播时间
            StreamSession.end_time.is_not(None),    # 必须有下播时间
            StreamSession.end_time > datetime.now() - timedelta(days=3)
        ).order_by(StreamSession.start_time)
        
        complete_sessions_result = await db.execute(complete_sessions_query)
        complete_sessions = complete_sessions_result.scalars().all()
        
        # B. 获取有上播时间但尚未下播的场次（正在进行的直播）
        current_session_query = select(StreamSession).filter(
            StreamSession.streamer_name == streamer_name,
            StreamSession.start_time.is_not(None),  # 必须有上播时间
            StreamSession.end_time.is_(None)        # 没有下播时间 = 正在直播
        ).order_by(desc(StreamSession.start_time)).limit(1)  # 只获取最近的一个
        
        current_session_result = await db.execute(current_session_query)
        current_session = current_session_result.scalars().first()
        
        # 合并两种场次
        all_sessions = list(complete_sessions)
        if current_session:
            # 将当前正在进行的直播添加到场次列表中
            all_sessions.append(current_session)
            logging.info(f"发现当前正在进行的直播，开始于: {current_session.start_time}")
            
        if not all_sessions:
            logging.warning(f"主播 {streamer_name} 没有可用的直播场次记录，无法划分直播场次")
            return
            
        logging.info(f"共获取到 {len(all_sessions)} 条直播场次记录（含 {len(complete_sessions)} 条完整记录和 {1 if current_session else 0} 条进行中的直播）")
    except Exception as e:
        logging.error(f"获取直播场次信息时出错: {e}")
        return
    
    # 5. 根据直播场次将视频分组
    # 为每个场次创建时间范围
    session_time_buffer = timedelta(minutes=config.STREAM_START_TIME_ADJUSTMENT)
    session_ranges = []
    for session in all_sessions:
        # 对于已结束的直播，使用实际的开始和结束时间
        # 对于正在进行的直播，使用开始时间到当前时间作为范围
        end_time = session.end_time if session.end_time else datetime.now()
        
        session_ranges.append({
            'start_time': session.start_time - session_time_buffer,
            'end_time': end_time + session_time_buffer,
            'session_id': session.id,
            'is_current': session.end_time is None  # 标记是否为当前直播
        })
    
    # 将视频分配到对应的时间段
    session_videos = {}
    unassigned_videos = []  # 存储无法分配的视频
    
    for video_info in video_info_list:
        video_time = video_info['timestamp']
        assigned = False
        
        for session_range in session_ranges:
            # 判断视频时间是否在某个直播场次内
            if session_range['start_time'] <= video_time <= session_range['end_time']:
                session_id = session_range['session_id']
                if session_id not in session_videos:
                    session_videos[session_id] = {
                        'videos': [],
                        'is_current': session_range['is_current']
                    }
                
                session_videos[session_id]['videos'].append(video_info)
                assigned = True
                break
        
        if not assigned:
            logging.warning(f"无法确定视频 {video_info['filename']} 所属的直播场次，将保存到未分配列表")
            unassigned_videos.append(video_info)
    
    if not session_videos and not unassigned_videos:
        logging.info("没有视频能够匹配到任何直播场次，结束上传流程")
        return
    
    logging.info(f"视频已分组到 {len(session_videos)} 个直播场次，另有 {len(unassigned_videos)} 个视频无法分配")
    
    # 6. 处理每个时间段的视频上传
    for session_id, session_data in session_videos.items():
        videos = session_data['videos']
        is_current_session = session_data['is_current']
        
        if not videos:
            continue
        
        # 对该时间段内的视频按时间排序
        videos.sort(key=lambda x: x['timestamp'])
        session_start_time = min(v['timestamp'] for v in videos)
        formatted_date = session_start_time.strftime('%Y-%m-%d')
        
        # 获取会话详情
        session_query = select(StreamSession).filter(StreamSession.id == session_id)
        session_result = await db.execute(session_query)
        session = session_result.scalars().first()
        
        if is_current_session:
            logging.info(f"开始处理当前进行中的直播 ID:{session_id} (开始于 {session.start_time}) 的 {len(videos)} 个视频")
        else:
            logging.info(f"开始处理已结束的直播场次 ID:{session_id} ({formatted_date}) 的 {len(videos)} 个视频")
        
        # 获取该时间段的BVID（根据时间段查询）
        period_start = session.start_time - session_time_buffer
        period_end = (session.end_time or datetime.now()) + session_time_buffer
        
        logging.info(f"查询直播场次 ID:{session_id} 的时间范围(含buffer): {period_start} 到 {period_end}")

        # 查询数据库中该时间段上传的视频的BVID
        query = select(UploadedVideo).filter(
            UploadedVideo.upload_time.between(period_start, period_end),
            UploadedVideo.bvid.is_not(None)
        ).order_by(desc(UploadedVideo.upload_time)).limit(1)
        result = await db.execute(query)
        existing_record = result.scalars().first()
        
        existing_bvid = None
        if existing_record:
            existing_bvid = existing_record.bvid
            logging.info(f"该直播场次已有上传记录，BVID: {existing_bvid}")

        if not existing_bvid:
            pending_query = select(UploadedVideo).filter(
                UploadedVideo.upload_time.between(period_start, period_end),
                UploadedVideo.bvid.is_(None),
            ).order_by(desc(UploadedVideo.upload_time)).limit(1)
            pending_result = await db.execute(pending_query)
            pending_record = pending_result.scalars().first()

            if pending_record:
                logging.info(
                    f"直播场次 ID:{session_id} 已存在待回填BVID的上传记录，"
                    "本次跳过创建新稿件，等待BVID回填后再追加分P"
                )
                continue
        
        # 根据是否有BVID决定上传方式
        if existing_bvid:
            # --- 情况1: 追加分P ---
            bvid = existing_bvid
            logging.info(f"将以分P形式追加视频到 BVID: {bvid}")
            
            try:
                # Determine next part number by counting uploaded records in this session window.
                count_query = select(func.count()).select_from(UploadedVideo).filter(
                    UploadedVideo.upload_time.between(period_start, period_end)
                )
                count_result = await db.execute(count_query)
                uploaded_in_period = count_result.scalar_one()
                start_part_number = uploaded_in_period + 1
                
                # 获取CDN参数
                cdn = yaml_config.get('cdn')
                
                # 逐个追加视频
                part_number = start_part_number
                for video_info in videos:
                    file_path = video_info['path']
                    file_name = video_info['filename']
                    
                    # 再次检查是否已上传（double check）
                    recheck_query = select(UploadedVideo).filter(
                        UploadedVideo.first_part_filename == file_name
                    )
                    recheck_result = await db.execute(recheck_query)
                    if recheck_result.scalars().first():
                        logging.info(f"二次检查: 文件 {file_name} 已上传，跳过")
                        continue
                    
                    # 分P标题处理
                    try:
                        video_time = video_info['timestamp']
                        part_time_str = video_time.strftime('%H:%M:%S')
                        
                        # 根据是否跳过压制添加不同的标题后缀
                        if is_skip_encoding:
                            part_title = f"P{part_number} {part_time_str} {title_suffix}"
                        else:
                            part_title = f"P{part_number} {part_time_str}"
                    except Exception:
                        # 如果时间戳解析失败，仍然添加后缀
                        if is_skip_encoding:
                            part_title = f"P{part_number} {title_suffix}"
                        else:
                            part_title = f"P{part_number}"
                    
                    logging.info(f"准备追加分P ({part_title}): {file_name}")
                    
                    # 调用追加接口
                    if uploader_backend == "biliup_cli":
                        append_success = _biliup_append_video_entry(
                            video_path=file_path,
                            bvid=bvid,
                            part_title=part_title,
                        )
                    else:
                        append_success = upload_controller.append_video_entry(
                            video_path=file_path,
                            bvid=bvid,
                            cdn=cdn,
                            video_name=part_title,
                        )
                    
                    if append_success:
                        logging.info(f"成功追加分P: {file_name}")
                        uploaded_count += 1
                        
                        # 记录到数据库
                        try:
                            new_upload = UploadedVideo(
                                bvid=None,
                                title=f"{part_title} (分P)",
                                first_part_filename=file_name,
                                upload_time=video_info['timestamp']  # 设置录制时间
                            )
                            db.add(new_upload)
                            await db.commit()
                            logging.info(f"已将分P信息记录到数据库 (文件: {file_name}, BVID: {bvid})")
                            
                            # 处理文件
                            if config.DELETE_UPLOADED_FILES:
                                try:
                                    os.remove(file_path)
                                    logging.info(f"已删除已上传的视频: {file_name}")
                                except OSError as e:
                                    logging.warning(f"删除已上传视频失败: {e}")
                        except Exception as db_e:
                            logging.error(f"将视频分P信息记录到数据库时出错: {db_e}")
                            await db.rollback()
                    else:
                        logging.error(f"追加分P失败: {file_name}")
                        error_count += 1
                    
                    part_number += 1
            
            except Exception as e:
                logging.error(f"处理直播场次 ID:{session_id} 的追加分P时出错: {e}")
                continue
        
        else:
            # --- 情况2: 创建新稿件 ---
            logging.info(f"该直播场次尚未上传视频，将创建新稿件")
            
            # 只处理第一个视频，创建新稿件
            first_video_info = videos[0]
            first_video_path = first_video_info['path']
            first_video_filename = first_video_info['filename']
            
            # 获取上传参数
            try:
                tid = yaml_config['tid']
                tag = yaml_config['tag']
                source = yaml_config['source']
                cover = yaml_config['cover']
                dynamic = yaml_config['dynamic']
                video_desc = yaml_config['desc']
                title_template = yaml_config['title']
                cdn = yaml_config.get('cdn')
            except KeyError as e:
                logging.error(f"缺少必要的上传参数: {e}")
                continue
            
            # 生成标题
            title = title_template
            try:
                # 从视频信息获取时间
                video_time = first_video_info['timestamp']
                formatted_time = video_time.strftime('%Y年%m月%d日')
                
                # 替换标题中的时间占位符
                if '{time}' in title_template:
                    title = title_template.replace('{time}', formatted_time)
                elif len(videos) > 1:  # 有多个文件，使用合集标题
                    title = f"{title_template} (合集 {video_time.strftime('%Y-%m-%d')})"
                
                # 如果跳过压制，添加无弹幕标题后缀
                if is_skip_encoding:
                    title = f"{title} {title_suffix}"
                
            except Exception as e:
                logging.warning(f"生成标题时出错: {e}，使用默认标题: {title}")
                # 即使出错也添加无弹幕标题后缀
                if is_skip_encoding:
                    title = f"{title} {title_suffix}"
            
            logging.info(f"上传首个视频，创建稿件。标题: {title}")
            
            # 调用上传接口
            acquired_bvid = None
            if uploader_backend == "biliup_cli":
                try:
                    upload_result, acquired_bvid = _biliup_upload_video_entry(
                        video_path=first_video_path,
                        tid=tid,
                        title=title,
                        copyright=2,
                        desc=video_desc,
                        tag=tag,
                        source=source,
                        cover=cover,
                        dynamic=dynamic,
                    )
                except Exception as cli_e:
                    logging.error(f"调用 biliup 上传失败: {cli_e}")
                    upload_result = False
            else:
                upload_result = upload_controller.upload_video_entry(
                    video_path=first_video_path,
                    yaml=None,
                    tid=tid,
                    title=title,
                    copyright=2,
                    desc=video_desc,
                    tag=tag,
                    source=source,
                    cover=cover,
                    dynamic=dynamic,
                    cdn=cdn
                )
            
            if upload_result:
                logging.info(f"成功上传首个视频: {first_video_filename}")
                uploaded_count += 1
                
                # 记录到数据库（biliup 可直接返回 BVID；bilitool 先置空后回填）
                try:
                    new_upload = UploadedVideo(
                        bvid=acquired_bvid,
                        title=title,
                        first_part_filename=first_video_filename,
                        upload_time=first_video_info['timestamp']  # 设置录制时间
                    )
                    db.add(new_upload)
                    await db.commit()
                    await db.refresh(new_upload)
                    record_id = new_upload.id
                    logging.info(
                        f"已将视频信息记录到数据库 (ID: {record_id}, 标题: {title}, "
                        f"BVID: {acquired_bvid or '暂无'})"
                    )
                    
                    # 处理文件
                    if config.DELETE_UPLOADED_FILES:
                        try:
                            os.remove(first_video_path)
                            logging.info(f"已删除已上传的视频: {first_video_filename}")
                        except OSError as e:
                            logging.warning(f"删除已上传视频失败: {e}")
                    
                    if uploader_backend == "bilitool":
                        # 等待获取BVID
                        logging.info("上传成功，等待15秒后尝试获取BVID...")
                        await asyncio.sleep(15)
                        
                        # 从B站API获取BVID
                        acquired_bvid = None
                        for attempt in range(3):  # 尝试最多3次
                            try:
                                # 调用B站API获取视频列表
                                video_list_data = feed_controller.get_video_dict_info(
                                    size=20,
                                    status_type="pubed,is_pubing",
                                )
                                
                                # 查找匹配标题的视频
                                if video_list_data and isinstance(video_list_data, dict):
                                    for video_title, video_bvid in video_list_data.items():
                                        if video_title == title and isinstance(video_bvid, str) and video_bvid.startswith('BV'):
                                            acquired_bvid = video_bvid
                                            logging.info(f"成功获取BVID: {acquired_bvid}")
                                            break
                                
                                # 如果在分P视频中找到了BVID，则更新数据库
                                if acquired_bvid:
                                    # 更新数据库中的BVID
                                    new_upload.bvid = acquired_bvid
                                    await db.commit()
                                    logging.info(f"已更新数据库记录 ID:{record_id} 的BVID为 {acquired_bvid}")
                                    break  # 成功获取BVID，退出重试循环
                                else:
                                    logging.warning(f"第 {attempt+1} 次尝试未获取到BVID，{5} 秒后重试...")
                                    await asyncio.sleep(5)  # 等待5秒后重试
                                    
                            except Exception as api_e:
                                logging.error(f"获取BVID时出错: {api_e}")
                                await asyncio.sleep(5)  # 出错后等待5秒再重试
                        
                        # 如果获取不到BVID，则提示用户稍后再运行
                        if not acquired_bvid:
                            logging.warning("无法获取BVID，请等待B站处理完成后再运行程序，追加分P")
                            # 此时数据库记录中的BVID仍为None，下次运行时会尝试更新
                            continue # 结束本场次处理，不追加分P
                        
                        # 如果获取到BVID且该场次有多个视频，则继续追加
                        # 因为已经花费了时间等待BVID，为确保上传完整性，本次就不继续上传分P
                        # 下次程序运行时会使用已保存的BVID继续追加其他分P
                        if len(videos) > 1:
                            logging.info(f"已获取BVID: {acquired_bvid}，但为确保稳定性，将在下次运行时追加剩余的 {len(videos)-1} 个分P")
                    else:
                        if acquired_bvid:
                            logging.info(f"biliup 已直接返回 BVID: {acquired_bvid}")
                            if len(videos) > 1:
                                logging.info(f"已获取BVID: {acquired_bvid}，下次运行时将继续追加剩余的 {len(videos)-1} 个分P")
                        else:
                            logging.warning("biliup 上传成功但未解析到BVID，本次仅记录首个视频；请稍后人工确认稿件后再继续追加分P")
                
                except Exception as db_e:
                    logging.error(f"将视频信息记录到数据库或获取BVID时出错: {db_e}")
                    await db.rollback()
            else:
                logging.error(f"上传首个视频失败: {first_video_filename}")
                error_count += 1
    
    # 处理未分配的视频
    if unassigned_videos and len(unassigned_videos) > 0:
        logging.info(f"开始处理 {len(unassigned_videos)} 个未分配到直播场次的视频")
        
        # 对于无法分配到直播场次的视频，我们可以将它们作为独立的一组处理
        # 检查是否已有相关BVID
        today = datetime.now().strftime('%Y-%m-%d')
        
        # 为标题添加无弹幕后缀（如果需要）
        title_keyword = f"直播记录 {today}"
        if is_skip_encoding:
            title_keyword = f"{title_keyword} {title_suffix}"
        
        # 查找今天的上传记录
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = datetime.now()
        
        query = select(UploadedVideo).filter(
            UploadedVideo.upload_time.between(today_start, today_end),
            UploadedVideo.bvid.is_not(None),
            UploadedVideo.title.contains(title_keyword)
        ).order_by(desc(UploadedVideo.upload_time)).limit(1)
        
        result = await db.execute(query)
        today_record = result.scalars().first()
        
        existing_bvid = None
        if today_record:
            existing_bvid = today_record.bvid
            logging.info(f"找到今天的上传记录，BVID: {existing_bvid}")
        
        # 按照与直播场次相同的逻辑处理
        # 对未分配视频按时间排序
        unassigned_videos.sort(key=lambda x: x['timestamp'])
        
        if existing_bvid:
            # 以分P形式追加
            logging.info(f"将未分配视频以分P形式追加到今天的记录 BVID: {existing_bvid}")
            # 处理未分配视频的追加上传逻辑与上面类似，这里代码略去
            # ... 
        else:
            # 创建新稿件
            logging.info(f"为未分配视频创建新稿件")
            
            # 只处理第一个视频，创建新稿件
            if unassigned_videos:
                first_video_info = unassigned_videos[0]
                # 处理未分配视频的上传逻辑与上面类似，这里代码略去
                # ...
    
    # 根据上传的文件类型更新日志信息
    file_type = "FLV" if is_skip_encoding else "MP4"
    logging.info(f"Bilibili {file_type} 视频上传完成。共处理 {len(video_info_list)} 个文件，成功上传: {uploaded_count}，失败: {error_count}")


async def update_video_bvids(db: AsyncSession):
    """检查并更新数据库中缺失BVID的视频记录 (直接操作数据库)"""
    logging.info("开始检查和更新缺失BVID的视频记录...")
    if _detect_uploader_backend() == "biliup_cli":
        logging.info("当前使用 biliup CLI 上传后端（创建稿件时通常可直接拿到BVID），跳过旧 API 回填任务")
        return
    
    try:
        # 1. 检查登录状态，确保能调用B站API
        if LoginController is None or FeedController is None:
            logging.error("未安装 bilitool，无法执行旧 API 的 BVID 回填")
            return
        login_controller = LoginController()
        if not login_controller.check_bilibili_login():
            logging.error("Bilibili 登录验证失败，无法更新BVID信息")
            return
            
        feed_controller = FeedController()
        
        # 2. 获取所有没有BVID的记录 (直接查询数据库)
        try:
            query = select(UploadedVideo).filter(
                UploadedVideo.bvid.is_(None)
            ).order_by(desc(UploadedVideo.upload_time))
            result = await db.execute(query)
            no_bvid_records = result.scalars().all()
            
            if not no_bvid_records:
                logging.info("没有找到需要更新BVID的视频记录")
                return
                
            logging.info(f"找到 {len(no_bvid_records)} 条缺失BVID的记录，尝试更新...")
        except Exception as db_e:
            logging.error(f"从数据库获取缺失BVID记录时出错: {db_e}")
            return
        
        # 3. 调用B站API获取视频列表
        try:
            # 尝试获取已上传和正在上传的视频列表
            videos_published = feed_controller.get_video_dict_info(size=20, status_type='pubed')
            videos_pending = feed_controller.get_video_dict_info(size=10, status_type='is_pubing')
            
            all_videos = {}
            # 合并两个字典，优先使用已发布的视频信息
            if isinstance(videos_pending, dict):
                all_videos.update(videos_pending)
            if isinstance(videos_published, dict):
                all_videos.update(videos_published)
                
            if not all_videos:
                logging.warning("未从B站API获取到任何视频信息")
                return
                
            logging.info(f"从B站API获取到 {len(all_videos)} 条视频信息")
            
            # 4. 根据标题匹配更新BVID
            updated_count = 0
            for record in no_bvid_records:
                record_id = record.id
                record_title = record.title
                
                if not record_id or not record_title:
                    continue
                    
                # 在B站视频中查找匹配的标题
                found_bvid = None
                for video_title, video_bvid in all_videos.items():
                    if video_title == record_title and isinstance(video_bvid, str) and video_bvid.startswith('BV'):
                        found_bvid = video_bvid
                        break # 找到第一个匹配就跳出
                
                # 如果找到BVID，更新数据库
                if found_bvid:
                    try:
                        # 检查该 BVID 是否已被其他记录使用
                        bvid_query = select(UploadedVideo).filter(
                            UploadedVideo.bvid == found_bvid,
                            UploadedVideo.id != record_id
                        )
                        bvid_result = await db.execute(bvid_query)
                        bvid_exists = bvid_result.scalars().first()

                        if bvid_exists:
                            logging.warning(f"尝试更新 BVID {found_bvid} 失败，因为它已被记录 ID:{bvid_exists.id} 使用")
                            continue # 跳过此记录

                        # 更新记录
                        record.bvid = found_bvid
                        await db.commit()
                        await db.refresh(record)
                        logging.info(f"成功更新记录 ID:{record_id}, 标题:'{record_title}' 的BVID为 {found_bvid}")
                        updated_count += 1
                    except Exception as update_e:
                         logging.error(f"更新记录 ID:{record_id} 的BVID ({found_bvid}) 时数据库出错: {update_e}")
                         await db.rollback() # 出错时回滚
            
            logging.info(f"BVID更新完成，共更新了 {updated_count}/{len(no_bvid_records)} 条记录")
            
        except Exception as e:
            logging.error(f"调用B站API获取视频列表或更新BVID时出错: {e}")
    
    except Exception as e:
        logging.error(f"更新视频BVID过程中发生错误: {e}")
