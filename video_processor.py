import os
import glob
import subprocess
import json
import time
import logging
import shlex
import schedule
import shutil
import yaml
from datetime import datetime

# 从同一目录导入配置和 Bilibili 工具
import config
from dmconvert import convert_xml_to_ass
from bilitool import LoginController, UploadController, FeedController # 假设需要这些

# 导入 API 客户端

# 导入数据库相关模块
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from models import UploadedVideo, StreamSession # 需要导入模型

# 配置日志记录
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 全局变量 --- 
yaml_config = {} # 用于存储从 config.yaml 读取的配置

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

def cleanup_small_files():
    """删除 PROCESSING_FOLDER 中小于 MIN_FILE_SIZE_MB 的 .flv 及其对应的 .xml 文件"""
    logging.info("开始清理小文件...")
    min_size_bytes = config.MIN_FILE_SIZE_MB * 1024 * 1024
    files_deleted = 0
    
    flv_files = glob.glob(os.path.join(config.PROCESSING_FOLDER, "*.flv"))

    for flv_file in flv_files:
        try:
            file_size = os.path.getsize(flv_file)
            if file_size < min_size_bytes:
                base_name = os.path.splitext(flv_file)[0]
                xml_file = base_name + ".xml"

                logging.info(f"找到小于 {config.MIN_FILE_SIZE_MB}MB 的文件: {os.path.basename(flv_file)} ({file_size / (1024*1024):.2f}MB)")

                # 1. 删除 flv 文件
                try:
                    os.remove(flv_file)
                    logging.info(f"已删除: {os.path.basename(flv_file)}")
                    files_deleted += 1

                    # 2. 删除对应的 xml 文件 (如果存在)
                    if os.path.exists(xml_file):
                        try:
                            os.remove(xml_file)
                            logging.info(f"已删除对应的 XML: {os.path.basename(xml_file)}")
                        except OSError as e:
                            logging.error(f"删除 XML 文件 {os.path.basename(xml_file)} 失败: {e}")
                    else:
                        logging.warning(f"未找到 {os.path.basename(flv_file)} 对应的 XML 文件: {os.path.basename(xml_file)}")

                except OSError as e:
                    logging.error(f"删除 FLV 文件 {os.path.basename(flv_file)} 失败: {e}")

        except FileNotFoundError:
            logging.warning(f"检查文件大小时未找到文件: {flv_file} (可能已被其他进程处理)")
        except Exception as e:
            logging.error(f"处理文件 {os.path.basename(flv_file)} 时出错: {e}")

    logging.info(f"小文件清理完成，共删除 {files_deleted} 个 FLV 文件及其对应 XML。")

