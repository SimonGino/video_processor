# Bilibili 自动录播处理与上传脚本

本项目旨在自动化处理直播录像文件（FLV格式）及其对应的弹幕文件（XML格式），将其压制为内嵌 ASS 弹幕的 MP4 视频，并自动上传到 Bilibili。

## 主要功能

*   **定时任务:** 使用 `schedule` 库定时执行处理流程。
*   **文件清理:** 自动删除指定目录下体积过小的无效 FLV 文件及其关联的 XML 文件。
*   **弹幕转换:** 调用 `dmconvert` (或其他类似工具) 将 XML 格式的 Bilibili 弹幕转换为 ASS 格式字幕文件。
*   **视频压制:** 使用 FFmpeg (支持 Intel QSV 硬件加速) 将 FLV 视频和 ASS 字幕压制合并为 MP4 文件。
*   **自动上传:** 调用 `bilitool` (或其他你配置的 Bilibili 上传工具) 将处理好的 MP4 文件上传到 Bilibili。
*   **分P上传尝试:**
    *   按文件名时间顺序处理待上传文件。
    *   上传第一个视频后，尝试通过 `bilitool` 的 API 获取其 BVID。
    *   如果成功获取 BVID，则尝试将后续视频作为分P追加到同一稿件。
*   **上传失败处理:**
    *   如果无法获取 BVID 或 `bilitool` 不支持追加，则剩余视频**不会**作为独立稿件上传，会被跳过本次处理。
    *   如果第一个视频上传失败，则剩余视频也会被跳过本次处理。
*   **灵活配置:**
    *   通过 `config.py` 配置处理/上传目录路径、FFmpeg/FFprobe路径、清理阈值、弹幕字体大小、定时任务间隔、上传后是否删除文件等。
    *   通过 `config.yaml` 配置 Bilibili 投稿元数据（标题模板、分区ID、标签、简介、来源等）。
*   **后台运行支持:** 可通过不同方式在后台持续运行。

## 环境要求

*   **Python 3.x**
*   **FFmpeg 和 FFprobe:** 需要安装并将可执行文件路径添加到系统 PATH 环境变量中，或者在 `config.py` 文件中指定其绝对路径。推荐使用支持 QSV 的版本以启用硬件加速。
*   **bilitool (或你使用的 Bilibili 上传库):** 需要正确安装并配置。本脚本依赖其提供 `LoginController`, `UploadController`, `FeedController` 类以及相应的 `check_bilibili_login`, `upload_video_entry`, `get_video_dict_info`, `append_video_entry` (假设存在) 等方法。请参考 `bilitool` 的文档进行安装和配置。
*   **Python 依赖库:** `schedule`, `PyYAML`

## 安装

1.  **克隆或下载项目:**
    ```bash
    # 如果使用 Git
    git clone <your-repository-url>
    cd <project-directory>
    ```
    或者直接下载 ZIP 文件并解压。

2.  **安装 Python 依赖:**
    在项目根目录下打开终端，运行：
    ```bash
    pip install schedule PyYAML
    # 或者使用 uv
    # uv pip install schedule PyYAML
    ```

3.  **安装/配置 `bilitool`:**
    请根据 `bilitool` 的官方说明进行安装和必要的配置。确保其库文件能被 Python 找到。

4.  **安装 FFmpeg/FFprobe:**
    下载并安装 FFmpeg，确保 `ffmpeg` 和 `ffprobe` 命令可用（添加到 PATH 或配置 `config.py`）。

## 配置

项目包含两个主要的配置文件：

1.  **`config.py`:**
    *   `PROCESSING_FOLDER`: 存放原始 FLV 和 XML 文件的目录路径（**请确保录制工具将文件保存到此处**）。支持绝对路径。
    *   `UPLOAD_FOLDER`: 存放压制后的 MP4 文件的目录路径。支持绝对路径。
    *   `YAML_CONFIG_PATH`: 指向 Bilibili 投稿信息 YAML 文件的路径 (默认为 `config.yaml`)。
    *   `COOKIES_PATH`: 指向 Bilibili 登录 cookies 文件的路径 (默认为 `cookies.json`)。
    *   `MIN_FILE_SIZE_MB`: 清理小于此大小 (MB) 的 FLV 文件。
    *   `FONT_SIZE`, `SC_FONT_SIZE`: ASS 弹幕字体设置。
    *   `FFPROBE_PATH`, `FFMPEG_PATH`: FFprobe 和 FFmpeg 的路径 (如果不在系统 PATH 中，请填写绝对路径)。
    *   `SCHEDULE_INTERVAL_MINUTES`: 定时任务运行间隔（分钟）。
    *   `DELETE_UPLOADED_FILES`: 上传成功后是否删除 `UPLOAD_FOLDER` 中的 MP4 文件 (`True` 或 `False`)，默认为 `False` (不删除)。

2.  **`config.yaml` (根据 `YAML_CONFIG_PATH` 指定的路径创建):**
    *   包含 Bilibili 投稿所需信息，例如：
        ```yaml
        title: "【直播录像】{time} 弹幕版" # 视频标题模板, {time} 会被替换
        tid: 171         # B站分区 ID
        tag: "直播录像,游戏实况,XXX" # 标签, 逗号分隔
        source: "https://live.bilibili.com/xxxx" # 稿件来源 (自制也建议填写直播间地址)
        cover: ""       # 封面路径 (留空则自动截取)
        desc: |         # 视频简介 (支持多行)
          这是自动录制的直播录像。
          欢迎关注！
        dynamic: "发布了新的录播视频" # 发布动态的内容 (留空则不发)
        # cdn: "ws"      # 可选: 上传线路 (如 'ws', 'qn')，不填则使用 bilitool 默认
        ```
    *   **必需项:** `title`, `tid`, `tag`, `source`, `cover`, `dynamic`, `desc`。请确保这些键存在且值有效。

