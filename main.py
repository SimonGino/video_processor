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
            # 读取删除设置，默认为 False (不删除)
            # yaml_config['delete_uploaded_files'] = yaml_config.get('delete_uploaded_files', False)
            # if yaml_config['delete_uploaded_files']:
            #      logging.info("配置了上传成功后删除本地 MP4 文件。")
            # else:
            #      logging.info("配置了上传成功后保留本地 MP4 文件。")
            # 删除逻辑移至 config.py

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
                    else:
                        logging.info(f"保留原始 FLV: {os.path.basename(flv_file)} (根据配置)")
                    if os.path.exists(ass_file):
                        os.remove(ass_file)
                        logging.info(f"已删除原始 ASS: {os.path.basename(ass_file)}")
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


def upload_to_bilibili():
    """上传 UPLOAD_FOLDER 中的 MP4 文件到 Bilibili，尝试按顺序作为分P上传。"""
    global yaml_config # 确保能访问全局配置
    if not yaml_config: # 如果配置加载失败，则不执行上传
         logging.error("Bilibili 上传配置 (config.yaml) 未成功加载，跳过上传步骤。")
         return

    logging.info("开始检查并上传视频到 Bilibili...")
    uploaded_count = 0
    error_count = 0
    files_processed = 0

    # 1. 检查登录状态 (假设 LoginController 不需要 cookies_path)
    try:
        login_controller = LoginController() 
        if not login_controller.check_bilibili_login():
            logging.error("Bilibili 登录验证失败，请检查 cookies.json 文件是否有效或已生成。")
            return # 登录失败则不继续上传
        logging.info("Bilibili 登录验证成功。")
    except Exception as e:
        logging.error(f"检查 Bilibili 登录状态时出错: {e}")
        return

    # 2. 查找并排序待上传的视频
    mp4_files = glob.glob(os.path.join(config.UPLOAD_FOLDER, "*.mp4"))

    if not mp4_files:
        logging.info("在上传目录中没有找到 MP4 文件，无需上传。")
        return

    files_processed = len(mp4_files)

    # --- 按文件名中的时间戳排序 --- 
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
        mp4_files.sort(key=get_timestamp_from_filename)
        logging.info(f"找到 {files_processed} 个待上传文件，已按时间顺序排序：")
        for f in mp4_files:
            logging.info(f"  - {os.path.basename(f)}")
    except Exception as e:
        logging.error(f"根据时间戳排序文件时出错: {e}，将按默认顺序处理。")

    # 3. 准备上传
    # (假设 UploadController 和 FeedController 也不需要 cookies_path)
    upload_controller = UploadController() 
    # feed_controller = FeedController() # 可能需要用于获取 BVID
    bvid = None # 用于存储第一个视频成功上传后获得的 BVID
    first_video_uploaded_successfully = False

    # --- 从 yaml_config 获取通用上传参数 ---
    # 这些参数在整个上传过程中（包括分P）通常保持不变
    try:
        tid = yaml_config['tid']
        tag = yaml_config['tag']
        source = yaml_config['source']
        cover = yaml_config['cover']
        dynamic = yaml_config['dynamic']
        desc = yaml_config['desc']
        # cdn = yaml_config['cdn'] # 移出严格检查
        title_template = yaml_config['title'] # 获取标题模板
    except KeyError as e:
         logging.error(f"从 config.yaml 中获取必要的上传参数 '{e.args[0]}' 失败。请检查配置文件。")
         return # 缺少必要参数，无法继续
    
    # --- 获取可选的 CDN 参数 ---
    cdn = yaml_config.get('cdn') # 使用 .get() 获取，如果不存在则为 None
    if cdn:
        logging.info(f"使用配置文件中的上传线路 (CDN): {cdn}")
    else:
        logging.info("未在配置文件中指定上传线路 (CDN)，将使用上传库默认线路。")

    # --- 尝试按分P上传 --- 
    if mp4_files:
        first_video_path = mp4_files[0]
        remaining_videos = mp4_files[1:]

        # --- 上传第一个视频 --- 
        try:
            base_filename = os.path.basename(first_video_path)
            base_title_part = os.path.splitext(base_filename)[0] # 文件名去后缀 "银剑君录播YYYY-MM-DDTHH_mm_ss"
            title = title_template # 默认使用模板原样作为标题
            formatted_time_str = "" # 用于替换 {time}

            # 尝试从文件名解析日期时间并格式化
            try:
                 timestamp_str = base_title_part.split('录播')[-1].replace('T', ' ')
                 dt_obj = datetime.strptime(timestamp_str, '%Y-%m-%d %H_%M_%S')
                 # 选择一种格式，例如 'YYYY年MM月DD日 HH:MM'
                 formatted_time_str = dt_obj.strftime('%Y年%m月%d日 %H:%M') 
                 
                 # 如果标题模板包含 {time}，则替换
                 if '{time}' in title_template:
                      title = title_template.replace('{time}', formatted_time_str)
                      logging.info(f"根据模板和文件名生成标题: {title}")
                 else:
                      # 如果模板没有 {time}，但有多个文件，考虑修改标题以示区分或合集
                      if remaining_videos:
                           # 可以选择附加日期或 P1
                           title = f"{title_template} (合集 {dt_obj.strftime('%Y-%m-%d')})" # 示例
                           logging.info(f"生成合集标题: {title}")
                      # else: title 保持模板原样

            except Exception as e:
                 logging.warning(f"无法从文件名 {base_filename} 解析日期或格式化标题: {e}。将使用 config.yaml 中的原始 title: '{title_template}'")
                 title = title_template # 解析失败，使用原始模板标题

            logging.info(f"准备上传第一个视频 (创建稿件): {base_filename}")
            logging.info(f"  标题: {title}") 
            logging.info(f"  分区: {tid}")
            logging.info(f"  标签: {tag}")

            # --- 关键: 调用上传接口 --- 
            # 假设 upload_video_entry 使用 yaml_config 中的信息，但仍需传递路径和部分参数
            upload_result = upload_controller.upload_video_entry(
                video_path=first_video_path,
                # 不再传递 yaml 路径，假设 biliup 内部处理或不再需要
                yaml=None, # 恢复传递 yaml 路径
                
                tid=tid,
                title=title, # 使用上面生成的标题
                copyright=2, # 添加 copyright 参数，1 表示自制
                desc=desc,
                tag=tag,
                source=source,
                cover=cover,
                dynamic=dynamic,
                cdn=cdn # 使用从 yaml 读取的 CDN
            )

            # --- 处理上传结果并尝试获取 BVID --- 
            if upload_result:
                logging.info(f"成功上传第一个视频: {upload_result}") # 使用 upload_result 记录，虽然它可能只是 True
                uploaded_count += 1
                first_video_uploaded_successfully = True

                # --- 尝试通过调用 get_video_dict_info 获取 BVID --- 
                bvid = None # 明确 bvid 初始为 None
                logging.info("第一个视频上传成功，尝试调用 API 查询最新投稿 BVID...")
                try:
                    feed_controller = FeedController() # 假设不需要 cookies_path
                    # 查询状态可以根据需要调整，例如 'is_pubing,pubed' 可能更适合刚上传的视频
                    status_to_check = 'pubed,not_pubed,is_pubing' 
                    logging.info(f"调用 get_video_dict_info(size=1, status_type='{status_to_check}')")
                    video_list_data = feed_controller.get_video_dict_info(size=1, status_type=status_to_check)
                    logging.info(f"API 返回的视频列表数据: {video_list_data}") # 打印返回结果

                    # 解析返回的字典
                    if video_list_data and isinstance(video_list_data, dict) and len(video_list_data) > 0:
                         # 假设字典的第一个值就是最新的 BVID
                         potential_bvid = list(video_list_data.values())[0]
                         if isinstance(potential_bvid, str) and potential_bvid.startswith('BV'):
                              bvid = potential_bvid
                              logging.info(f"成功从 API 获取到最新投稿 BVID: {bvid}")
                         else:
                              logging.warning(f"从 API 返回的数据中未能提取有效的 BVID (第一个值: {potential_bvid})。")
                    else:
                         logging.warning(f"API 返回的数据不是预期的非空字典 ({video_list_data})，无法获取 BVID。")

                except AttributeError:
                    logging.error("`bilitool` 的 FeedController 中似乎没有找到 `get_video_dict_info` 方法。无法查询 BVID。")
                except Exception as e:
                     logging.error(f"调用 get_video_dict_info 或解析结果时出错: {e}", exc_info=True)

                # 如果未能获取 BVID，记录警告
                if not bvid:
                     logging.warning("未能自动获取 BVID。将无法追加分P，后续文件将尝试独立上传。")
                 
                 # 后续逻辑会根据 bvid 是否被成功赋值来决定是否追加

            else:
                 logging.error(f"上传第一个视频失败 (返回值为 {upload_result}) : {base_filename}")
                 error_count += 1 # 第一个失败，算作错误

        except Exception as e:
            # 捕获所有上传过程中的异常，包括可能的 biliup 内部错误
            logging.error(f"上传第一个视频 {base_filename} 时发生异常: {e}", exc_info=True) # 添加 exc_info=True 获取 traceback
            error_count += 1 # 第一个异常，算作错误
            # first_video_uploaded_successfully 保持 False

        # --- 如果第一个视频上传成功并且获取到了 BVID，则尝试追加剩余视频 --- 
        if first_video_uploaded_successfully and bvid and remaining_videos:
            logging.info(f"获取到 BVID: {bvid}，准备追加剩余 {len(remaining_videos)} 个视频作为分P。")
            part_number = 2 # 分P从 P2 开始
            for video_path in remaining_videos:
                base_filename_part = os.path.basename(video_path)
                try:
                    # 分P标题通常会自动生成为 Pn，但biliup可能允许自定义
                    # 尝试从文件名提取时间作为分P标题的一部分
                    part_time_str = ""
                    try:
                         part_base = os.path.splitext(base_filename_part)[0]
                         part_timestamp_str = part_base.split('录播')[-1].replace('T', ' ')
                         part_dt_obj = datetime.strptime(part_timestamp_str, '%Y-%m-%d %H_%M_%S')
                         part_time_str = part_dt_obj.strftime('%H:%M:%S') # 例如 HH:MM:SS
                    except Exception:
                         part_time_str = f"Part {part_number}" # 解析失败用 Pn

                    part_title = f"P{part_number} {part_time_str}" # 组合标题 Pn HH:MM:SS
                    
                    logging.info(f"  准备追加分P ({part_title}): {base_filename_part}")
                    
                    # --- 关键: 调用追加接口 (假设存在且参数正确) --- 
                    # 参数需要根据 biliup 的实际 append_video_entry 实现调整
                    append_success = upload_controller.append_video_entry(
                        video_path=video_path,
                        bvid=bvid,
                        cdn=cdn,         # 可能需要传递 CDN
                    )

                    if append_success:
                        logging.info(f"    成功追加分P: {base_filename_part}")
                        uploaded_count += 1
                        # 追加成功后删除文件
                        try:
                            if config.DELETE_UPLOADED_FILES:
                                os.remove(video_path)
                                logging.info(f"    已删除已追加的 MP4: {base_filename_part} (根据配置)")
                            else:
                                logging.info(f"    保留已追加的 MP4: {base_filename_part} (根据配置)")
                        except OSError as e:
                            logging.warning(f"    删除已追加的 MP4 文件时出错: {e}")
                    else:
                        logging.error(f"    追加分P失败 (返回值为 {append_success}): {base_filename_part}")
                        error_count += 1
                    part_number += 1

                except AttributeError:
                    logging.error(f"    `bilitool` (或 biliup) 中似乎没有找到追加分P的方法 (`append_video_entry`)。停止追加。")
                    error_count += len(remaining_videos) - remaining_videos.index(video_path) # 剩余未追加的都算错误
                    # 保留剩余未上传的文件
                    break # 停止尝试追加
                except Exception as e:
                    logging.error(f"    追加分P {base_filename_part} 时发生异常: {e}", exc_info=True)
                    error_count += 1
                    # 出现异常时，是否继续尝试下一个？这里选择继续，但标记错误
            
            # 所有追加尝试完成后 (无论成功失败)，删除第一个视频文件 (因为它已经属于B站稿件)
            try:
                if config.DELETE_UPLOADED_FILES:
                    os.remove(first_video_path)
                    logging.info(f"已删除第一个视频的 MP4 文件: {os.path.basename(first_video_path)} (根据配置)")
                else:
                    logging.info(f"保留第一个视频的 MP4 文件: {os.path.basename(first_video_path)} (根据配置)")
            except OSError as e:
                logging.warning(f"删除第一个视频的 MP4 文件时出错: {e}")

        # --- 如果第一个视频上传成功但无法追加 (无BVID或无剩余文件) ---
        elif first_video_uploaded_successfully and (not bvid or not remaining_videos):
            # 第一个成功了，但没BVID 或 没有剩余文件 -> 任务完成 (对于第一个文件)
            logging.info(f"第一个视频上传成功。{'没有获取到 BVID 或无追加功能，无法追加分P。' if remaining_videos else '没有其他文件需要追加。'}")
            # 删除第一个已上传的文件 (如果配置了删除)
            try:
                 if config.DELETE_UPLOADED_FILES: # 使用 config.py 的设置
                     os.remove(first_video_path)
                     logging.info(f"已删除已上传的 MP4 文件: {os.path.basename(first_video_path)} (根据配置)")
                 else:
                     logging.info(f"保留已上传的 MP4 文件: {os.path.basename(first_video_path)} (根据配置)")
            except OSError as e:
                 logging.warning(f"删除已上传的 MP4 文件时出错: {e}")
             
             # 如果是因为没有 BVID 而无法追加，需要处理剩余文件 (回退到独立上传)
            if not bvid and remaining_videos:
                 logging.warning(f"由于未能获取 BVID，本次运行将跳过剩余的 {len(remaining_videos)} 个文件。")

         # --- 如果第一个视频上传失败且有剩余文件 ---
        elif not first_video_uploaded_successfully and remaining_videos:
             logging.warning(f"由于第一个视频上传失败，本次运行将跳过剩余的 {len(remaining_videos)} 个文件。")

    # --- 结束日志 --- 
    logging.info(f"Bilibili 视频上传尝试完成。共找到 {files_processed} 个文件，成功上传/追加: {uploaded_count}，失败或未处理: {error_count}。")


def job():
    """定义要定时执行的任务"""
    logging.info("="*30 + f" 开始执行定时任务 @ {datetime.now()} " + "="*30)
    # 1. 清理小文件
    cleanup_small_files()
    # 2. 转换弹幕
    convert_danmaku()
    # 3. 压制视频
    encode_video()
    # 4. 上传视频
    upload_to_bilibili()
    logging.info("="*30 + f" 定时任务执行完毕 @ {datetime.now()} " + "="*30 + "\n")


if __name__ == "__main__":
    logging.info("程序启动，开始加载配置...")
    # 首先加载 YAML 配置，如果失败则退出
    if not load_yaml_config():
        logging.error("无法加载或验证配置文件 config.yaml，程序将退出。")
        exit(1) # 或者采取其他错误处理措施

    logging.info("配置加载完成，开始设置定时任务...")

    # 立即执行一次任务，以便快速测试
    job()

    # 设置定时任务
    schedule.every(config.SCHEDULE_INTERVAL_MINUTES).minutes.do(job)
    logging.info(f"定时任务已设置，每 {config.SCHEDULE_INTERVAL_MINUTES} 分钟执行一次。")

    while True:
        schedule.run_pending()
        time.sleep(1)
