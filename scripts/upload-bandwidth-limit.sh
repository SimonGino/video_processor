#!/usr/bin/env bash
#
# 上传带宽限速脚本 — 使用 tc + cgroup v2 仅限制 biliup 进程的上传速度
#
# 用法:
#   sudo ./upload-bandwidth-limit.sh setup 10mbit   # 设置限速 10Mbps
#   sudo ./upload-bandwidth-limit.sh setup 5mbit    # 设置限速 5Mbps
#   sudo ./upload-bandwidth-limit.sh status          # 查看当前限速状态
#   sudo ./upload-bandwidth-limit.sh teardown        # 移除限速
#
# 注意:
#   - 需要 root 权限
#   - 需要 cgroup v2（内核 4.15+，大多数现代发行版默认启用）
#   - 仅限制上传(出站)流量，不影响下载和其他进程

set -euo pipefail

CGROUP_NAME="biliup-limit"
CGROUP_PATH="/sys/fs/cgroup/${CGROUP_NAME}"
MARK="0x10"
IFACE=""

die() { echo "错误: $*" >&2; exit 1; }

detect_iface() {
    # 自动检测默认出口网卡
    IFACE=$(ip route show default | awk '/default/ {print $5; exit}')
    [ -n "$IFACE" ] || die "无法检测默认网卡，请手动设置 IFACE 变量"
}

check_root() {
    [ "$(id -u)" -eq 0 ] || die "需要 root 权限，请使用 sudo"
}

check_cgroup_v2() {
    mount | grep -q "cgroup2" || die "未检测到 cgroup v2，请确认内核版本 >= 4.15"
}

setup() {
    local rate="${1:-10mbit}"
    check_root
    check_cgroup_v2
    detect_iface

    echo "=== 上传限速设置 ==="
    echo "网卡: ${IFACE}"
    echo "限速: ${rate}"
    echo "cgroup: ${CGROUP_PATH}"
    echo ""

    # 1. 清理已有规则（忽略错误）
    teardown_quiet

    # 2. 创建 cgroup
    mkdir -p "${CGROUP_PATH}"
    echo "✓ 创建 cgroup: ${CGROUP_PATH}"

    # 3. iptables 标记 cgroup 内进程的出站包
    iptables -t mangle -A OUTPUT -m cgroup --path "${CGROUP_NAME}" -j MARK --set-mark "${MARK}"
    echo "✓ 添加 iptables 标记规则 (mark=${MARK})"

    # 4. tc 限速：只对带标记的流量限速，其他流量走默认不限速
    tc qdisc add dev "${IFACE}" root handle 1: htb default 99
    tc class add dev "${IFACE}" parent 1: classid 1:1 htb rate "${rate}" ceil "${rate}"
    tc class add dev "${IFACE}" parent 1: classid 1:99 htb rate 1000mbit ceil 1000mbit
    tc filter add dev "${IFACE}" parent 1: protocol ip handle "${MARK}" fw flowid 1:1
    echo "✓ 添加 tc 限速规则 (rate=${rate})"

    echo ""
    echo "=== 设置完成 ==="
    echo ""
    echo "使用方式: 在启动 biliup 之前，将进程加入 cgroup:"
    echo ""
    echo "  # 方式1: 将当前 shell 及其子进程加入 cgroup"
    echo "  echo \$\$ > ${CGROUP_PATH}/cgroup.procs"
    echo "  biliup upload ..."
    echo ""
    echo "  # 方式2: 用 cgexec 直接启动（需要 cgroup-tools）"
    echo "  cgexec -g :${CGROUP_NAME} biliup upload ..."
    echo ""
    echo "  # 方式3: 在代码中将子进程 PID 写入 cgroup"
    echo "  echo <PID> > ${CGROUP_PATH}/cgroup.procs"
}

teardown_quiet() {
    local iface
    iface=$(ip route show default | awk '/default/ {print $5; exit}' 2>/dev/null)

    if [ -n "$iface" ]; then
        tc qdisc del dev "$iface" root 2>/dev/null || true
        iptables -t mangle -D OUTPUT -m cgroup --path "${CGROUP_NAME}" -j MARK --set-mark "${MARK}" 2>/dev/null || true
    fi
    [ -d "${CGROUP_PATH}" ] && rmdir "${CGROUP_PATH}" 2>/dev/null || true
}

teardown() {
    check_root
    teardown_quiet
    echo "✓ 已移除所有限速规则"
}

status() {
    detect_iface
    echo "=== tc 规则 ==="
    tc -s qdisc show dev "${IFACE}" 2>/dev/null || echo "(无 tc 规则)"
    echo ""
    tc -s class show dev "${IFACE}" 2>/dev/null || true
    echo ""
    echo "=== iptables 标记规则 ==="
    iptables -t mangle -L OUTPUT -n -v 2>/dev/null | grep -E "cgroup|MARK" || echo "(无 cgroup 标记规则)"
    echo ""
    echo "=== cgroup 内进程 ==="
    if [ -f "${CGROUP_PATH}/cgroup.procs" ]; then
        local pids
        pids=$(cat "${CGROUP_PATH}/cgroup.procs" 2>/dev/null)
        if [ -n "$pids" ]; then
            echo "$pids" | while read -r pid; do
                ps -p "$pid" -o pid,comm,args --no-headers 2>/dev/null || echo "PID $pid (已退出)"
            done
        else
            echo "(cgroup 内无进程)"
        fi
    else
        echo "(cgroup 不存在，未设置限速)"
    fi
}

case "${1:-help}" in
    setup)    setup "${2:-10mbit}" ;;
    teardown) teardown ;;
    status)   status ;;
    *)
        echo "用法: sudo $0 {setup <rate>|teardown|status}"
        echo ""
        echo "示例:"
        echo "  sudo $0 setup 10mbit    # 限速 10Mbps"
        echo "  sudo $0 setup 50mbit    # 限速 50Mbps"
        echo "  sudo $0 status          # 查看状态"
        echo "  sudo $0 teardown        # 移除限速"
        ;;
esac