def get_video_resolution(video_file):
    """使用 ffprobe 获取视频分辨率"""
    cmd = [
        config.FFPROBE_PATH,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        video_file
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        if data and 'streams' in data and data['streams']:
            width = data['streams'][0].get('width')
            height = data['streams'][0].get('height')
            if width and height:
                logging.info(f"获取到视频 {os.path.basename(video_file)} 分辨率: {width}x{height}")
                return int(width), int(height)
        logging.error(f"无法从 ffprobe 输出中解析分辨率: {video_file}")
        return None, None
    except FileNotFoundError:
        logging.error(f"找不到 ffprobe 命令，请检查 config.py 中的 FFPROBE_PATH 设置或确保 ffprobe 在系统 PATH 中。")
        return None, None
    except subprocess.CalledProcessError as e:
        logging.error(f"运行 ffprobe 时出错 (文件: {os.path.basename(video_file)}): {e}")
        logging.error(f"ffprobe stderr: {e.stderr}")
        return None, None
    except json.JSONDecodeError as e:
        logging.error(f"解析 ffprobe JSON 输出时出错 (文件: {os.path.basename(video_file)}): {e}")
        logging.error(f"ffprobe stdout: {result.stdout}")
        return None, None
    except Exception as e:
         logging.error(f"获取视频分辨率时发生未知错误 (文件: {os.path.basename(video_file)}): {e}")
         return None, None


def convert_danmaku():
    """转换 PROCESSING_FOLDER 中的 XML 文件为 ASS 文件，跳过正在录制的视频"""
    logging.info("开始转换 XML 弹幕文件为 ASS...")
    converted_count = 0
    skipped_count = 0
    error_count = 0

    xml_files = glob.glob(os.path.join(config.PROCESSING_FOLDER, "*.xml"))

    for xml_file in xml_files:
        base_name = os.path.splitext(xml_file)[0]
        flv_file = base_name + ".flv"
        flv_part_file = flv_file + ".part"
        ass_file = base_name + ".ass"

        # 检查是否存在对应的 .flv.part 文件，如果存在则跳过
        if os.path.exists(flv_part_file):
            logging.info(f"跳过转换，因为找到正在录制的文件: {os.path.basename(flv_part_file)}")
            skipped_count += 1
            continue

        # 检查是否存在对应的 .flv 文件
        if not os.path.exists(flv_file):
            logging.warning(f"跳过转换，因为找不到对应的 FLV 文件: {os.path.basename(flv_file)} (XML: {os.path.basename(xml_file)})")
            skipped_count += 1
            continue

        # 检查是否已存在 ass 文件
        if os.path.exists(ass_file):
             logging.info(f"ASS 文件已存在，跳过转换: {os.path.basename(ass_file)}")
             skipped_count +=1
             continue

        # 获取视频分辨率
        resolution_x, resolution_y = get_video_resolution(flv_file)
        if resolution_x is None or resolution_y is None:
            logging.error(f"无法获取视频分辨率，跳过转换: {os.path.basename(flv_file)}")
            error_count += 1
            continue

        # 执行转换
        try:
            logging.info(f"正在转换: {os.path.basename(xml_file)} -> {os.path.basename(ass_file)}")
            convert_xml_to_ass(
                font_size=config.FONT_SIZE,
                sc_font_size=config.SC_FONT_SIZE,
                resolution_x=resolution_x,
                resolution_y=resolution_y,
                xml_file=xml_file,
                ass_file=ass_file
            )
            # 假设 convert_xml_to_ass 成功时不抛出异常
            if os.path.exists(ass_file):
                 logging.info(f"成功转换: {os.path.basename(ass_file)}")
                 
                 # 删除XML文件 (根据配置决定是否删除)
                 if config.DELETE_UPLOADED_FILES:
                     try:
                         os.remove(xml_file)
                         logging.info(f"已删除原始 XML 文件: {os.path.basename(xml_file)} (根据配置)")
                     except OSError as e:
                         logging.warning(f"删除 XML 文件 {os.path.basename(xml_file)} 失败: {e}")
                 else:
                     logging.info(f"保留原始 XML 文件: {os.path.basename(xml_file)} (根据配置)")
                 
                 converted_count += 1
            else:
                 logging.error(f"转换函数执行完毕但未找到输出文件: {os.path.basename(ass_file)}")
                 error_count += 1

        except Exception as e:
            logging.error(f"转换 XML 文件 {os.path.basename(xml_file)} 时出错: {e}")
            error_count += 1

    logging.info(f"弹幕转换完成。成功: {converted_count}, 跳过: {skipped_count}, 失败: {error_count}")


def encode_video():
    """压制带有 ASS 弹幕的 FLV 视频为 MP4"""
    logging.info("开始压制视频...")
    encoded_count = 0
    skipped_count = 0
    error_count = 0

    ass_files = glob.glob(os.path.join(config.PROCESSING_FOLDER, "*.ass"))

    for ass_file in ass_files:
        base_name = os.path.splitext(ass_file)[0]
        flv_file = base_name + ".flv"
        # 先定义临时输出路径和最终上传路径
        temp_mp4_file = base_name + ".mp4" # 输出到 processing 文件夹
        upload_mp4_file = os.path.join(config.UPLOAD_FOLDER, os.path.basename(temp_mp4_file)) # 最终移动到 upload 文件夹

        # 检查 FLV 文件是否存在
        if not os.path.exists(flv_file):
            logging.warning(f"找不到对应的 FLV 文件，跳过压制: {os.path.basename(flv_file)} (ASS: {os.path.basename(ass_file)})")
            skipped_count += 1
            continue

        # 检查最终 MP4 文件是否已存在于上传目录
        if os.path.exists(upload_mp4_file):
            logging.info(f"MP4 文件已存在于上传目录，跳过压制: {os.path.basename(upload_mp4_file)}")
            # 如果最终文件存在，也考虑删除 processing 文件夹中的 ass 和 flv
            try:
                if os.path.exists(ass_file):
                    os.remove(ass_file)
                    logging.info(f"已删除已处理的 ASS: {os.path.basename(ass_file)}")
                if os.path.exists(flv_file):
                    os.remove(flv_file)
                    logging.info(f"已删除已处理的 FLV: {os.path.basename(flv_file)}")
            except OSError as e:
                logging.warning(f"删除已存在于上传目录的视频对应的原始文件时出错: {e}")
            skipped_count += 1
            continue
        
        # 如果临时 MP4 文件存在 (可能是上次压制中断)，先删除
        if os.path.exists(temp_mp4_file):
            logging.warning(f"发现上次残留的临时 MP4 文件，将删除: {os.path.basename(temp_mp4_file)}")
            try:
                os.remove(temp_mp4_file)
            except OSError as e:
                logging.error(f"删除残留的临时 MP4 文件失败: {e}, 跳过此文件压制。")
                error_count += 1
                continue


        # 构建 FFmpeg 命令 (使用 QSV 加速)
        # 注意：shlex.quote 用于安全地处理可能包含特殊字符的文件名
        # 输出到 temp_mp4_file
        cmd_str = (
            f'{config.FFMPEG_PATH} -v verbose '
            f'-init_hw_device qsv=hw '
            f'-hwaccel qsv '
            f'-hwaccel_output_format qsv '
            f'-i {shlex.quote(flv_file)} '
            f'-vf "ass={shlex.quote(ass_file)},hwupload=extra_hw_frames=64" '
            f'-c:v h264_qsv '
            f'-preset veryfast '
            f'-global_quality 25 ' # 数字越小质量越高，25 是一个不错的平衡点
            f'-c:a copy ' # 直接复制音频流，不重新编码
            f'-y {shlex.quote(temp_mp4_file)}' # 输出到临时文件
        )

        logging.info(f"开始压制: {os.path.basename(flv_file)} + {os.path.basename(ass_file)} -> {os.path.basename(temp_mp4_file)}")
        logging.debug(f"执行 FFmpeg 命令: {cmd_str}")

        try:
            # 更安全的方式：将命令分割成列表
            cmd_list = shlex.split(cmd_str)
            process = subprocess.run(cmd_list, check=True, capture_output=True, text=True, encoding='utf-8')

            logging.info(f"成功压制到临时文件: {os.path.basename(temp_mp4_file)}")
            logging.debug(f"FFmpeg stdout:\n{process.stdout}")
            logging.debug(f"FFmpeg stderr:\n{process.stderr}")

            # 压制成功后，移动到上传目录
            try:
                logging.info(f"准备移动文件: {os.path.basename(temp_mp4_file)} -> {config.UPLOAD_FOLDER}")
                shutil.move(temp_mp4_file, upload_mp4_file)
                logging.info(f"成功移动文件到: {upload_mp4_file}")

                # 移动成功后，删除原始的 flv 和 ass 文件
                try:
                    if config.DELETE_UPLOADED_FILES:
                        os.remove(flv_file)
                        logging.info(f"已删除原始 FLV: {os.path.basename(flv_file)} (根据配置)")
                        
                        if os.path.exists(ass_file):
                            os.remove(ass_file)
                            logging.info(f"已删除原始 ASS: {os.path.basename(ass_file)} (根据配置)")
                    else:
                        logging.info(f"保留原始 FLV: {os.path.basename(flv_file)} (根据配置)")
                        
                        if os.path.exists(ass_file):
                            logging.info(f"保留原始 ASS: {os.path.basename(ass_file)} (根据配置)")
                    
                    encoded_count += 1 # 只有完全成功才计数
                except OSError as e:
                    logging.warning(f"移动文件成功，但删除原始文件时出错 ({os.path.basename(flv_file)} / {os.path.basename(ass_file)}): {e}")
                    # 即使删除失败，也算成功压制和移动了
                    encoded_count += 1

            except Exception as e: # 捕获移动过程中的所有异常
                logging.error(f"移动文件 {os.path.basename(temp_mp4_file)} 到上传目录失败: {e}")
                error_count += 1
                # 如果移动失败，尝试删除临时 MP4 文件，保留原始文件
                try:
                    if os.path.exists(temp_mp4_file):
                         os.remove(temp_mp4_file)
                         logging.info(f"已删除移动失败的临时 MP4 文件: {os.path.basename(temp_mp4_file)}")
                except OSError as del_e:
                     logging.warning(f"删除移动失败的临时 MP4 文件时也出错: {del_e}")


        except FileNotFoundError:
             logging.error(f"找不到 ffmpeg 命令，请检查 config.py 中的 FFMPEG_PATH 设置或确保 ffmpeg 在系统 PATH 中。")
             error_count += 1
        except subprocess.CalledProcessError as e:
            logging.error(f"运行 ffmpeg 压制视频时出错 (文件: {os.path.basename(flv_file)}): {e}")
            logging.error(f"FFmpeg return code: {e.returncode}")
            logging.error(f"FFmpeg stdout:\n{e.stdout}")
            logging.error(f"FFmpeg stderr:\n{e.stderr}")
            error_count += 1
             # 如果压制失败，尝试删除可能产生的损坏的临时 MP4 文件
            if os.path.exists(temp_mp4_file):
                 try:
                      os.remove(temp_mp4_file)
                      logging.info(f"已删除压制失败产生的临时 MP4: {os.path.basename(temp_mp4_file)}")
                 except OSError as del_e:
                      logging.warning(f"删除压制失败的临时 MP4 文件时出错: {del_e}")

        except Exception as e:
            logging.error(f"压制视频时发生未知错误 (文件: {os.path.basename(flv_file)}): {e}")
            error_count += 1
            # 同样尝试清理临时文件
            if os.path.exists(temp_mp4_file):
                 try:
                      os.remove(temp_mp4_file)
                      logging.info(f"已删除因未知错误产生的临时 MP4: {os.path.basename(temp_mp4_file)}")
                 except OSError as del_e:
                      logging.warning(f"删除因未知错误产生的临时 MP4 文件时出错: {del_e}")

    logging.info(f"视频压制与移动完成。成功: {encoded_count}, 跳过: {skipped_count}, 失败: {error_count}")


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

    logging.info("开始检查并上传视频到 Bilibili...")
    uploaded_count = 0
    error_count = 0
    
    # 1. 检查登录状态
    try:
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
    
    upload_controller = UploadController()
    feed_controller = FeedController()
    
    # 2. 获取所有待上传的视频文件并按时间戳排序
    mp4_files = glob.glob(os.path.join(config.UPLOAD_FOLDER, "*.mp4"))
    if not mp4_files:
        logging.info("在上传目录中没有找到 MP4 文件，无需上传。")
        return
    
    logging.info(f"上传目录中共找到 {len(mp4_files)} 个 MP4 文件")
    
    # 按文件名中的时间戳排序
    def get_timestamp_from_filename(filepath):
        filename = os.path.basename(filepath)
        try:
            # 适配 '银剑君录播YYYY-MM-DDTHH_mm_ss.mp4' 格式
            timestamp_str = filename.split('录播')[-1].split('.')[0].replace('T', ' ')
            return datetime.strptime(timestamp_str, '%Y-%m-%d %H_%M_%S')
        except (IndexError, ValueError) as e:
            logging.warning(f"无法从文件名 {filename} 解析时间戳: {e}，将影响排序。")
            return datetime.min
    
    try:
        # 对所有MP4文件按时间戳排序
        mp4_files.sort(key=get_timestamp_from_filename)
    except Exception as e:
        logging.error(f"根据时间戳排序文件时出错: {e}，将按默认顺序处理。")
    
    # 3. 筛选出已上传的文件，构建文件信息列表
    video_info_list = []
    already_uploaded_files = []
    
    for file_path in mp4_files:
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
    
    # 4. 获取所有直播场次信息
    try:
        streamer_name = config.DEFAULT_STREAMER_NAME
        sessions_query = select(StreamSession).filter(
            StreamSession.streamer_name == streamer_name
        ).order_by(StreamSession.end_time)
        
        sessions_result = await db.execute(sessions_query)
        all_sessions = sessions_result.scalars().all()
        
        if not all_sessions:
            logging.warning(f"主播 {streamer_name} 没有下播记录，无法划分直播场次")
            return
            
        logging.info(f"获取到 {len(all_sessions)} 条直播场次记录")
    except Exception as e:
        logging.error(f"获取直播场次信息时出错: {e}")
        return
    
    # 5. 根据直播场次将视频分组
    # 创建时间段映射: 每个场次的结束时间到下一个场次的结束时间
    session_ranges = []
    for i in range(len(all_sessions) - 1):
        current_session = all_sessions[i]
        next_session = all_sessions[i + 1]
        session_ranges.append({
            'start_time': current_session.end_time,
            'end_time': next_session.end_time,
            'session_id': next_session.id
        })
    
    # 添加最后一个场次到当前时间
    if all_sessions:
        last_session = all_sessions[-1]
        session_ranges.append({
            'start_time': last_session.end_time,
            'end_time': datetime.now(),
            'session_id': last_session.id
        })
    
    # 将视频分配到对应的时间段
    session_videos = {}
    for video_info in video_info_list:
        video_time = video_info['timestamp']
        assigned = False
        
        for session_range in session_ranges:
            if session_range['start_time'] < video_time <= session_range['end_time']:
                session_id = session_range['session_id']
                if session_id not in session_videos:
                    session_videos[session_id] = []
                
                session_videos[session_id].append(video_info)
                assigned = True
                break
        
        if not assigned:
            logging.warning(f"无法确定视频 {video_info['filename']} 所属的直播场次，将跳过上传")
    
    if not session_videos:
        logging.info("没有视频能够匹配到任何直播场次，结束上传流程")
        return
    
    logging.info(f"视频已分组到 {len(session_videos)} 个直播场次")
    
    # 6. 处理每个时间段的视频上传
    for session_id, videos in session_videos.items():
        if not videos:
            continue
        
        # 对该时间段内的视频按时间排序
        videos.sort(key=lambda x: x['timestamp'])
        session_start_time = min(v['timestamp'] for v in videos)
        formatted_date = session_start_time.strftime('%Y-%m-%d')
        
        logging.info(f"开始处理直播场次 ID:{session_id} ({formatted_date}) 的 {len(videos)} 个视频")
        
        # 查询该场次是否已有上传记录且有BVID
        try:
            # 找出该场次中第一个视频的文件名
            first_video_filename = videos[0]['filename']
            
            # 查询数据库中是否有该场次第一个视频的上传记录
            query = select(UploadedVideo).filter(
                UploadedVideo.first_part_filename == first_video_filename
            )
            result = await db.execute(query)
            existing_record = result.scalars().first()
            
            existing_bvid = None
            if existing_record and existing_record.bvid:
                existing_bvid = existing_record.bvid
                logging.info(f"该直播场次已有上传记录，BVID: {existing_bvid}")
        except Exception as e:
            logging.error(f"查询场次 ID:{session_id} 上传记录时出错: {e}")
            continue
        
        # 根据是否有BVID决定上传方式
        if existing_bvid:
            # --- 情况1: 追加分P ---
            bvid = existing_bvid
            logging.info(f"将以分P形式追加视频到 BVID: {bvid}")
            
            try:
                # 查询该BVID已有多少分P，确定起始P号
                # 这里简化处理，从数据库查询该BVID相关的文件数量
                count_query = select(UploadedVideo).filter(
                    UploadedVideo.bvid == bvid
                )
                count_result = await db.execute(count_query)
                existing_files = count_result.scalars().all()
                
                # 设置起始P号，如果无法确定就从P2开始
                start_part_number = len(existing_files) + 1 if existing_files else 2
                
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
                        part_title = f"P{part_number} {part_time_str}"
                    except Exception:
                        part_title = f"P{part_number}"
                    
                    logging.info(f"准备追加分P ({part_title}): {file_name}")
                    
                    # 调用追加接口
                    append_success = upload_controller.append_video_entry(
                        video_path=file_path,
                        bvid=bvid,
                        cdn=cdn
                    )
                    
                    if append_success:
                        logging.info(f"成功追加分P: {file_name}")
                        uploaded_count += 1
                        
                        # 记录到数据库
                        try:
                            new_upload = UploadedVideo(
                                bvid=bvid,
                                title=f"{part_title} (分P)",
                                first_part_filename=file_name
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
                formatted_time = video_time.strftime('%Y年%m月%d日 %H:%M')
                
                # 替换标题中的时间占位符
                if '{time}' in title_template:
                    title = title_template.replace('{time}', formatted_time)
                elif len(videos) > 1:  # 有多个文件，使用合集标题
                    title = f"{title_template} (合集 {video_time.strftime('%Y-%m-%d')})"
            except Exception as e:
                logging.warning(f"生成标题时出错: {e}，使用默认标题: {title}")
            
            logging.info(f"上传首个视频，创建稿件。标题: {title}")
            
            # 调用上传接口
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
                
                # 先将无BVID的记录存入数据库
                try:
                    new_upload = UploadedVideo(
                        bvid=None,  # 先设为None
                        title=title,
                        first_part_filename=first_video_filename
                    )
                    db.add(new_upload)
                    await db.commit()
                    await db.refresh(new_upload)
                    record_id = new_upload.id
                    logging.info(f"已将视频信息记录到数据库 (ID: {record_id}, 标题: {title}, 暂无BVID)")
                    
                    # 处理文件
                    if config.DELETE_UPLOADED_FILES:
                        try:
                            os.remove(first_video_path)
                            logging.info(f"已删除已上传的视频: {first_video_filename}")
                        except OSError as e:
                            logging.warning(f"删除已上传视频失败: {e}")
                    
                    # 等待获取BVID
                    logging.info("上传成功，等待15秒后尝试获取BVID...")
                    time.sleep(15)
                    
                    # 从B站API获取BVID
                    acquired_bvid = None
                    for attempt in range(3):  # 尝试最多3次
                        try:
                            # 调用B站API获取视频列表
                            video_list_data = feed_controller.get_video_dict_info(size=10, status_type='is_pubing')
                            
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
                                time.sleep(5)  # 等待5秒后重试
                                
                        except Exception as api_e:
                            logging.error(f"获取BVID时出错: {api_e}")
                            time.sleep(5)  # 出错后等待5秒再重试
                    
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
                
                except Exception as db_e:
                    logging.error(f"将视频信息记录到数据库或获取BVID时出错: {db_e}")
                    await db.rollback()
            else:
                logging.error(f"上传首个视频失败: {first_video_filename}")
                error_count += 1
    
    logging.info(f"Bilibili 视频上传完成。共处理 {len(video_info_list)} 个文件，成功上传: {uploaded_count}，失败: {error_count}")


async def update_video_bvids(db: AsyncSession):
    """检查并更新数据库中缺失BVID的视频记录 (直接操作数据库)"""
    logging.info("开始检查和更新缺失BVID的视频记录...")
    
    try:
        # 1. 检查登录状态，确保能调用B站API
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
