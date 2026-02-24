#!/bin/bash

# Video Processor Service Management Script
# 使用 uv 管理的 Python 项目服务脚本

# 配置变量
SERVICE_NAME="video_processor"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_MODULE="app:app"
PID_FILE="$SCRIPT_DIR/${SERVICE_NAME}.pid"
LOG_FILE="$SCRIPT_DIR/${SERVICE_NAME}.log"

# Recording service (optional, standalone process)
RECORDING_SERVICE_NAME="${SERVICE_NAME}_recording"
RECORDING_SCRIPT="recording_service.py"
RECORDING_PID_FILE="$SCRIPT_DIR/${RECORDING_SERVICE_NAME}.pid"
RECORDING_LOG_FILE="$SCRIPT_DIR/${RECORDING_SERVICE_NAME}.log"

# 默认配置 (可根据需要修改)
HOST="0.0.0.0"
PORT="50009"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的信息
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查 uv 是否安装
check_uv() {
    if ! command -v uv &> /dev/null; then
        print_error "uv 未安装或不在 PATH 中"
        print_info "请先安装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
}

# 检查进程是否运行
is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            return 0
        else
            # PID 文件存在但进程不存在，清理 PID 文件
            rm -f "$PID_FILE"
            return 1
        fi
    fi
    return 1
}

