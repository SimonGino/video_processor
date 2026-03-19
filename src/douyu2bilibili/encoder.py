import os
import glob
import subprocess
import shlex
import shutil
import logging
import sys

from . import config

logger = logging.getLogger("pipeline.encoder")

# Module-level failure counter: {file_path: consecutive_failure_count}
_failure_counts: dict[str, int] = {}
# Orphan FLV tracking: FLVs seen once without XML/ASS, processed on second sighting
_orphan_seen: set[str] = set()


def _quarantine_files(*file_paths: str) -> bool:
    """Move files to the failed directory for manual inspection.

    Returns True if all existing files were moved successfully.
    """
    all_ok = True
    for fp in file_paths:
        if not os.path.exists(fp):
            continue
        dest = os.path.join(config.FAILED_FOLDER, os.path.basename(fp))
        try:
            shutil.move(fp, dest)
            logger.warning(f"已隔离文件到 failed 目录: {os.path.basename(fp)}")
        except Exception as e:
            logger.error(f"隔离文件 {os.path.basename(fp)} 失败: {e}")
            all_ok = False
    return all_ok


def _record_failure(key: str, *related_files: str) -> bool:
    """Increment failure count. If threshold reached, quarantine files.

    Returns True if the file was quarantined (caller should skip it).
    """
    _failure_counts[key] = _failure_counts.get(key, 0) + 1
    count = _failure_counts[key]
    if count >= config.MAX_RETRY_COUNT:
        logger.warning(
            f"文件 {os.path.basename(key)} 已连续失败 {count} 次，"
            f"达到阈值 {config.MAX_RETRY_COUNT}，移入隔离目录"
        )
        if _quarantine_files(key, *related_files):
            _failure_counts.pop(key, None)
        return True
    logger.info(f"文件 {os.path.basename(key)} 失败计数: {count}/{config.MAX_RETRY_COUNT}")
    return False


def _clear_failure(key: str):
    """Clear failure count on success."""
    _failure_counts.pop(key, None)


def _build_ffmpeg_env():
    """Build ffmpeg subprocess env overrides for QSV/libva compatibility."""
    ld_library_path = (getattr(config, "FFMPEG_QSV_LD_LIBRARY_PATH", "") or "").strip()
    libva_drivers_path = (getattr(config, "FFMPEG_QSV_LIBVA_DRIVERS_PATH", "") or "").strip()
    libva_driver_name = (getattr(config, "FFMPEG_QSV_LIBVA_DRIVER_NAME", "") or "").strip()

    if not (ld_library_path or libva_drivers_path or libva_driver_name):
        return None

    env = os.environ.copy()
    if ld_library_path:
        current = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = (
            f"{ld_library_path}{os.pathsep}{current}" if current else ld_library_path
        )
    if libva_drivers_path:
        env["LIBVA_DRIVERS_PATH"] = libva_drivers_path
    if libva_driver_name:
        env["LIBVA_DRIVER_NAME"] = libva_driver_name
    return env


def _qsv_init_hw_device():
    device = (getattr(config, "FFMPEG_QSV_INIT_DEVICE", "") or "").strip()
    if device:
        return f"qsv=hw:{device}"
    return "qsv=hw"


