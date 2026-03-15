import os
import glob
import shutil
import subprocess
import json
import logging

from . import config
from dmconvert import convert_xml_to_ass
from .danmaku_postprocess import postprocess_ass

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Module-level failure counter: {xml_file_path: consecutive_failure_count}
_failure_counts: dict[str, int] = {}


def _quarantine_files(*file_paths: str):
    """Move files to the failed directory for manual inspection."""
    for fp in file_paths:
        if not os.path.exists(fp):
            continue
        dest = os.path.join(config.FAILED_FOLDER, os.path.basename(fp))
        try:
            shutil.move(fp, dest)
            logging.warning(f"已隔离文件到 failed 目录: {os.path.basename(fp)}")
        except Exception as e:
            logging.error(f"隔离文件 {os.path.basename(fp)} 失败: {e}")


def _record_failure(key: str, *related_files: str) -> bool:
    """Increment failure count. If threshold reached, quarantine files.

    Returns True if the file was quarantined.
    """
    _failure_counts[key] = _failure_counts.get(key, 0) + 1
    count = _failure_counts[key]
    if count >= config.MAX_RETRY_COUNT:
        logging.warning(
            f"文件 {os.path.basename(key)} 已连续失败 {count} 次，"
            f"达到阈值 {config.MAX_RETRY_COUNT}，移入隔离目录"
        )
        _quarantine_files(key, *related_files)
        _failure_counts.pop(key, None)
        return True
    logging.info(f"文件 {os.path.basename(key)} 失败计数: {count}/{config.MAX_RETRY_COUNT}")
    return False


def _clear_failure(key: str):
    """Clear failure count on success."""
    _failure_counts.pop(key, None)


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

        # Skip files that have already reached the failure threshold
        if _failure_counts.get(xml_file, 0) >= config.MAX_RETRY_COUNT:
            logging.info(f"文件 {os.path.basename(xml_file)} 已达失败阈值，跳过")
            skipped_count += 1
            continue

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
            _record_failure(xml_file, flv_file)
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
                 postprocess_ass(
                     ass_file=ass_file,
                     resolution_y=resolution_y,
                     display_area=config.DANMAKU_DISPLAY_AREA,
                     opacity=config.DANMAKU_OPACITY,
                     color_enabled=config.DANMAKU_COLOR_ENABLED,
                 )
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
                 
                 _clear_failure(xml_file)
                 converted_count += 1
            else:
                 logging.error(f"转换函数执行完毕但未找到输出文件: {os.path.basename(ass_file)}")
                 _record_failure(xml_file, flv_file)
                 error_count += 1

        except Exception as e:
            logging.error(f"转换 XML 文件 {os.path.basename(xml_file)} 时出错: {e}")
            _record_failure(xml_file, flv_file)
            error_count += 1

    logging.info(f"弹幕转换完成。成功: {converted_count}, 跳过: {skipped_count}, 失败: {error_count}")