# 检查录制进程是否运行
is_recording_running() {
    if [ -f "$RECORDING_PID_FILE" ]; then
        local pid=$(cat "$RECORDING_PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            return 0
        else
            rm -f "$RECORDING_PID_FILE"
            return 1
        fi
    fi
    return 1
}

# 获取进程 PID
get_pid() {
    if [ -f "$PID_FILE" ]; then
        cat "$PID_FILE"
    else
        echo ""
    fi
}

# 获取录制进程 PID
get_recording_pid() {
    if [ -f "$RECORDING_PID_FILE" ]; then
        cat "$RECORDING_PID_FILE"
    else
        echo ""
    fi
}

# 启动服务
start_service() {
    print_info "启动 $SERVICE_NAME 服务..."
    
    # 检查服务是否已经运行
    if is_running; then
        local pid=$(get_pid)
        print_warning "服务已经在运行中 (PID: $pid)"
        return 1
    fi
    
    # 检查 uv
    check_uv
    
    # 切换到项目目录
    cd "$SCRIPT_DIR" || {
        print_error "无法切换到项目目录: $SCRIPT_DIR"
        exit 1
    }
    
    # 检查项目文件
    if [ ! -f "pyproject.toml" ]; then
        print_error "未找到 pyproject.toml 文件，请确认当前目录是正确的项目根目录"
        exit 1
    fi
    
    if [ ! -f "app.py" ]; then
        print_error "未找到 app.py 文件"
        exit 1
    fi
    
    # 启动服务 (后台运行)
    print_info "使用 uv 启动 FastAPI 应用..."
    nohup uv run uvicorn "$APP_MODULE" \
        --host "$HOST" \
        --port "$PORT" \
        --log-level info \
        >> "$LOG_FILE" 2>&1 &
    
    local pid=$!
    echo $pid > "$PID_FILE"
    
    # 等待一下确认服务启动成功
    sleep 2
    
    if is_running; then
        print_success "服务启动成功 (PID: $pid)"
        print_info "服务地址: http://$HOST:$PORT"
        print_info "日志文件: $LOG_FILE"
        return 0
    else
        print_error "服务启动失败"
        if [ -f "$LOG_FILE" ]; then
            print_error "最新日志信息："
            tail -n 10 "$LOG_FILE"
        fi
        return 1
    fi
}

# 启动录制服务
start_recording_service() {
    print_info "启动 $RECORDING_SERVICE_NAME 服务..."

    if is_recording_running; then
        local pid=$(get_recording_pid)
        print_warning "录制服务已经在运行中 (PID: $pid)"
        return 1
    fi

    check_uv

    cd "$SCRIPT_DIR" || {
        print_error "无法切换到项目目录: $SCRIPT_DIR"
        exit 1
    }

    if [ ! -f "$RECORDING_SCRIPT" ]; then
        print_error "未找到 $RECORDING_SCRIPT 文件"
        exit 1
    fi

    print_info "使用 uv 启动录制服务..."
    nohup uv run python "$RECORDING_SCRIPT" >> "$RECORDING_LOG_FILE" 2>&1 &

    local pid=$!
    echo $pid > "$RECORDING_PID_FILE"

    sleep 2

    if is_recording_running; then
        print_success "录制服务启动成功 (PID: $pid)"
        print_info "日志文件: $RECORDING_LOG_FILE"
        return 0
    else
        print_error "录制服务启动失败"
        if [ -f "$RECORDING_LOG_FILE" ]; then
            print_error "最新日志信息："
            tail -n 10 "$RECORDING_LOG_FILE"
        fi
        return 1
    fi
}

# 停止服务
stop_service() {
    print_info "停止 $SERVICE_NAME 服务..."
    
    if ! is_running; then
        print_warning "服务未运行"
        return 1
    fi
    
    local pid=$(get_pid)
    print_info "正在停止服务 (PID: $pid)..."
    
    # 优雅停止
    kill "$pid" 2>/dev/null
    
    # 等待进程结束
    local count=0
    while [ $count -lt 10 ] && ps -p "$pid" > /dev/null 2>&1; do
        sleep 1
        count=$((count + 1))
    done
    
    # 如果进程仍然存在，强制杀死
    if ps -p "$pid" > /dev/null 2>&1; then
        print_warning "优雅停止失败，强制终止进程..."
        kill -9 "$pid" 2>/dev/null
        sleep 1
    fi
    
    # 清理 PID 文件
    rm -f "$PID_FILE"
    
    if ! ps -p "$pid" > /dev/null 2>&1; then
        print_success "服务已停止"
        return 0
    else
        print_error "服务停止失败"
        return 1
    fi
}

# 停止录制服务
stop_recording_service() {
    print_info "停止 $RECORDING_SERVICE_NAME 服务..."

    if ! is_recording_running; then
        print_warning "录制服务未运行"
        return 1
    fi

    local pid=$(get_recording_pid)
    print_info "正在停止录制服务 (PID: $pid)..."

    kill "$pid" 2>/dev/null

    local count=0
    while [ $count -lt 10 ] && ps -p "$pid" > /dev/null 2>&1; do
        sleep 1
        count=$((count + 1))
    done

    if ps -p "$pid" > /dev/null 2>&1; then
        print_warning "优雅停止失败，强制终止进程..."
        kill -9 "$pid" 2>/dev/null
        sleep 1
    fi

    rm -f "$RECORDING_PID_FILE"

    if ! ps -p "$pid" > /dev/null 2>&1; then
        print_success "录制服务已停止"
        return 0
    else
        print_error "录制服务停止失败"
        return 1
    fi
}

# 重启服务
restart_service() {
    print_info "重启 $SERVICE_NAME 服务..."
    
    if is_running; then
        stop_service
        if [ $? -eq 0 ]; then
            sleep 2
            start_service
        else
            print_error "停止服务失败，无法重启"
            return 1
        fi
    else
        print_info "服务未运行，直接启动..."
        start_service
    fi
}

# 重启录制服务
restart_recording_service() {
    print_info "重启 $RECORDING_SERVICE_NAME 服务..."

    if is_recording_running; then
        stop_recording_service
        if [ $? -eq 0 ]; then
            sleep 2
            start_recording_service
        else
            print_error "停止录制服务失败，无法重启"
            return 1
        fi
    else
        print_info "录制服务未运行，直接启动..."
        start_recording_service
    fi
}

# 查看服务状态
status_service() {
    print_info "检查 $SERVICE_NAME 服务状态..."
    
    if is_running; then
        local pid=$(get_pid)
        print_success "服务正在运行"
        echo "  PID: $pid"
        echo "  服务地址: http://$HOST:$PORT"
        echo "  项目目录: $SCRIPT_DIR"
        echo "  日志文件: $LOG_FILE"
        
        # 显示进程信息
        if command -v ps &> /dev/null; then
            echo "  进程信息:"
            ps -p "$pid" -o pid,ppid,pcpu,pmem,etime,cmd 2>/dev/null || echo "    无法获取进程详细信息"
        fi
        
        # 检查端口占用
        if command -v netstat &> /dev/null; then
            echo "  端口监听:"
            netstat -tlnp 2>/dev/null | grep ":$PORT " || echo "    端口信息不可用"
        elif command -v ss &> /dev/null; then
            echo "  端口监听:"
            ss -tlnp | grep ":$PORT " || echo "    端口信息不可用"
        fi
        
        return 0
    else
        print_warning "服务未运行"
        
        # 检查是否有残留的 PID 文件
        if [ -f "$PID_FILE" ]; then
            print_warning "发现残留的 PID 文件，已清理"
            rm -f "$PID_FILE"
        fi
        
        return 1
    fi
}

# 查看录制服务状态
status_recording_service() {
    print_info "检查 $RECORDING_SERVICE_NAME 服务状态..."

    if is_recording_running; then
        local pid=$(get_recording_pid)
        print_success "录制服务正在运行"
        echo "  PID: $pid"
        echo "  项目目录: $SCRIPT_DIR"
        echo "  日志文件: $RECORDING_LOG_FILE"

        if command -v ps &> /dev/null; then
            echo "  进程信息:"
            ps -p "$pid" -o pid,ppid,pcpu,pmem,etime,cmd 2>/dev/null || echo "    无法获取进程详细信息"
        fi
        return 0
    else
        print_warning "录制服务未运行"

        if [ -f "$RECORDING_PID_FILE" ]; then
            print_warning "发现残留的 PID 文件，已清理"
            rm -f "$RECORDING_PID_FILE"
        fi
        return 1
    fi
}

# 查看日志
logs_service() {
    print_info "查看 $SERVICE_NAME 服务日志..."
    
    local lines=${2:-50}  # 默认显示最后50行
    
    if [ -f "$LOG_FILE" ]; then
        print_info "=== 服务日志 (最后 $lines 行) ==="
        tail -n "$lines" "$LOG_FILE"
    else
        print_warning "日志文件不存在: $LOG_FILE"
    fi
}

# 查看录制服务日志
logs_recording_service() {
    print_info "查看 $RECORDING_SERVICE_NAME 服务日志..."

    local lines=${2:-50}
    if [ -f "$RECORDING_LOG_FILE" ]; then
        print_info "=== 录制服务日志 (最后 $lines 行) ==="
        tail -n "$lines" "$RECORDING_LOG_FILE"
    else
        print_warning "日志文件不存在: $RECORDING_LOG_FILE"
    fi
}

# 显示帮助信息
show_help() {
    echo "Usage: $0 {start|stop|restart|status|logs|start-recording|stop-recording|restart-recording|status-recording|logs-recording} [options]"
    echo ""
    echo "Commands:"
    echo "  start      启动服务"
    echo "  stop       停止服务"
    echo "  restart    重启服务"
    echo "  status     查看服务状态"
    echo "  logs [N]   查看日志 (可选：指定行数，默认50行)"
    echo "  start-recording      启动录制服务"
    echo "  stop-recording       停止录制服务"
    echo "  restart-recording    重启录制服务"
    echo "  status-recording     查看录制服务状态"
    echo "  logs-recording [N]   查看录制服务日志 (可选：指定行数，默认50行)"
    echo ""
    echo "Examples:"
    echo "  $0 start"
    echo "  $0 status"
    echo "  $0 logs 100"
    echo "  $0 start-recording"
    echo "  $0 logs-recording 100"
    echo ""
    echo "Configuration:"
    echo "  HOST: $HOST"
    echo "  PORT: $PORT"
    echo "  PID_FILE: $PID_FILE"
    echo "  LOG_FILE: $LOG_FILE"
    echo "  RECORDING_PID_FILE: $RECORDING_PID_FILE"
    echo "  RECORDING_LOG_FILE: $RECORDING_LOG_FILE"
}

# 主逻辑
case "$1" in
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
    start-recording)
        start_recording_service
        ;;
    stop-recording)
        stop_recording_service
        ;;
    restart-recording)
        restart_recording_service
        ;;
    status-recording)
        status_recording_service
        ;;
    logs-recording)
        logs_recording_service "$@"
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