def encode_video():
    """压制带有 ASS 弹幕的 FLV 视频为 MP4"""
    logger.info("开始处理视频文件...")
    
    # Check if video encoding should be skipped
    if config.SKIP_VIDEO_ENCODING:
        logger.info("检测到 SKIP_VIDEO_ENCODING=True 配置，将跳过压制步骤直接处理 FLV 文件")
        moved_count = 0
        skipped_count = 0
        error_count = 0
        
        # Find all FLV files
        flv_pattern = os.path.join(config.PROCESSING_FOLDER, "*.flv")
        logger.info(f"正在搜索 FLV 文件，使用模式: {flv_pattern}")
        flv_files = glob.glob(flv_pattern)
        
        if not flv_files:
            logger.warning(f"在处理目录 {config.PROCESSING_FOLDER} 中未找到任何 FLV 文件")
            # Try listing directory contents to check for permission issues
            try:
                dir_content = os.listdir(config.PROCESSING_FOLDER)
                logger.info(f"目录内容: {dir_content[:10]}{'...' if len(dir_content) > 10 else ''}")
            except Exception as e:
                logger.error(f"无法列出目录内容: {e}")
        else:
            logger.info(f"找到 {len(flv_files)} 个 FLV 文件: {[os.path.basename(f) for f in flv_files]}")
        
        for flv_file in flv_files:
            try:
                base_name = os.path.splitext(flv_file)[0]
                # Keep .flv extension for target path
                upload_flv_file = os.path.join(config.UPLOAD_FOLDER, os.path.basename(flv_file))
                
                logger.info(f"处理文件: {os.path.basename(flv_file)}")
                
                # Check if FLV file is currently being recorded
                flv_part_file = flv_file + ".part"
                if os.path.exists(flv_part_file):
                    logger.info(f"跳过处理，因为找到正在录制的文件: {os.path.basename(flv_part_file)}")
                    skipped_count += 1
                    continue
                
                # Check file size
                try:
                    file_size = os.path.getsize(flv_file)
                    logger.info(f"文件大小: {file_size / (1024*1024):.2f} MB")
                except Exception as e:
                    logger.error(f"获取文件大小失败: {e}")
                    
                # Check if FLV file already exists in upload directory
                if os.path.exists(upload_flv_file):
                    logger.info(f"FLV 文件已存在于上传目录，跳过处理: {os.path.basename(upload_flv_file)}")
                    skipped_count += 1
                    continue
                    
                # Check if upload directory exists and is writable
                if not os.path.exists(config.UPLOAD_FOLDER):
                    try:
                        os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
                        logger.info(f"创建上传目录: {config.UPLOAD_FOLDER}")
                    except Exception as e:
                        logger.error(f"创建上传目录失败: {e}")
                        error_count += 1
                        continue
                        
                # Check file permissions
                try:
                    if not os.access(flv_file, os.R_OK):
                        logger.error(f"没有权限读取文件: {flv_file}")
                        xml_file = os.path.splitext(flv_file)[0] + ".xml"
                        _record_failure(flv_file, xml_file)
                        error_count += 1
                        continue

                    if not os.access(config.UPLOAD_FOLDER, os.W_OK):
                        logger.error(f"没有权限写入上传目录: {config.UPLOAD_FOLDER}")
                        xml_file = os.path.splitext(flv_file)[0] + ".xml"
                        _record_failure(flv_file, xml_file)
                        error_count += 1
                        continue
                except Exception as e:
                    logger.error(f"检查文件权限时出错: {e}")
                
                # Move FLV file directly to upload directory
                try:
                    logger.info(f"准备移动文件: {os.path.basename(flv_file)} -> {config.UPLOAD_FOLDER}")
                    shutil.move(flv_file, upload_flv_file)  # 使用 move 直接移动文件
                    logger.info(f"成功移动文件到: {upload_flv_file}")
                    _clear_failure(flv_file)
                    moved_count += 1
                except Exception as e:
                    logger.error(f"移动文件 {os.path.basename(flv_file)} 到上传目录失败: {e}")
                    xml_file = os.path.splitext(flv_file)[0] + ".xml"
                    _record_failure(flv_file, xml_file)
                    error_count += 1
            except Exception as e:
                logger.error(f"处理文件 {os.path.basename(flv_file) if 'flv_file' in locals() else '未知'} 时发生未知错误: {e}")
                if 'flv_file' in locals():
                    xml_file = os.path.splitext(flv_file)[0] + ".xml"
                    _record_failure(flv_file, xml_file)
                error_count += 1
        
        logger.info(f"直接处理 FLV 文件完成。成功: {moved_count}, 跳过: {skipped_count}, 失败: {error_count}")
        return
    
    # Original video encoding logic below
    logger.info("开始压制视频...")
    encoded_count = 0
    skipped_count = 0
    error_count = 0
    ffmpeg_env = _build_ffmpeg_env()

    ass_files = glob.glob(os.path.join(config.PROCESSING_FOLDER, "*.ass"))

    for ass_file in ass_files:
        base_name = os.path.splitext(ass_file)[0]
        flv_file = base_name + ".flv"
        # Define temp output path and final upload path
        temp_mp4_file = base_name + ".mp4" # 输出到 processing 文件夹
        upload_mp4_file = os.path.join(config.UPLOAD_FOLDER, os.path.basename(temp_mp4_file)) # 最终移动到 upload 文件夹

        # Skip files that have already reached the failure threshold
        if _failure_counts.get(flv_file, 0) >= config.MAX_RETRY_COUNT:
            logger.info(f"文件 {os.path.basename(flv_file)} 已达失败阈值，跳过")
            skipped_count += 1
            continue

        # Check if FLV file exists
        if not os.path.exists(flv_file):
            logger.warning(f"找不到对应的 FLV 文件，跳过压制: {os.path.basename(flv_file)} (ASS: {os.path.basename(ass_file)})")
            skipped_count += 1
            continue

        # Check if final MP4 file already exists in upload directory
        if os.path.exists(upload_mp4_file):
            logger.info(f"MP4 文件已存在于上传目录，跳过压制: {os.path.basename(upload_mp4_file)}")
            # If final file exists, also consider deleting ass and flv in processing folder
            try:
                if os.path.exists(ass_file):
                    os.remove(ass_file)
                    logger.info(f"已删除已处理的 ASS: {os.path.basename(ass_file)}")
                if os.path.exists(flv_file):
                    os.remove(flv_file)
                    logger.info(f"已删除已处理的 FLV: {os.path.basename(flv_file)}")
            except OSError as e:
                logger.warning(f"删除已存在于上传目录的视频对应的原始文件时出错: {e}")
            skipped_count += 1
            continue
        
        # If temp MP4 file exists (possibly from interrupted encoding), delete it first
        if os.path.exists(temp_mp4_file):
            logger.warning(f"发现上次残留的临时 MP4 文件，将删除: {os.path.basename(temp_mp4_file)}")
            try:
                os.remove(temp_mp4_file)
            except OSError as e:
                logger.error(f"删除残留的临时 MP4 文件失败: {e}, 跳过此文件压制。")
                error_count += 1
                continue


        # Build FFmpeg command (using QSV acceleration)
        # Note: shlex.quote is used to safely handle filenames with special characters
        # Output to temp_mp4_file
        qsv_cmd_str = (
            f'{config.FFMPEG_PATH} -v verbose '
            f'-init_hw_device {_qsv_init_hw_device()} '
            f'-hwaccel qsv '
            f'-hwaccel_output_format qsv '
            f'-i {shlex.quote(flv_file)} '
            f'-vf "subtitles=filename={shlex.quote(ass_file)},hwupload=extra_hw_frames=64" '
            f'-c:v h264_qsv '
            f'-preset veryfast '
            f'-global_quality 35 ' # Lower number = higher quality, higher = faster/smaller
            f'-c:a copy ' # Copy audio stream directly without re-encoding
            f'-y {shlex.quote(temp_mp4_file)}' # Output to temp file
        )

        logger.info(f"开始压制: {os.path.basename(flv_file)} + {os.path.basename(ass_file)} -> {os.path.basename(temp_mp4_file)}")
        logger.debug(f"执行 FFmpeg 命令: {qsv_cmd_str}")

        # Hardware-only fallback encoders for environments where QSV is unavailable.
        fallback_cmds = []
        if sys.platform == "darwin":
            fallback_cmds.append(
                (
                    "videotoolbox",
                    f'{config.FFMPEG_PATH} -v verbose '
                    f'-i {shlex.quote(flv_file)} '
                    f'-vf "subtitles=filename={shlex.quote(ass_file)}" '
                    f'-c:v h264_videotoolbox '
                    f'-b:v 6M '
                    f'-maxrate 8M '
                    f'-bufsize 12M '
                    f'-c:a copy '
                    f'-y {shlex.quote(temp_mp4_file)}'
                )
            )

        try:
            # Safer approach: split command into list
            cmd_list = shlex.split(qsv_cmd_str)
            try:
                process = subprocess.run(
                    cmd_list,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    env=ffmpeg_env,
                )
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or "").lower()
                is_qsv_error = ("init_hw_device" in stderr) or ("device creation failed" in stderr) or ("qsv=hw" in stderr)
                if not is_qsv_error:
                    raise

                logger.warning("QSV 不可用，尝试使用备用硬件编码器压制（不使用 CPU 编码兜底）")
                # Clean up possibly corrupted output before fallback
                if os.path.exists(temp_mp4_file):
                    try:
                        os.remove(temp_mp4_file)
                    except OSError:
                        pass

                if not fallback_cmds:
                    logger.error("未找到可用的备用硬件编码器，已停止压制（已禁用 CPU/libx264 兜底）")
                    raise e

                last_exc: subprocess.CalledProcessError | None = None
                for encoder_name, cmd_str in fallback_cmds:
                    try:
                        logger.info(f"使用备用编码器压制: {encoder_name}")
                        cmd_list = shlex.split(cmd_str)
                        process = subprocess.run(
                            cmd_list,
                            check=True,
                            capture_output=True,
                            text=True,
                            encoding='utf-8',
                            errors='replace',
                            env=ffmpeg_env,
                        )
                        break
                    except subprocess.CalledProcessError as e2:
                        last_exc = e2
                        logger.warning(f"备用编码器失败: {encoder_name} (code={e2.returncode})")
                        if os.path.exists(temp_mp4_file):
                            try:
                                os.remove(temp_mp4_file)
                            except OSError:
                                pass
                else:
                    raise last_exc if last_exc else e

            logger.info(f"成功压制到临时文件: {os.path.basename(temp_mp4_file)}")
            logger.debug(f"FFmpeg stdout:\n{process.stdout}")
            logger.debug(f"FFmpeg stderr:\n{process.stderr}")

            # After successful encoding, move to upload directory
            try:
                logger.info(f"准备移动文件: {os.path.basename(temp_mp4_file)} -> {config.UPLOAD_FOLDER}")
                shutil.move(temp_mp4_file, upload_mp4_file)
                logger.info(f"成功移动文件到: {upload_mp4_file}")

                # After successful move, delete original flv and ass files
                try:
                    if config.DELETE_UPLOADED_FILES:
                        os.remove(flv_file)
                        logger.info(f"已删除原始 FLV: {os.path.basename(flv_file)} (根据配置)")
                        
                        if os.path.exists(ass_file):
                            os.remove(ass_file)
                            logger.info(f"已删除原始 ASS: {os.path.basename(ass_file)} (根据配置)")
                    else:
                        logger.info(f"保留原始 FLV: {os.path.basename(flv_file)} (根据配置)")
                        
                        if os.path.exists(ass_file):
                            logger.info(f"保留原始 ASS: {os.path.basename(ass_file)} (根据配置)")
                    
                    _clear_failure(flv_file)
                    encoded_count += 1 # Only count as success when fully complete
                except OSError as e:
                    logger.warning(f"移动文件成功，但删除原始文件时出错 ({os.path.basename(flv_file)} / {os.path.basename(ass_file)}): {e}")
                    # Even if deletion fails, encoding and move were successful
                    _clear_failure(flv_file)
                    encoded_count += 1

            except Exception as e: # Catch all exceptions during move
                logger.error(f"移动文件 {os.path.basename(temp_mp4_file)} 到上传目录失败: {e}")
                _record_failure(flv_file, ass_file)
                error_count += 1
                # If move fails, try to delete temp MP4 file, keep original files
                try:
                    if os.path.exists(temp_mp4_file):
                         os.remove(temp_mp4_file)
                         logger.info(f"已删除移动失败的临时 MP4 文件: {os.path.basename(temp_mp4_file)}")
                except OSError as del_e:
                     logger.warning(f"删除移动失败的临时 MP4 文件时也出错: {del_e}")


        except FileNotFoundError:
             logger.error(f"找不到 ffmpeg 命令，请检查 config.py 中的 FFMPEG_PATH 设置或确保 ffmpeg 在系统 PATH 中。")
             _record_failure(flv_file, ass_file)
             error_count += 1
        except subprocess.CalledProcessError as e:
            logger.error(f"运行 ffmpeg 压制视频时出错 (文件: {os.path.basename(flv_file)}): {e}")
            logger.error(f"FFmpeg return code: {e.returncode}")
            logger.error(f"FFmpeg stdout:\n{e.stdout}")
            logger.error(f"FFmpeg stderr:\n{e.stderr}")
            stderr = e.stderr or ""
            if ("No such filter" in stderr) and ("subtitles" in stderr or "ass" in stderr):
                logger.error(
                    "当前 FFmpeg 未启用 libass，无法 burn-in ASS 弹幕。macOS/Homebrew 建议安装 `ffmpeg-full`，"
                    "并在 config.py 里把 `FFMPEG_PATH` 指向 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`。"
                )
            _record_failure(flv_file, ass_file)
            error_count += 1
             # If encoding fails, try to delete possibly corrupted temp MP4 file
            if os.path.exists(temp_mp4_file):
                 try:
                      os.remove(temp_mp4_file)
                      logger.info(f"已删除压制失败产生的临时 MP4: {os.path.basename(temp_mp4_file)}")
                 except OSError as del_e:
                      logger.warning(f"删除压制失败的临时 MP4 文件时出错: {del_e}")

        except Exception as e:
            logger.error(f"压制视频时发生未知错误 (文件: {os.path.basename(flv_file)}): {e}")
            _record_failure(flv_file, ass_file)
            error_count += 1
            # Also try to clean up temp files
            if os.path.exists(temp_mp4_file):
                 try:
                      os.remove(temp_mp4_file)
                      logger.info(f"已删除因未知错误产生的临时 MP4: {os.path.basename(temp_mp4_file)}")
                 except OSError as del_e:
                      logger.warning(f"删除因未知错误产生的临时 MP4 文件时出错: {del_e}")

    # --- Orphan FLV passthrough: process FLVs without XML/ASS ---
    all_flv_files = glob.glob(os.path.join(config.PROCESSING_FOLDER, "*.flv"))
    # Collect basenames that have ASS files (already handled above)
    ass_basenames = {os.path.splitext(af)[0] for af in ass_files}
    orphan_count = 0

    for flv_file in all_flv_files:
        base_name = os.path.splitext(flv_file)[0]

        # Skip if this FLV has an ASS (already handled by the loop above)
        if base_name in ass_basenames:
            continue

        # Skip if still recording
        if os.path.exists(flv_file + ".part"):
            continue

        # Skip if XML exists (danmaku conversion will create ASS next round)
        if os.path.exists(base_name + ".xml"):
            continue

        # Skip if already reached failure threshold
        if _failure_counts.get(flv_file, 0) >= config.MAX_RETRY_COUNT:
            continue

        temp_mp4_file = base_name + ".mp4"
        upload_mp4_file = os.path.join(config.UPLOAD_FOLDER, os.path.basename(temp_mp4_file))

        # Skip if already in upload directory
        if os.path.exists(upload_mp4_file):
            continue

        # First sighting: mark and skip (wait one cycle to avoid race with XML writer)
        if flv_file not in _orphan_seen:
            _orphan_seen.add(flv_file)
            logger.info(
                f"发现无弹幕 FLV: {os.path.basename(flv_file)}，"
                f"等待下一轮确认后处理（避免与弹幕写入竞态）"
            )
            continue

        # Second sighting: confirmed orphan, encode without subtitles
        _orphan_seen.discard(flv_file)
        logger.info(f"无弹幕 FLV 确认：{os.path.basename(flv_file)}，开始无弹幕编码")

        # Clean up leftover temp file
        if os.path.exists(temp_mp4_file):
            try:
                os.remove(temp_mp4_file)
            except OSError as e:
                logger.error(f"删除残留临时 MP4 失败: {e}，跳过")
                error_count += 1
                continue

        # FFmpeg command without subtitle filter
        qsv_cmd_str = (
            f'{config.FFMPEG_PATH} -v verbose '
            f'-init_hw_device {_qsv_init_hw_device()} '
            f'-hwaccel qsv '
            f'-hwaccel_output_format qsv '
            f'-i {shlex.quote(flv_file)} '
            f'-vf "hwupload=extra_hw_frames=64" '
            f'-c:v h264_qsv '
            f'-preset veryfast '
            f'-global_quality 35 '
            f'-c:a copy '
            f'-y {shlex.quote(temp_mp4_file)}'
        )

        fallback_cmds = []
        if sys.platform == "darwin":
            fallback_cmds.append(
                (
                    "videotoolbox",
                    f'{config.FFMPEG_PATH} -v verbose '
                    f'-i {shlex.quote(flv_file)} '
                    f'-c:v h264_videotoolbox '
                    f'-b:v 6M '
                    f'-maxrate 8M '
                    f'-bufsize 12M '
                    f'-c:a copy '
                    f'-y {shlex.quote(temp_mp4_file)}'
                )
            )

        try:
            cmd_list = shlex.split(qsv_cmd_str)
            try:
                process = subprocess.run(
                    cmd_list, check=True, capture_output=True,
                    text=True, encoding='utf-8', errors='replace', env=ffmpeg_env,
                )
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or "").lower()
                is_qsv_error = ("init_hw_device" in stderr) or ("device creation failed" in stderr) or ("qsv=hw" in stderr)
                if not is_qsv_error:
                    raise

                logger.warning("QSV 不可用，尝试备用硬件编码器（无弹幕模式）")
                if os.path.exists(temp_mp4_file):
                    try:
                        os.remove(temp_mp4_file)
                    except OSError:
                        pass

                if not fallback_cmds:
                    raise e

                last_exc = None
                for encoder_name, cmd_str in fallback_cmds:
                    try:
                        logger.info(f"使用备用编码器: {encoder_name}")
                        process = subprocess.run(
                            shlex.split(cmd_str), check=True, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', env=ffmpeg_env,
                        )
                        break
                    except subprocess.CalledProcessError as e2:
                        last_exc = e2
                        if os.path.exists(temp_mp4_file):
                            try:
                                os.remove(temp_mp4_file)
                            except OSError:
                                pass
                else:
                    raise last_exc if last_exc else e

            # Encoding succeeded, move to upload
            shutil.move(temp_mp4_file, upload_mp4_file)
            logger.info(f"无弹幕编码完成并移动到上传目录: {os.path.basename(upload_mp4_file)}")

            if config.DELETE_UPLOADED_FILES:
                try:
                    os.remove(flv_file)
                    logger.info(f"已删除原始 FLV: {os.path.basename(flv_file)}")
                except OSError as e:
                    logger.warning(f"删除原始 FLV 失败: {e}")

            _clear_failure(flv_file)
            orphan_count += 1
            encoded_count += 1

        except Exception as e:
            logger.error(f"无弹幕编码失败 ({os.path.basename(flv_file)}): {e}")
            _record_failure(flv_file)
            error_count += 1
            if os.path.exists(temp_mp4_file):
                try:
                    os.remove(temp_mp4_file)
                except OSError:
                    pass

    if orphan_count:
        logger.info(f"无弹幕 FLV 直通处理完成: {orphan_count} 个")

    logger.info(f"视频压制与移动完成。成功: {encoded_count}, 跳过: {skipped_count}, 失败: {error_count}")
