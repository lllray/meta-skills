#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 Skill 使用与更新记录：record / record_edit → rank_data.json + SQLite → usage_stats / scores
运行：在项目根目录执行  python tests/test_usage_recording.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run(*args: str) -> str:
    r = subprocess.run(
        [sys.executable, str(ROOT / "manager.py")] + list(args),
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return (r.stdout or "").strip() + (("\n" + (r.stderr or "").strip()) if r.stderr else "")


def main():
    print("=== 1. 当前使用/更新统计（usage_stats）===\n")
    out = run("usage_stats")
    print(out)
    data_before = json.loads(out) if out.startswith("[") else []

    print("\n=== 2. 模拟多次调用：record team-tasks 3 次，github-explorer 2 次 ===\n")
    for _ in range(3):
        run("record", "team-tasks", "https://github.com/win4r/team-tasks")
    for _ in range(2):
        run("record", "github-explorer", "https://github.com/blessonism/github-explorer-skill")
    print("recorded 3x team-tasks, 2x github-explorer")

    print("\n=== 3. 再次查看 usage_stats ===\n")
    out = run("usage_stats")
    print(out)

    print("\n=== 4. 模拟用户修改一次 skill：record_edit team-tasks ===\n")
    run("record_edit", "team-tasks", "https://github.com/win4r/team-tasks")
    print("record_edit recorded")

    print("\n=== 5. 最终 usage_stats ===\n")
    out = run("usage_stats")
    print(out)

    print("\n=== 6. 评分（scores）：基于 SQLite invocations + feedback ===\n")
    out = run("scores")
    print(out)

    print("\n=== 7. 查看 rank_data.json 中 skills 片段 ===\n")
    rank_file = ROOT / "rank_data.json"
    if rank_file.exists():
        d = json.loads(rank_file.read_text(encoding="utf-8"))
        for s in d.get("skills", []):
            if s["name"] in ("team-tasks", "github-explorer"):
                print(json.dumps(s, ensure_ascii=False, indent=2))
    print("\n测试完成。使用情况写入 rank_data.json（use_count/edit_count）与 meta_skills.db（invocations），每日可 upload_rank 同步到 meta-skills-rank-lists。")


if __name__ == "__main__":
    main()
