# -*- coding: utf-8 -*-
"""
meta-skills - 本地排名 JSON 与 meta-skills-rank-lists 同步
打分来源：用户调用次数。本地 JSON 记录，每日统一上传到用户 GitHub 仓库 meta-skills-rank-lists。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

# 本地 JSON 文件名
RANK_JSON = "rank_data.json"
BASE_DIR = Path(__file__).resolve().parent


def _rank_json_path(base_dir: Optional[Path] = None) -> Path:
    return (base_dir or BASE_DIR) / RANK_JSON


def load_rank_data(base_dir: Optional[Path] = None) -> dict:
    path = _rank_json_path(base_dir)
    if not path.exists():
        return {"keywords": [], "skills": [], "last_upload": None, "updated_at": None}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"keywords": [], "skills": [], "last_upload": None, "updated_at": None}


def save_rank_data(data: dict, base_dir: Optional[Path] = None) -> None:
    path = _rank_json_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.utcnow().isoformat() + "Z"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_skill_entry(data: dict, name: str, source_url: str = "", installed_at: Optional[str] = None) -> dict:
    """确保 skills 列表中有该技能条目，返回该条目。"""
    skills = data.get("skills", [])
    for s in skills:
        if s.get("name") == name:
            if source_url:
                s["source_url"] = source_url
            if installed_at:
                s["installed_at"] = installed_at
            return s
    entry = {
        "name": name,
        "source_url": source_url or "",
        "installed_at": installed_at or datetime.utcnow().isoformat() + "Z",
        "use_count": 0,
        "edit_count": 0,
    }
    skills.append(entry)
    data["skills"] = skills
    return entry


def record_use(base_dir: Optional[Path] = None, skill_name: str = "", source_url: str = "") -> None:
    """记录一次技能调用（打分目前唯一来源）。"""
    data = load_rank_data(base_dir)
    ensure_skill_entry(data, skill_name, source_url=source_url)
    for s in data["skills"]:
        if s["name"] == skill_name:
            s["use_count"] = s.get("use_count", 0) + 1
            break
    save_rank_data(data, base_dir)


def record_edit(base_dir: Optional[Path] = None, skill_name: str = "", source_url: str = "") -> None:
    """记录用户对技能的修改次数。"""
    data = load_rank_data(base_dir)
    ensure_skill_entry(data, skill_name, source_url=source_url)
    for s in data["skills"]:
        if s["name"] == skill_name:
            s["edit_count"] = s.get("edit_count", 0) + 1
            break
    save_rank_data(data, base_dir)


def set_keywords(keywords: list[str], base_dir: Optional[Path] = None) -> None:
    """设置用户关键词（用于 README 展示）。"""
    data = load_rank_data(base_dir)
    data["keywords"] = list(keywords)
    save_rank_data(data, base_dir)


def get_keywords(base_dir: Optional[Path] = None) -> list[str]:
    return load_rank_data(base_dir).get("keywords", [])


def build_readme(data: dict, repo_full_name: str = "meta-skills-rank-lists") -> str:
    """生成 meta-skills-rank-lists 仓库的 README 内容。"""
    keywords = data.get("keywords", [])
    skills = data.get("skills", [])
    total = len(skills)
    if not skills:
        avg_rank = 0
    else:
        use_counts = [s.get("use_count", 0) for s in skills]
        avg_rank = sum(use_counts) / len(use_counts) if use_counts else 0

    lines = [
        "# meta-skills-rank-lists",
        "",
        "由 [meta-skills](https://github.com/yourname/meta-skills) 自动维护的 OpenClaw 技能使用与排名数据。",
        "",
        "## 用户关键词",
        "",
    ]
    if keywords:
        lines.append(", ".join(f"`{k}`" for k in keywords))
    else:
        lines.append("（暂无）")
    lines.extend([
        "",
        "## 统计概览",
        "",
        "| 项目 | 数值 |",
        "|------|------|",
        f"| 已自动安装技能数 | {total} |",
        f"| 技能平均使用次数（作为排名依据） | {avg_rank:.1f} |",
        "",
        "## Skill 排名表",
        "",
        "| Skill 名称 | 原 GitHub 链接 | 用户修改次数 | 用户使用次数 |",
        "|------------|----------------|--------------|--------------|",
    ])
    # 按使用次数降序
    sorted_skills = sorted(skills, key=lambda s: s.get("use_count", 0), reverse=True)
    for s in sorted_skills:
        name = s.get("name", "")
        url = s.get("source_url", "")
        link = f"[{name}]({url})" if url else name
        edit = s.get("edit_count", 0)
        use = s.get("use_count", 0)
        lines.append(f"| {name} | {link} | {edit} | {use} |")
    lines.extend(["", "---", "", f"*最后更新: {data.get('updated_at', '')}*"])
    return "\n".join(lines)


def push_to_github(
    repo: str,
    token: str,
    base_dir: Optional[Path] = None,
    data: Optional[dict] = None,
) -> tuple[bool, str]:
    """将当前 rank_data 与生成的 README 推送到用户仓库 username/meta-skills-rank-lists。"""
    if not repo or "/" not in repo:
        return False, "rank_lists_repo 未配置（应为 username/meta-skills-rank-lists）"
    data = data or load_rank_data(base_dir)
    readme = build_readme(data, repo)
    url = f"https://github.com/{repo}.git"
    if token:
        url = url.replace("https://", f"https://{token}@")
    env = os.environ.copy()
    if token:
        env["GH_TOKEN"] = token
    try:
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            clone_ok = subprocess.run(
                ["git", "clone", "--depth", "1", url, str(t)],
                capture_output=True,
                timeout=60,
                env=env,
            ).returncode == 0
            if not clone_ok:
                subprocess.run(["git", "init", "-b", "main"], cwd=t, check=True, capture_output=True, env=env)
                subprocess.run(["git", "remote", "add", "origin", url], cwd=t, check=True, capture_output=True, env=env)
            (t / RANK_JSON).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            (t / "README.md").write_text(readme, encoding="utf-8")
            subprocess.run(["git", "add", RANK_JSON, "README.md"], cwd=t, check=True, capture_output=True, env=env)
            subprocess.run(
                ["git", "commit", "-m", "chore: update rank data and README (meta-skills daily sync)"],
                cwd=t,
                capture_output=True,
                env=env,
            )
            push_cmd = ["git", "push", "-u", "origin", "main"] if not clone_ok else ["git", "push", "origin", "HEAD"]
            subprocess.run(push_cmd, cwd=t, check=True, capture_output=True, timeout=30, env=env)
        return True, "pushed"
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or e.stdout or str(e)).decode("utf-8", errors="replace")[:500]
    except Exception as e:
        return False, str(e)


def fetch_rank_list_from_github(repo: str, token: str = "") -> list[dict]:
    """从指定 meta-skills-rank-lists 仓库拉取 rank_data.json，返回 skills 列表（用于发现高使用量技能）。"""
    url = f"https://raw.githubusercontent.com/{repo}/main/{RANK_JSON}"
    if "/" not in repo:
        return []
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return data.get("skills", [])
    except Exception:
        try:
            url = f"https://api.github.com/repos/{repo}/contents/{RANK_JSON}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                import base64
                d = json.loads(resp.read().decode())
                raw = base64.b64decode(d["content"]).decode()
                return json.loads(raw).get("skills", [])
        except Exception:
            return []
