import os
import glob
import subprocess
import json
import logging

import config
from dmconvert import convert_xml_to_ass

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


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