3.  **`cookies.json` (根据 `COOKIES_PATH` 指定的路径):**
    *   **极其重要！** 此文件包含 Bilibili 登录凭证。
    *   你需要使用 `bilitool` 提供的**登录功能** (通常是运行某个命令，然后扫码或输入账号密码) 来生成此文件。
    *   确保生成的文件位于正确的路径下。

## 使用方法

1.  **确保配置正确:** 仔细检查 `config.py` 和 `config.yaml` 中的所有设置，特别是路径和 Bilibili 相关信息。
2.  **生成 `cookies.json`:** 使用 `bilitool` 完成登录以生成 `cookies.json` 文件。
3.  **启动脚本:**
    在项目根目录下打开终端，运行：
    ```bash
    python main.py
    ```
4.  **运行逻辑:**
    *   脚本启动后会首先加载配置。
    *   然后立即执行一次完整的处理流程 (`job()` 函数)。
    *   之后会根据 `config.py` 中设置的 `SCHEDULE_INTERVAL_MINUTES` 定时重复执行 `job()` 函数。
    *   所有的操作和状态信息会打印到控制台日志中。
5.  **停止脚本:**
    在运行脚本的终端窗口按下 `Ctrl + C`。

## 后台运行

为了让脚本能够在你关闭终端或退出登录后持续运行，你需要将其放在后台执行。

**Linux / macOS:**

*   **使用 `nohup` (简单常用):**
    ```bash
    nohup python main.py > output.log 2>&1 &
    ```
    *   `nohup`: 使命令在退出终端后继续运行。
    *   `python main.py`: 你要运行的命令。
    *   `>`: 重定向标准输出。
    *   `output.log`: 保存标准输出日志的文件名。
    *   `2>&1`: 将标准错误 (stderr) 重定向到标准输出 (stdout)，这样错误信息也会记录到 `output.log`。
    *   `&`: 将命令放到后台执行。
    *   你需要通过 `tail -f output.log` 查看实时日志，或者用 `cat output.log` 查看全部。
    *   要停止 `nohup` 运行的进程，需要找到其进程 ID (PID) (`ps aux | grep main.py`)，然后使用 `kill <PID>`。

*   **使用 `screen` 或 `tmux` (更灵活):**
    这些是终端复用工具，可以创建持久化的会话。
    ```bash
    # 使用 screen
    screen -S bilibili_uploader # 创建一个名为 bilibili_uploader 的 screen 会话
    python main.py             # 在会话中运行脚本 (日志会直接显示)
    # 按 Ctrl+A 然后按 D 键分离会话 (脚本继续在后台运行)
    screen -r bilibili_uploader # 重新连接会话查看输出或停止 (Ctrl+C)

    # 使用 tmux (类似 screen)
    tmux new -s bilibili_uploader # 创建会话
    python main.py
    # 按 Ctrl+B 然后按 D 键分离
    tmux attach -t bilibili_uploader # 重新连接
    ```

**Windows:**

*   **使用 `start /B` (简单但不稳定):**
    ```cmd
    start /B python main.py > output.log 2>&1
    ```
    这会在后台启动一个进程，但关闭命令提示符窗口**可能**会终止该进程。

*   **使用 `pythonw.exe` (无窗口运行):**
    ```cmd
    pythonw.exe main.py
    ```
    这会使用无窗口的 Python 解释器运行脚本，脚本会在后台执行。缺点是你看不到任何直接的输出（除非脚本将日志明确写入文件），并且需要通过任务管理器来结束进程。

*   **使用 Windows 任务计划程序 (Task Scheduler) (推荐):**
    这是在 Windows 上最稳定可靠的方法，可以让脚本在系统启动时或按计划运行，即使用户未登录。
    1.  打开“任务计划程序”。
    2.  在右侧操作栏点击“创建基本任务”或“创建任务”。
    3.  **名称/描述:** 给任务起个名字，如 "Bilibili Auto Uploader"。
    4.  **触发器:** 设置何时运行（例如，“计算机启动时”或“每天”）。
    5.  **操作:** 选择“启动程序”。
    6.  **程序或脚本:** 填写 `python.exe` 的完整路径（或者如果 Python 在 PATH 中，可以直接写 `python.exe`）。
    7.  **添加参数(可选):** 填写 `main.py` 的完整路径。
    8.  **起始于(可选):** **非常重要！** 填写你的项目根目录的完整路径。这能确保脚本能正确找到 `config.py`, `config.yaml` 等相对路径文件。
    9.  **其他设置:** 在任务属性中，可以配置“只在用户登录时运行”或“不管用户是否登录都运行”（后者通常需要输入密码），以及配置电源选项等。
    10. 保存任务。你可以手动运行任务进行测试。

*   **使用第三方工具 (如 NSSM):**
    NSSM (Non-Sucking Service Manager) 可以将任何可执行文件（包括 Python 脚本）包装成 Windows 服务，实现开机自启和稳定运行。但这需要额外下载和配置 NSSM。

**后台运行注意事项:**

*   **路径问题:** 在后台运行时，脚本的当前工作目录可能不是你预期的项目目录。强烈建议在 `config.py` 中对 `PROCESSING_FOLDER`, `UPLOAD_FOLDER`, `YAML_CONFIG_PATH`, `COOKIES_PATH` 使用**绝对路径**，或者确保你的后台运行方式正确设置了工作目录（例如任务计划程序的“起始于”）。
*   **日志:** 确保你的日志配置 (在 `main.py` 开头) 能正常工作，并将日志输出到可访问的文件或控制台（如 `nohup` 的 `output.log`）。定期检查日志文件以监控脚本运行状态。
