#!/usr/bin/env python3
# 供沙箱或本地校验 meta-skills 自身：检查必要文件与配置。
from pathlib import Path

def main():
    base = Path(__file__).resolve().parent
    required = ["SKILL.md", "manager.py", "db_handler.py", "rank_store.py", "config.yaml"]
    missing = [f for f in required if not (base / f).exists()]
    if missing:
        raise SystemExit("missing files: " + ", ".join(missing))
    print("OK")

if __name__ == "__main__":
    main()
