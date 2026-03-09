#!/bin/bash

# Douyu-to-Bilibili Suite Service Management Script
# 统一管理主服务和录制服务，带进程守护和日志管理

# 配置变量
SERVICE_NAME="douyu-to-bilibili-suite"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF="$SCRIPT_DIR/service.sh"
APP_MODULE="app:app"
HOST="0.0.0.0"
PORT="50009"
RECORDING_SCRIPT="recording_service.py"

# PID 文件（守护进程）
MAIN_SUPERVISOR_PID_FILE="$SCRIPT_DIR/${SERVICE_NAME}_main_supervisor.pid"
REC_SUPERVISOR_PID_FILE="$SCRIPT_DIR/${SERVICE_NAME}_rec_supervisor.pid"

# 日志文件
MAIN_LOG_FILE="$SCRIPT_DIR/${SERVICE_NAME}.log"
REC_LOG_FILE="$SCRIPT_DIR/${SERVICE_NAME}_recording.log"

# 守护进程配置
RESTART_DELAY=5          # 重启等待秒数
CRASH_WINDOW=60          # 崩溃检测窗口（秒）
MAX_CRASHES=5            # 窗口内最大崩溃次数
GRACEFUL_TIMEOUT=10      # 优雅退出超时（秒）

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 检查 uv 是否安装
check_uv() {
    if ! command -v uv &> /dev/null; then
        print_error "uv 未安装或不在 PATH 中"
        print_info "请先安装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
}

# 从 config.py 读取日志保留时间（小时），失败则返回 24
get_log_retention_hours() {
    local hours
    hours=$(cd "$SCRIPT_DIR" && uv run python -c "from config import DELETE_UPLOADED_FILES_DELAY_HOURS; print(DELETE_UPLOADED_FILES_DELAY_HOURS)" 2>/dev/null)
    if [ -z "$hours" ] || ! [[ "$hours" =~ ^[0-9]+$ ]]; then
        echo "24"
    else
        echo "$hours"
    fi
}

# 清理过期的归档日志文件
clean_old_logs() {
    local retention_hours
    retention_hours=$(get_log_retention_hours)
    local retention_minutes=$((retention_hours * 60))
    find "$SCRIPT_DIR" -maxdepth 1 -name "${SERVICE_NAME}*.log.*" -type f -mmin +"${retention_minutes}" -delete 2>/dev/null
}

# 日志按日期轮转（在守护循环内调用，stdout 已重定向到日志文件）
rotate_log_if_needed() {
    local log_file="$1"
    [ ! -f "$log_file" ] && return

    local file_date today
    if [[ "$(uname)" == "Darwin" ]]; then
        file_date=$(stat -f "%Sm" -t "%Y-%m-%d" "$log_file" 2>/dev/null)
    else
        file_date=$(date -r "$log_file" "+%Y-%m-%d" 2>/dev/null)
    fi
    today=$(date "+%Y-%m-%d")

    if [ -n "$file_date" ] && [ "$file_date" != "$today" ]; then
        local archive="${log_file}.${file_date}"
        mv "$log_file" "$archive"
        # 重新打开 stdout/stderr 到新日志文件
        exec >> "$log_file" 2>&1
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [SUPERVISOR] 日志轮转: $(basename "$log_file") -> $(basename "$archive")"
    fi
}

# 检查守护进程是否运行
is_supervisor_running() {
    local pid_file="$1"
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        else
            rm -f "$pid_file"
        fi
    fi
    return 1
}

