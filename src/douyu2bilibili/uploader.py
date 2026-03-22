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
from . import config
try:
    from bilitool import LoginController, UploadController, FeedController  # 假设需要这些
except Exception:  # pragma: no cover - optional when using biliup CLI backend
    LoginController = UploadController = FeedController = None

# 导入 API 客户端

# 导入数据库相关模块
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, and_, or_

from .models import UploadedVideo, StreamSession # 需要导入模型

# 配置日志记录
logger = logging.getLogger("upload.uploader")

# --- 全局变量 ---
yaml_config = {} # 用于存储从 config.yaml 读取的配置
streamer_configs = {}  # 主播名 -> 上传元数据 dict
upload_global_config = {}  # 全局上传配置 (max_concurrent 等)
_upload_semaphore: Optional[asyncio.Semaphore] = None  # 上传并发控制信号量

_BILIUP_BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_BILIUP_CODE_RE = re.compile(r'"code"\s*:\s*(?:Number\()?(\d+)\)?')


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


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
        logger.warning(f"BILIUP_BIN_PATH 配置的文件不存在: {configured_path}，将尝试自动探测")

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

    logger.warning(f"未找到 biliup cookies 文件，尝试路径: {cookie_path} / {sibling_cookie}")
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
        logger.warning(f"BILIUP_SUBMIT_MODE={submit_mode} 不受支持，回退为 app")
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
        logger.warning(f"BILIBILI_UPLOADER_BACKEND={configured} 无效，回退为 auto")
        configured = "auto"
    if configured != "auto":
        return configured

    try:
        _get_biliup_runtime()
        return "biliup_cli"
    except Exception:
        return "bilitool"


_CGROUP_PROCS_PATH = "/sys/fs/cgroup/biliup-limit/cgroup.procs"


def _assign_pid_to_cgroup(pid: int) -> None:
    """Try to write *pid* into the biliup-limit cgroup for bandwidth limiting.

    Silently skips if the cgroup directory does not exist (limiter not set up).
    Logs a warning on permission or other write errors without aborting.
    """
    if not os.path.exists(os.path.dirname(_CGROUP_PROCS_PATH)):
        return  # cgroup not configured — silent skip
    try:
        with open(_CGROUP_PROCS_PATH, "w") as f:
            f.write(str(pid))
        logger.info(f"已将 biliup 进程 PID {pid} 写入 cgroup ({_CGROUP_PROCS_PATH})")
    except Exception as e:
        logger.warning(f"无法将 PID {pid} 写入 cgroup: {e}")


