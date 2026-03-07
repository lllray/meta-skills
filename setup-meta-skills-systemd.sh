#!/usr/bin/env bash
# meta-skills systemd 一键管理脚本
#
# 功能：
#   1. 安装并启动每日定时任务（systemd user timer）
#   2. 卸载并清理 meta-skills 的 systemd 单元
#   3. 查看当前状态与下次执行时间
#
# 用法:
#   ./setup-meta-skills-systemd.sh install    # 安装并启动 meta-skills 每日任务
#   ./setup-meta-skills-systemd.sh uninstall  # 卸载 meta-skills 每日任务
#   ./setup-meta-skills-systemd.sh status     # 查看状态与下次执行时间

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_SYSTEMD_DIR="${HOME}/.config/systemd/user"
TIMER_UNIT="meta-skills-daily.timer"
SERVICE_UNIT="meta-skills-daily.service"

install_meta_skills_timer() {
    mkdir -p "${USER_SYSTEMD_DIR}"

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  📦 安装 meta-skills 每日定时任务（systemd user）"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # 确保在 meta-skills 项目根目录调用 manager.py
    cd "${SCRIPT_DIR}"

    # 由 manager.py 负责写入 unit 文件并 enable --now
    python3 manager.py schedule install

    # 启用 lingering（允许用户服务在未登录时运行），若系统支持
    if command -v loginctl &> /dev/null; then
        loginctl enable-linger "$(whoami)" 2>/dev/null || true
    fi

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ✅ 安装完成"
    echo ""
    echo "  - 单元目录: ${USER_SYSTEMD_DIR}"
    echo "  - 定时器:   ${TIMER_UNIT}"
    echo ""
    echo "  查看状态:   $0 status"
    echo "  手动执行:   systemctl --user start ${SERVICE_UNIT}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

uninstall_meta_skills_timer() {
    echo "🗑️  卸载 meta-skills 每日定时任务..."

    systemctl --user disable --now "${TIMER_UNIT}" 2>/dev/null || true
    systemctl --user stop "${SERVICE_UNIT}" 2>/dev/null || true

    rm -f "${USER_SYSTEMD_DIR}/${TIMER_UNIT}"
    rm -f "${USER_SYSTEMD_DIR}/${SERVICE_UNIT}"

    systemctl --user daemon-reload || true

    echo "✅ 已卸载 meta-skills systemd timer 与 service（不影响已安装的 skills）"
}

show_status() {
    echo "━━━━━━━━ meta-skills 定时任务状态 ━━━━━━━━"
    systemctl --user status "${TIMER_UNIT}" --no-pager 2>/dev/null || echo "  定时器未安装"
    echo ""
    systemctl --user status "${SERVICE_UNIT}" --no-pager 2>/dev/null || echo "  服务未安装"
    echo ""
    echo "━━━━━━━━ 下次执行时间（若已安装） ━━━━━━━━"
    systemctl --user list-timers "${TIMER_UNIT}" --no-pager 2>/dev/null || echo "  无已注册的 meta-skills 定时器"
}

case "${1:-}" in
    install)
        install_meta_skills_timer
        ;;
    uninstall)
        uninstall_meta_skills_timer
        ;;
    status)
        show_status
        ;;
    *)
        echo "用法: $0 {install|uninstall|status}"
        exit 1
        ;;
esac