# ============================================================
# 守护循环（内部命令 _supervise 调用）
# 参数: <标签> <日志文件> <PID文件> <命令...>
# ============================================================
_run_supervisor() {
    local label="$1"
    local log_file="$2"
    local pid_file="$3"
    shift 3
    local cmd=("$@")

    local child_pid=""
    local should_exit=false
    local crash_times=()

    # 信号处理：转发给子进程并退出，不触发自动重启
    _handle_signal() {
        should_exit=true
        if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] [SUPERVISOR] 收到停止信号，终止 ${label} (PID: $child_pid)"
            kill "$child_pid" 2>/dev/null
            local c=0
            while [ $c -lt $GRACEFUL_TIMEOUT ] && kill -0 "$child_pid" 2>/dev/null; do
                sleep 1
                c=$((c + 1))
            done
            if kill -0 "$child_pid" 2>/dev/null; then
                kill -9 "$child_pid" 2>/dev/null
            fi
        fi
        rm -f "$pid_file"
        exit 0
    }
    trap _handle_signal TERM INT

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [SUPERVISOR] 开始守护 ${label} (PID: $$)"

    while true; do
        # 日志轮转和清理
        rotate_log_if_needed "$log_file"
        clean_old_logs

        # 启动子进程
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [SUPERVISOR] 启动 ${label}..."
        "${cmd[@]}" &
        child_pid=$!

        # 等待子进程退出
        wait "$child_pid" 2>/dev/null
        local exit_code=$?
        child_pid=""

        [ "$should_exit" = true ] && break

        local now=$(date +%s)
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [SUPERVISOR] ${label} 退出 (code: $exit_code)"

        # 快速崩溃检测
        crash_times+=("$now")
        local window_start=$((now - CRASH_WINDOW))
        local filtered=()
        for t in "${crash_times[@]}"; do
            [ "$t" -ge "$window_start" ] && filtered+=("$t")
        done
        crash_times=("${filtered[@]}")

        if [ ${#crash_times[@]} -ge $MAX_CRASHES ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] [SUPERVISOR] ${label} 在 ${CRASH_WINDOW}s 内崩溃 ${#crash_times[@]} 次，停止自动重启"
            rm -f "$pid_file"
            exit 1
        fi

        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [SUPERVISOR] 等待 ${RESTART_DELAY}s 后重启 ${label}..."
        sleep "$RESTART_DELAY"
    done
}

# ============================================================
# 用户命令
# ============================================================

# 启动所有服务
start_service() {
    print_info "启动 ${SERVICE_NAME} 服务..."

    if is_supervisor_running "$MAIN_SUPERVISOR_PID_FILE" || is_supervisor_running "$REC_SUPERVISOR_PID_FILE"; then
        print_warning "服务已在运行中"
        is_supervisor_running "$MAIN_SUPERVISOR_PID_FILE" && print_info "  主服务守护 PID: $(cat "$MAIN_SUPERVISOR_PID_FILE")"
        is_supervisor_running "$REC_SUPERVISOR_PID_FILE" && print_info "  录制服务守护 PID: $(cat "$REC_SUPERVISOR_PID_FILE")"
        return 1
    fi

    check_uv

    cd "$SCRIPT_DIR" || { print_error "无法切换到项目目录: $SCRIPT_DIR"; exit 1; }
    [ ! -f "pyproject.toml" ] && { print_error "未找到 pyproject.toml"; exit 1; }
    [ ! -f "app.py" ] && { print_error "未找到 app.py"; exit 1; }
    [ ! -f "$RECORDING_SCRIPT" ] && { print_error "未找到 $RECORDING_SCRIPT"; exit 1; }

    # 启动前清理过期日志
    clean_old_logs

    # 启动主服务守护循环
    nohup "$SELF" _supervise main >> "$MAIN_LOG_FILE" 2>&1 &
    local main_pid=$!
    disown "$main_pid" 2>/dev/null
    echo "$main_pid" > "$MAIN_SUPERVISOR_PID_FILE"

    # 启动录制服务守护循环
    nohup "$SELF" _supervise recording >> "$REC_LOG_FILE" 2>&1 &
    local rec_pid=$!
    disown "$rec_pid" 2>/dev/null
    echo "$rec_pid" > "$REC_SUPERVISOR_PID_FILE"

    sleep 2

    local ok=true
    if is_supervisor_running "$MAIN_SUPERVISOR_PID_FILE"; then
        print_success "主服务启动成功 (守护 PID: $main_pid)"
    else
        print_error "主服务启动失败"
        ok=false
    fi

    if is_supervisor_running "$REC_SUPERVISOR_PID_FILE"; then
        print_success "录制服务启动成功 (守护 PID: $rec_pid)"
    else
        print_error "录制服务启动失败"
        ok=false
    fi

    if [ "$ok" = true ]; then
        print_info "服务地址: http://$HOST:$PORT"
        print_info "主服务日志: $MAIN_LOG_FILE"
        print_info "录制服务日志: $REC_LOG_FILE"
        return 0
    else
        return 1
    fi
}

# 停止单个守护进程
_stop_one() {
    local label="$1"
    local pid_file="$2"

    if ! is_supervisor_running "$pid_file"; then
        return 0
    fi

    local pid=$(cat "$pid_file")
    print_info "停止 ${label} (PID: $pid)..."

    kill "$pid" 2>/dev/null

    local c=0
    while [ $c -lt $GRACEFUL_TIMEOUT ] && kill -0 "$pid" 2>/dev/null; do
        sleep 1
        c=$((c + 1))
    done

    if kill -0 "$pid" 2>/dev/null; then
        print_warning "${label} 优雅停止超时，强制终止..."
        kill -9 "$pid" 2>/dev/null
        sleep 1
    fi

    rm -f "$pid_file"

    if ! kill -0 "$pid" 2>/dev/null; then
        print_success "${label} 已停止"
        return 0
    else
        print_error "${label} 停止失败"
        return 1
    fi
}

# 停止所有服务
stop_service() {
    print_info "停止 ${SERVICE_NAME} 服务..."

    if ! is_supervisor_running "$MAIN_SUPERVISOR_PID_FILE" && ! is_supervisor_running "$REC_SUPERVISOR_PID_FILE"; then
        print_warning "服务未运行"
        return 1
    fi

    local result=0
    _stop_one "主服务" "$MAIN_SUPERVISOR_PID_FILE" || result=1
    _stop_one "录制服务" "$REC_SUPERVISOR_PID_FILE" || result=1

    [ $result -eq 0 ] && print_success "所有服务已停止"
    return $result
}

# 重启所有服务
restart_service() {
    print_info "重启 ${SERVICE_NAME} 服务..."

    if is_supervisor_running "$MAIN_SUPERVISOR_PID_FILE" || is_supervisor_running "$REC_SUPERVISOR_PID_FILE"; then
        stop_service
        sleep 2
    else
        print_info "服务未运行，直接启动..."
    fi

    start_service
}

# 查看所有服务状态
status_service() {
    print_info "检查 ${SERVICE_NAME} 服务状态..."
    echo ""

    echo -e "${BLUE}--- 主服务 ---${NC}"
    if is_supervisor_running "$MAIN_SUPERVISOR_PID_FILE"; then
        local pid=$(cat "$MAIN_SUPERVISOR_PID_FILE")
        print_success "运行中 (守护 PID: $pid)"
        echo "  服务地址: http://$HOST:$PORT"
        echo "  日志: $MAIN_LOG_FILE"
        ps -p "$pid" -o pid,ppid,pcpu,pmem,etime,cmd 2>/dev/null || true
    else
        print_warning "未运行"
    fi

    echo ""

    echo -e "${BLUE}--- 录制服务 ---${NC}"
    if is_supervisor_running "$REC_SUPERVISOR_PID_FILE"; then
        local pid=$(cat "$REC_SUPERVISOR_PID_FILE")
        print_success "运行中 (守护 PID: $pid)"
        echo "  日志: $REC_LOG_FILE"
        ps -p "$pid" -o pid,ppid,pcpu,pmem,etime,cmd 2>/dev/null || true
    else
        print_warning "未运行"
    fi
}

# 查看所有服务日志
logs_service() {
    local lines=${2:-50}

    echo -e "${BLUE}=== 主服务日志 (最后 $lines 行) ===${NC}"
    if [ -f "$MAIN_LOG_FILE" ]; then
        tail -n "$lines" "$MAIN_LOG_FILE"
    else
        print_warning "日志不存在: $MAIN_LOG_FILE"
    fi

    echo ""

    echo -e "${BLUE}=== 录制服务日志 (最后 $lines 行) ===${NC}"
    if [ -f "$REC_LOG_FILE" ]; then
        tail -n "$lines" "$REC_LOG_FILE"
    else
        print_warning "日志不存在: $REC_LOG_FILE"
    fi
}

# 显示帮助
show_help() {
    echo "Usage: $0 {start|stop|restart|status|logs} [options]"
    echo ""
    echo "Commands:"
    echo "  start      启动所有服务（主服务 + 录制服务），带进程守护"
    echo "  stop       停止所有服务"
    echo "  restart    重启所有服务"
    echo "  status     查看所有服务状态"
    echo "  logs [N]   查看日志 (默认各50行)"
    echo ""
    echo "Examples:"
    echo "  $0 start"
    echo "  $0 status"
    echo "  $0 logs 100"
    echo ""
    echo "Configuration:"
    echo "  HOST: $HOST"
    echo "  PORT: $PORT"
    echo "  主服务日志: $MAIN_LOG_FILE"
    echo "  录制服务日志: $REC_LOG_FILE"
}

# ============================================================
# 主逻辑
# ============================================================
case "$1" in
    _supervise)
        # 内部命令：由 start_service 调用，启动守护循环
        cd "$SCRIPT_DIR" || exit 1
        case "$2" in
            main)
                _run_supervisor "主服务" "$MAIN_LOG_FILE" "$MAIN_SUPERVISOR_PID_FILE" \
                    uv run uvicorn "$APP_MODULE" --host "$HOST" --port "$PORT" --log-level info
                ;;
            recording)
                _run_supervisor "录制服务" "$REC_LOG_FILE" "$REC_SUPERVISOR_PID_FILE" \
                    uv run python "$RECORDING_SCRIPT"
                ;;
        esac
        ;;
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    restart)
        restart_service
        ;;
    status)
        status_service
        ;;
    logs)
        logs_service "$@"
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        print_error "未知命令: $1"
        echo ""
        show_help
        exit 1
        ;;
esac

exit $?