def _run_biliup_cli_command(cmd: list[str]):
    logger.info(f"执行 biliup 命令: {' '.join(shlex.quote(part) for part in cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _assign_pid_to_cgroup(proc.pid)
    stdout, stderr = proc.communicate()
    result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    if result.stdout:
        for line in result.stdout.splitlines():
            logger.info(f"[biliup] {line}")
    if result.stderr:
        for line in result.stderr.splitlines():
            logger.warning(f"[biliup stderr] {line}")
    if result.returncode != 0:
        logger.error(f"biliup 命令执行失败，退出码: {result.returncode}")
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


def _extract_biliup_error_code(output: str) -> Optional[int]:
    match = _BILIUP_CODE_RE.search(output or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _is_biliup_rate_limited(output: str, returncode: int) -> bool:
    if returncode == 0:
        return False
    code = _extract_biliup_error_code(output)
    return code == 21540


def _normalize_tags(tag) -> str:
    if isinstance(tag, (list, tuple)):
        return ",".join(str(item) for item in tag if str(item).strip())
    return str(tag or "")


def _get_uploaded_file_delete_delay_hours() -> int:
    try:
        return max(0, int(getattr(config, "DELETE_UPLOADED_FILES_DELAY_HOURS", 0)))
    except (TypeError, ValueError):
        return 0


def _handle_uploaded_file_after_success(file_path: str, file_name: str) -> None:
    """Delete uploaded file immediately or keep it for delayed cleanup."""
    if not getattr(config, "DELETE_UPLOADED_FILES", False):
        return

    delay_hours = _get_uploaded_file_delete_delay_hours()
    if delay_hours > 0:
        logger.info(
            f"已启用延时删除({delay_hours}小时)，暂不删除已上传视频: {file_name}"
        )
        return

    try:
        os.remove(file_path)
        logger.info(f"已删除已上传的视频: {file_name}")
    except OSError as e:
        logger.warning(f"删除已上传视频失败: {e}")


async def cleanup_delayed_uploaded_files(db: AsyncSession) -> None:
    """Delete locally uploaded files after a configured retention delay."""
    if not getattr(config, "DELETE_UPLOADED_FILES", False):
        return

    delay_hours = _get_uploaded_file_delete_delay_hours()
    if delay_hours <= 0:
        return

    cutoff = datetime.now() - timedelta(hours=delay_hours)
    upload_dir = getattr(config, "UPLOAD_FOLDER", "")
    if not upload_dir:
        return

    logger.info(
        f"开始清理延时删除到期文件（保留期: {delay_hours} 小时，截止时间: {cutoff.strftime('%Y-%m-%d %H:%M:%S')}）"
    )

    try:
        query = select(UploadedVideo).filter(
            or_(
                and_(
                    UploadedVideo.created_at.is_not(None),
                    UploadedVideo.created_at < cutoff,
                ),
                and_(
                    UploadedVideo.created_at.is_(None),
                    UploadedVideo.upload_time.is_not(None),
                    UploadedVideo.upload_time < cutoff,
                ),
            )
        ).order_by(UploadedVideo.created_at)
        result = await db.execute(query)
        candidates = result.scalars().all()

        deleted_count = 0
        for record in candidates:
            file_name = record.first_part_filename
            if not file_name:
                continue
            file_path = os.path.join(upload_dir, file_name)
            if not os.path.isfile(file_path):
                continue
            try:
                os.remove(file_path)
                deleted_count += 1
                logger.info(f"延时删除已上传视频成功: {file_name}")
            except OSError as e:
                logger.warning(f"延时删除已上传视频失败 {file_name}: {e}")

        logger.info(f"延时删除清理完成，共删除 {deleted_count} 个文件")
    except Exception as e:
        logger.error(f"执行延时删除清理时出错: {e}")


def _biliup_check_login() -> bool:
    try:
        runtime = _get_biliup_runtime()
    except Exception as e:
        logger.error(f"初始化 biliup 运行环境失败: {e}")
        return False

    result = _run_biliup_cli_command([
        runtime["bin"],
        "-u", runtime["cookies"],
        "renew",
    ])
    return result.returncode == 0


async def _biliup_check_login_async() -> bool:
    return await asyncio.to_thread(_biliup_check_login)


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
    if _is_biliup_rate_limited(output, result.returncode):
        logger.warning("biliup 上传命中频率限制 (code 21540)")
    success = _biliup_create_submit_succeeded(output, result.returncode)
    return success, _extract_biliup_bvid(output)


def _get_upload_semaphore() -> asyncio.Semaphore:
    global _upload_semaphore
    if _upload_semaphore is None:
        max_concurrent = int(upload_global_config.get("max_concurrent", 1))
        if max_concurrent < 1:
            max_concurrent = 1
        _upload_semaphore = asyncio.Semaphore(max_concurrent)
        logger.info(f"上传并发控制信号量已创建，最大并发数: {max_concurrent}")
    return _upload_semaphore


async def _biliup_upload_video_entry_async(**kwargs) -> tuple[bool, Optional[str]]:
    async with _get_upload_semaphore():
        return await asyncio.to_thread(_biliup_upload_video_entry, **kwargs)


def _biliup_append_video_entry_with_status(
    *,
    video_path: str,
    bvid: str,
    part_title: Optional[str] = None,
) -> tuple[bool, bool]:
    runtime = _get_biliup_runtime()
    if part_title:
        logger.info("biliup append 当前版本未提供分P标题参数，将使用文件名作为分P标题")

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
    rate_limited = _is_biliup_rate_limited(output, result.returncode)
    if rate_limited:
        logger.warning("biliup 追加分P命中频率限制 (code 21540)")
    return _biliup_append_submit_succeeded(output, result.returncode), rate_limited


async def _biliup_append_video_entry_with_status_async(**kwargs) -> tuple[bool, bool]:
    async with _get_upload_semaphore():
        return await asyncio.to_thread(_biliup_append_video_entry_with_status, **kwargs)


def _biliup_append_video_entry(*, video_path: str, bvid: str, part_title: Optional[str] = None) -> bool:
    success, _ = _biliup_append_video_entry_with_status(
        video_path=video_path,
        bvid=bvid,
        part_title=part_title,
    )
    return success

# 从文件名解析时间戳的函数
def get_timestamp_from_filename(filepath):
    """从文件名解析时间戳，适配 '银剑君录播YYYY-MM-DDTHH_mm_ss.mp4' 格式"""
    filename = os.path.basename(filepath)
    try:
        # 适配 '银剑君录播YYYY-MM-DDTHH_mm_ss.mp4' 格式
        timestamp_str = filename.split('录播')[-1].split('.')[0].replace('T', ' ')
        return datetime.strptime(timestamp_str, '%Y-%m-%d %H_%M_%S')
    except (IndexError, ValueError) as e:
        logger.warning(f"无法从文件名 {filename} 解析时间戳: {e}，将使用当前时间。")
        return datetime.now()

def _reset_yaml_globals():
    """Reset all YAML-derived globals to empty state."""
    global yaml_config
    yaml_config = {}
    streamer_configs.clear()
    upload_global_config.clear()


def load_yaml_config():
    """加载 config.yaml 文件，解析按主播分组的配置结构并验证必要键"""
    global yaml_config, streamer_configs, upload_global_config
    try:
        with open(config.YAML_CONFIG_PATH, 'r', encoding='utf-8') as f:
            yaml_config = yaml.safe_load(f)
            if not isinstance(yaml_config, dict):
                logger.error(f"读取 {config.YAML_CONFIG_PATH} 失败: 文件内容不是有效的 YAML 字典格式。")
                _reset_yaml_globals()
                return False
            logger.info(f"成功加载配置文件: {config.YAML_CONFIG_PATH}")

            # --- 解析 streamers 配置 ---
            streamers_raw = yaml_config.get('streamers')
            if not isinstance(streamers_raw, dict) or not streamers_raw:
                logger.error(f"配置文件 {config.YAML_CONFIG_PATH} 中缺少 'streamers' 或格式错误。")
                _reset_yaml_globals()
                return False

            required_upload_keys = ['title', 'tid', 'tag', 'desc', 'source']
            parsed_configs = {}
            streamers_list = []
            valid = True

            for streamer_name, streamer_data in streamers_raw.items():
                if not isinstance(streamer_data, dict):
                    logger.error(f"主播 '{streamer_name}' 的配置格式错误，应为字典。")
                    valid = False
                    continue

                room_id = streamer_data.get('room_id')
                if not room_id:
                    logger.error(f"主播 '{streamer_name}' 缺少 'room_id'。")
                    valid = False
                    continue

                upload_data = streamer_data.get('upload', {})
                if not isinstance(upload_data, dict):
                    logger.error(f"主播 '{streamer_name}' 的 'upload' 配置格式错误。")
                    valid = False
                    continue

                missing_keys = [k for k in required_upload_keys if k not in upload_data]
                if missing_keys:
                    logger.error(
                        f"主播 '{streamer_name}' 的 upload 配置缺少以下必要字段: {', '.join(missing_keys)}"
                    )
                    valid = False
                    continue

                title = upload_data.get('title', '')
                if '{time}' not in title:
                    logger.warning(
                        f"主播 '{streamer_name}' 的 'title' ('{title}') 不包含 '{{time}}' 占位符。将使用固定标题。"
                    )

                # 为上传元数据设置默认值
                upload_data.setdefault('cover', '')
                upload_data.setdefault('dynamic', '')

                parsed_configs[streamer_name] = upload_data
                streamers_list.append({"name": streamer_name, "room_id": str(room_id)})

            if not valid:
                _reset_yaml_globals()
                return False

            streamer_configs.clear()
            streamer_configs.update(parsed_configs)

            # 更新 config.STREAMERS 以便录制服务和状态监控使用
            config.STREAMERS = streamers_list

            # --- 解析全局上传配置 ---
            global_upload = yaml_config.get('upload', {})
            upload_global_config.clear()
            if isinstance(global_upload, dict):
                upload_global_config.update(global_upload)

            logger.info(f"已加载 {len(streamer_configs)} 个主播配置: {list(streamer_configs.keys())}")
            return True

    except FileNotFoundError:
        logger.error(f"配置文件 {config.YAML_CONFIG_PATH} 未找到。请确保该文件存在。")
        _reset_yaml_globals()
        return False
    except yaml.YAMLError as e:
        logger.error(f"解析配置文件 {config.YAML_CONFIG_PATH} 时出错: {e}")
        _reset_yaml_globals()
        return False
    except Exception as e:
        logger.error(f"加载配置文件时发生未知错误: {e}")
        _reset_yaml_globals()
        return False

async def upload_to_bilibili(db: AsyncSession):
    """上传 UPLOAD_FOLDER 中的视频文件到 Bilibili（按主播分组处理）。

    遍历所有已配置主播，对每个主播：
    1. 按文件名前缀匹配属于该主播的待上传文件
    2. 查询该主播的直播场次进行分组
    3. 使用该主播独立的上传元数据创建/追加 B 站投稿
    """
    await cleanup_delayed_uploaded_files(db)

    global yaml_config, streamer_configs
    if not yaml_config or not streamer_configs:
        logger.error("Bilibili 上传配置 (config.yaml) 未成功加载，跳过上传步骤。")
        return

    is_skip_encoding = config.SKIP_VIDEO_ENCODING
    if is_skip_encoding:
        logger.info("检测到 SKIP_VIDEO_ENCODING=True 配置，将寻找并上传 FLV 文件")
        video_extension = "flv"
    else:
        logger.info("将寻找并上传压制后的 MP4 文件")
        video_extension = "mp4"
    danmaku_tag = config.NO_DANMAKU_TITLE_SUFFIX if is_skip_encoding else config.DANMAKU_TITLE_SUFFIX

    logger.info(f"开始检查并上传视频到 Bilibili (文件类型: {video_extension})...")
    total_uploaded = 0
    total_errors = 0
    uploader_backend = _detect_uploader_backend()
    logger.info(f"B站上传后端: {uploader_backend}")
    rate_limit_cooldown_seconds = max(0, int(getattr(config, "BILIUP_RATE_LIMIT_COOLDOWN_SECONDS", 300)))
    append_rate_limit_max_retries = max(0, int(getattr(config, "BILIUP_RATE_LIMIT_APPEND_MAX_RETRIES", 1)))

    # 1. 检查登录状态
    upload_controller = None
    feed_controller = None
    try:
        if uploader_backend == "biliup_cli":
            if not await _biliup_check_login_async():
                logger.error("biliup 登录验证失败，请检查 cookies.json 文件是否有效。")
                return
            logger.info("biliup 登录验证成功。")
        else:
            if LoginController is None:
                logger.error("未安装 bilitool，且当前上传后端配置为 bilitool")
                return
            login_controller = LoginController()
            if not login_controller.check_bilibili_login():
                logger.error("Bilibili 登录验证失败，请检查 cookies.json 文件是否有效或已生成。")
                return
            logger.info("Bilibili 登录验证成功。")
    except Exception as e:
        logger.error(f"检查 Bilibili 登录状态时出错: {e}")
        return

    if uploader_backend != "biliup_cli" and not config.API_ENABLED:
        logger.error("API 功能未配置或明确禁用，无法执行上传")
        return

    if uploader_backend == "bilitool":
        upload_controller = UploadController()
        feed_controller = FeedController()

    # 2. 获取所有待上传的视频文件
    all_video_files = glob.glob(os.path.join(config.UPLOAD_FOLDER, f"*.{video_extension}"))
    if not all_video_files:
        logger.info(f"在上传目录中没有找到 {video_extension.upper()} 文件，无需上传。")
        return

    logger.info(f"上传目录中共找到 {len(all_video_files)} 个 {video_extension.upper()} 文件")

    # 3. 按主播分组文件
    streamer_names = list(streamer_configs.keys())
    files_by_streamer: dict[str, list[str]] = {name: [] for name in streamer_names}
    unmatched_files = []

    for file_path in all_video_files:
        file_name = os.path.basename(file_path)
        matched = False
        for name in streamer_names:
            if file_name.startswith(f"{name}录播"):
                files_by_streamer[name].append(file_path)
                matched = True
                break
        if not matched:
            logger.warning(f"文件 {file_name} 不匹配任何已配置主播，跳过")
            unmatched_files.append(file_path)

    # 4. 遍历每个主播执行上传
    abort_due_to_rate_limit = False
    for streamer_name, video_files in files_by_streamer.items():
        if not video_files:
            continue

        streamer_upload_config = streamer_configs[streamer_name]
        logger.info(f"=== 开始处理主播 [{streamer_name}] 的 {len(video_files)} 个文件 ===")

        try:
            video_files.sort(key=get_timestamp_from_filename)
        except Exception as e:
            logger.error(f"根据时间戳排序文件时出错: {e}，将按默认顺序处理。")

        # 筛选未上传的文件
        video_info_list = []
        for file_path in video_files:
            file_name = os.path.basename(file_path)
            timestamp = get_timestamp_from_filename(file_path)
            try:
                query = select(UploadedVideo).filter(UploadedVideo.first_part_filename == file_name)
                result = await db.execute(query)
                if result.scalars().first():
                    logger.info(f"文件 {file_name} 已有上传记录，跳过")
                else:
                    video_info_list.append({'path': file_path, 'filename': file_name, 'timestamp': timestamp})
            except Exception as e:
                logger.error(f"检查文件 {file_name} 是否已上传时出错: {e}")

        if not video_info_list:
            logger.info(f"主播 [{streamer_name}] 没有未上传的视频文件")
            continue

        logger.info(f"主播 [{streamer_name}] 待上传 {len(video_info_list)} 个文件")

        # 获取该主播的直播场次
        try:
            complete_sessions_query = select(StreamSession).filter(
                StreamSession.streamer_name == streamer_name,
                StreamSession.start_time.is_not(None),
                StreamSession.end_time.is_not(None),
                StreamSession.end_time > datetime.now() - timedelta(days=3)
            ).order_by(StreamSession.start_time)
            complete_sessions_result = await db.execute(complete_sessions_query)
            complete_sessions = complete_sessions_result.scalars().all()

            current_session_query = select(StreamSession).filter(
                StreamSession.streamer_name == streamer_name,
                StreamSession.start_time.is_not(None),
                StreamSession.end_time.is_(None)
            ).order_by(desc(StreamSession.start_time)).limit(1)
            current_session_result = await db.execute(current_session_query)
            current_session = current_session_result.scalars().first()

            all_sessions = list(complete_sessions)
            if current_session:
                all_sessions.append(current_session)
                logger.info(f"主播 [{streamer_name}] 发现当前正在进行的直播，开始于: {current_session.start_time}")

            if not all_sessions:
                logger.warning(f"主播 [{streamer_name}] 没有可用的直播场次记录，无法划分直播场次")
                continue

            logger.info(f"主播 [{streamer_name}] 共获取到 {len(all_sessions)} 条直播场次记录")
        except Exception as e:
            logger.error(f"获取主播 [{streamer_name}] 直播场次信息时出错: {e}")
            continue

        # 将视频分配到场次
        session_time_buffer = timedelta(minutes=config.STREAM_START_TIME_ADJUSTMENT)
        session_ranges = []
        for session in all_sessions:
            end_time = session.end_time if session.end_time else datetime.now()
            session_ranges.append({
                'start_time': session.start_time - session_time_buffer,
                'end_time': end_time + session_time_buffer,
                'session_id': session.id,
                'is_current': session.end_time is None
            })

        session_videos = {}
        unassigned_videos = []

        for video_info in video_info_list:
            video_time = video_info['timestamp']
            assigned = False
            for session_range in session_ranges:
                if session_range['start_time'] <= video_time <= session_range['end_time']:
                    session_id = session_range['session_id']
                    if session_id not in session_videos:
                        session_videos[session_id] = {'videos': [], 'is_current': session_range['is_current']}
                    session_videos[session_id]['videos'].append(video_info)
                    assigned = True
                    break
            if not assigned:
                logger.warning(f"无法确定视频 {video_info['filename']} 所属的直播场次，将保存到未分配列表")
                unassigned_videos.append(video_info)

        if not session_videos and not unassigned_videos:
            logger.info(f"主播 [{streamer_name}] 没有视频能够匹配到任何直播场次")
            continue

        logger.info(f"主播 [{streamer_name}] 视频已分组到 {len(session_videos)} 个直播场次，另有 {len(unassigned_videos)} 个视频无法分配")

        # 处理每个场次的上传
        for session_id, session_data in session_videos.items():
            videos = session_data['videos']
            is_current_session = session_data['is_current']
            if not videos:
                continue

            videos.sort(key=lambda x: x['timestamp'])

            session_query = select(StreamSession).filter(StreamSession.id == session_id)
            session_result = await db.execute(session_query)
            session = session_result.scalars().first()

            logger.info(f"主播 [{streamer_name}] 开始处理直播场次 ID:{session_id} 的 {len(videos)} 个视频")

            period_start = session.start_time - session_time_buffer
            period_end = (session.end_time or datetime.now()) + session_time_buffer

            # 查询该主播该场次的已有 BVID
            query = select(UploadedVideo).filter(
                UploadedVideo.streamer_name == streamer_name,
                UploadedVideo.upload_time.between(period_start, period_end),
                UploadedVideo.bvid.is_not(None)
            ).order_by(desc(UploadedVideo.upload_time)).limit(1)
            result = await db.execute(query)
            existing_record = result.scalars().first()

            existing_bvid = None
            if existing_record:
                existing_bvid = existing_record.bvid
                logger.info(f"该直播场次已有上传记录，BVID: {existing_bvid}")

            if not existing_bvid:
                # 兼容旧记录（streamer_name 为 NULL），按时间范围回退查询
                fallback_query = select(UploadedVideo).filter(
                    UploadedVideo.streamer_name.is_(None),
                    UploadedVideo.upload_time.between(period_start, period_end),
                    UploadedVideo.bvid.is_not(None)
                ).order_by(desc(UploadedVideo.upload_time)).limit(1)
                fallback_result = await db.execute(fallback_query)
                fallback_record = fallback_result.scalars().first()
                if fallback_record:
                    existing_bvid = fallback_record.bvid
                    logger.info(f"从旧记录中找到 BVID: {existing_bvid}")

            if not existing_bvid:
                pending_query = select(UploadedVideo).filter(
                    UploadedVideo.upload_time.between(period_start, period_end),
                    UploadedVideo.bvid.is_(None),
                    or_(UploadedVideo.streamer_name == streamer_name, UploadedVideo.streamer_name.is_(None)),
                ).order_by(desc(UploadedVideo.upload_time)).limit(1)
                pending_result = await db.execute(pending_query)
                if pending_result.scalars().first():
                    logger.info(
                        f"直播场次 ID:{session_id} 已存在待回填BVID的上传记录，"
                        "本次跳过创建新稿件，等待BVID回填后再追加分P"
                    )
                    continue

            if existing_bvid:
                # --- 追加分P ---
                bvid = existing_bvid
                logger.info(f"将以分P形式追加视频到 BVID: {bvid}")
                try:
                    count_query = select(func.count()).select_from(UploadedVideo).filter(
                        UploadedVideo.upload_time.between(period_start, period_end)
                    )
                    count_result = await db.execute(count_query)
                    start_part_number = count_result.scalar_one() + 1

                    cdn = streamer_upload_config.get('cdn')
                    part_number = start_part_number
                    for video_info in videos:
                        file_path = video_info['path']
                        file_name = video_info['filename']

                        recheck_query = select(UploadedVideo).filter(UploadedVideo.first_part_filename == file_name)
                        recheck_result = await db.execute(recheck_query)
                        if recheck_result.scalars().first():
                            logger.info(f"二次检查: 文件 {file_name} 已上传，跳过")
                            continue

                        try:
                            video_time = video_info['timestamp']
                            part_time_str = video_time.strftime('%H:%M:%S')
                            part_title = f"P{part_number} {part_time_str}"
                        except Exception:
                            part_title = f"P{part_number}"

                        logger.info(f"准备追加分P ({part_title}): {file_name}")

                        append_rate_limited = False
                        append_retry_count = 0
                        while True:
                            if uploader_backend == "biliup_cli":
                                append_success, append_rate_limited = await _biliup_append_video_entry_with_status_async(
                                    video_path=file_path, bvid=bvid, part_title=part_title,
                                )
                            else:
                                append_rate_limited = False
                                append_success = upload_controller.append_video_entry(
                                    video_path=file_path, bvid=bvid, cdn=cdn, video_name=part_title,
                                )

                            if append_success:
                                break
                            if append_rate_limited and append_retry_count < append_rate_limit_max_retries:
                                append_retry_count += 1
                                logger.warning(
                                    f"追加分P命中频率限制(code 21540)，将在 {rate_limit_cooldown_seconds} 秒后重试 "
                                    f"(第 {append_retry_count}/{append_rate_limit_max_retries} 次): {file_name}"
                                )
                                if rate_limit_cooldown_seconds > 0:
                                    await asyncio.sleep(rate_limit_cooldown_seconds)
                                continue
                            break

                        if append_success:
                            logger.info(f"成功追加分P: {file_name}")
                            total_uploaded += 1
                            try:
                                new_upload = UploadedVideo(
                                    bvid=None, title=f"{part_title} (分P)",
                                    first_part_filename=file_name,
                                    upload_time=video_info['timestamp'],
                                    streamer_name=streamer_name,
                                )
                                db.add(new_upload)
                                await db.commit()
                                _handle_uploaded_file_after_success(file_path, file_name)
                            except Exception as db_e:
                                logger.error(f"将视频分P信息记录到数据库时出错: {db_e}")
                                await db.rollback()
                        else:
                            logger.error(f"追加分P失败: {file_name}")
                            total_errors += 1
                            if append_rate_limited:
                                logger.warning("命中B站频率限制且冷却重试已耗尽，本轮上传提前结束")
                                abort_due_to_rate_limit = True
                                break

                        part_number += 1

                    if abort_due_to_rate_limit:
                        break
                except Exception as e:
                    logger.error(f"处理直播场次 ID:{session_id} 的追加分P时出错: {e}")
                    continue
            else:
                # --- 创建新稿件 ---
                logger.info(f"该直播场次尚未上传视频，将创建新稿件")
                first_video_info = videos[0]
                first_video_path = first_video_info['path']
                first_video_filename = first_video_info['filename']

                try:
                    tid = streamer_upload_config['tid']
                    tag = streamer_upload_config['tag']
                    source = streamer_upload_config['source']
                    cover = streamer_upload_config.get('cover', '')
                    dynamic = streamer_upload_config.get('dynamic', '')
                    video_desc = streamer_upload_config['desc']
                    title_template = streamer_upload_config['title']
                    cdn = streamer_upload_config.get('cdn')
                except KeyError as e:
                    logger.error(f"主播 [{streamer_name}] 缺少必要的上传参数: {e}")
                    continue

                title = title_template
                try:
                    video_time = first_video_info['timestamp']
                    formatted_time = video_time.strftime('%Y年%m月%d日')
                    if '{time}' in title_template:
                        title = title_template.replace('{time}', formatted_time)
                    elif len(videos) > 1:
                        title = f"{title_template} (合集 {video_time.strftime('%Y-%m-%d')})"
                except Exception as e:
                    logger.warning(f"生成标题时出错: {e}，使用默认标题: {title}")
                title = title.replace('{danmaku_tag}', danmaku_tag)

                logger.info(f"上传首个视频，创建稿件。标题: {title}")

                acquired_bvid = None
                if uploader_backend == "biliup_cli":
                    try:
                        upload_result, acquired_bvid = await _biliup_upload_video_entry_async(
                            video_path=first_video_path, tid=tid, title=title, copyright=2,
                            desc=video_desc, tag=tag, source=source, cover=cover, dynamic=dynamic,
                        )
                    except Exception as cli_e:
                        logger.error(f"调用 biliup 上传失败: {cli_e}")
                        upload_result = False
                else:
                    upload_result = upload_controller.upload_video_entry(
                        video_path=first_video_path, yaml=None, tid=tid, title=title,
                        copyright=2, desc=video_desc, tag=tag, source=source,
                        cover=cover, dynamic=dynamic, cdn=cdn,
                    )

                if upload_result:
                    logger.info(f"成功上传首个视频: {first_video_filename}")
                    total_uploaded += 1
                    try:
                        new_upload = UploadedVideo(
                            bvid=acquired_bvid, title=title,
                            first_part_filename=first_video_filename,
                            upload_time=first_video_info['timestamp'],
                            streamer_name=streamer_name,
                        )
                        db.add(new_upload)
                        await db.commit()
                        await db.refresh(new_upload)
                        record_id = new_upload.id
                        logger.info(f"已将视频信息记录到数据库 (ID: {record_id}, 标题: {title}, BVID: {acquired_bvid or '暂无'})")
                        _handle_uploaded_file_after_success(first_video_path, first_video_filename)

                        if uploader_backend == "bilitool":
                            logger.info("上传成功，等待15秒后尝试获取BVID...")
                            await asyncio.sleep(15)
                            acquired_bvid = None
                            for attempt in range(3):
                                try:
                                    video_list_data = feed_controller.get_video_dict_info(size=20, status_type="pubed,is_pubing")
                                    if video_list_data and isinstance(video_list_data, dict):
                                        for video_title, video_bvid in video_list_data.items():
                                            if video_title == title and isinstance(video_bvid, str) and video_bvid.startswith('BV'):
                                                acquired_bvid = video_bvid
                                                break
                                    if acquired_bvid:
                                        new_upload.bvid = acquired_bvid
                                        await db.commit()
                                        logger.info(f"已更新BVID为 {acquired_bvid}")
                                        break
                                    else:
                                        logger.warning(f"第 {attempt+1} 次尝试未获取到BVID，5秒后重试...")
                                        await asyncio.sleep(5)
                                except Exception as api_e:
                                    logger.error(f"获取BVID时出错: {api_e}")
                                    await asyncio.sleep(5)
                            if not acquired_bvid:
                                logger.warning("无法获取BVID，等待下次运行")
                                continue
                            if len(videos) > 1:
                                logger.info(f"已获取BVID: {acquired_bvid}，将在下次运行时追加剩余 {len(videos)-1} 个分P")
                        else:
                            if acquired_bvid:
                                logger.info(f"biliup 已直接返回 BVID: {acquired_bvid}")
                                if len(videos) > 1:
                                    logger.info(f"下次运行时将继续追加剩余 {len(videos)-1} 个分P")
                            else:
                                logger.warning("biliup 上传成功但未解析到BVID，请稍后人工确认")
                    except Exception as db_e:
                        logger.error(f"将视频信息记录到数据库或获取BVID时出错: {db_e}")
                        await db.rollback()
                else:
                    logger.error(f"上传首个视频失败: {first_video_filename}")
                    total_errors += 1

            if abort_due_to_rate_limit:
                break

        if abort_due_to_rate_limit:
            break

    file_type = "FLV" if is_skip_encoding else "MP4"
    if abort_due_to_rate_limit:
        logger.info(f"Bilibili {file_type} 视频上传提前结束（触发频率限制）。成功: {total_uploaded}，失败: {total_errors}")
    else:
        logger.info(f"Bilibili {file_type} 视频上传完成。成功: {total_uploaded}，失败: {total_errors}")


async def update_video_bvids(db: AsyncSession):
    """检查并更新数据库中缺失BVID的视频记录 (直接操作数据库)"""
    logger.info("开始检查和更新缺失BVID的视频记录...")
    if _detect_uploader_backend() == "biliup_cli":
        logger.info("当前使用 biliup CLI 上传后端（创建稿件时通常可直接拿到BVID），跳过旧 API 回填任务")
        return
    
    try:
        # 1. 检查登录状态，确保能调用B站API
        if LoginController is None or FeedController is None:
            logger.error("未安装 bilitool，无法执行旧 API 的 BVID 回填")
            return
        login_controller = LoginController()
        if not login_controller.check_bilibili_login():
            logger.error("Bilibili 登录验证失败，无法更新BVID信息")
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
                logger.info("没有找到需要更新BVID的视频记录")
                return
                
            logger.info(f"找到 {len(no_bvid_records)} 条缺失BVID的记录，尝试更新...")
        except Exception as db_e:
            logger.error(f"从数据库获取缺失BVID记录时出错: {db_e}")
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
                logger.warning("未从B站API获取到任何视频信息")
                return
                
            logger.info(f"从B站API获取到 {len(all_videos)} 条视频信息")
            
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
                            logger.warning(f"尝试更新 BVID {found_bvid} 失败，因为它已被记录 ID:{bvid_exists.id} 使用")
                            continue # 跳过此记录

                        # 更新记录
                        record.bvid = found_bvid
                        await db.commit()
                        await db.refresh(record)
                        logger.info(f"成功更新记录 ID:{record_id}, 标题:'{record_title}' 的BVID为 {found_bvid}")
                        updated_count += 1
                    except Exception as update_e:
                         logger.error(f"更新记录 ID:{record_id} 的BVID ({found_bvid}) 时数据库出错: {update_e}")
                         await db.rollback() # 出错时回滚
            
            logger.info(f"BVID更新完成，共更新了 {updated_count}/{len(no_bvid_records)} 条记录")
            
        except Exception as e:
            logger.error(f"调用B站API获取视频列表或更新BVID时出错: {e}")
    
    except Exception as e:
        logger.error(f"更新视频BVID过程中发生错误: {e}")
