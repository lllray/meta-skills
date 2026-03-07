# -*- coding: utf-8 -*-
"""
meta-skills - 每日定时任务
默认每天 21:00 执行一次：检索并安装推荐技能，上传本日使用与评分到 meta-skills-rank-lists。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

# 使用 manager 的 daily_run 与配置
BASE_DIR = Path(__file__).resolve().parent


def next_run_time(hour: int = 21, minute: int = 0) -> datetime:
    """返回下一次运行时刻的 datetime（本地时间）。"""
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target


def run_scheduler() -> None:
    """循环：等到 21:00 执行 daily_run，再等下一个 21:00。"""
    try:
        from manager import _load_config, daily_run
    except ImportError:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from manager import _load_config, daily_run

    config = _load_config()
    schedule = config.get("schedule", {})
    if not schedule.get("enabled", True):
        return
    hour = schedule.get("hour", 21)
    minute = schedule.get("minute", 0)

    while True:
        now = datetime.now()
        target = next_run_time(hour, minute)
        delta = (target - now).total_seconds()
        print(f"[meta-skills] 下次运行: {target.isoformat()} (约 {delta/3600:.1f} 小时后)")
        time.sleep(min(delta, 3600))
        now = datetime.now()
        if now.hour != hour or now.minute != minute:
            continue
        print(f"[meta-skills] 执行每日任务 @ {now.isoformat()}")
        try:
            result = daily_run(config)
            print(f"[meta-skills] 安装: {result.get('installed', [])}, 上传: {result.get('uploaded')}")
            if result.get("upload_error"):
                print(f"[meta-skills] 上传错误: {result['upload_error']}")
        except Exception as e:
            print(f"[meta-skills] 错误: {e}")
        time.sleep(60)


if __name__ == "__main__":
    run_scheduler()
