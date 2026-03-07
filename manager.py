# -*- coding: utf-8 -*-
"""
meta-skills - 核心逻辑
发现（GitHub + meta-skills-rank-lists）、安装、本地 JSON 记录使用、每日上传排名、每日 21:00 定时任务。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml

from db_handler import DBHandler, SkillScore
from rank_store import (
    add_keywords,
    ensure_skill_entry,
    fetch_rank_list_from_github,
    get_keywords,
    load_rank_data,
    push_to_github,
    record_edit,
    record_use,
    save_rank_data,
    set_keywords,
    update_keywords,
)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.yaml"
LOCAL_CONFIG_PATH = BASE_DIR / "config.local.yaml"  # 用户 token、repo 等，不提交
DEFAULT_SKILLS_DIR = Path.home() / ".openclaw" / "skills"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_config(path: Optional[Path] = None) -> dict:
    path = path or DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    raw = os.path.expandvars(raw)
    cfg = yaml.safe_load(raw) or {}
    if LOCAL_CONFIG_PATH.exists():
        with open(LOCAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            local_raw = f.read()
        local_raw = os.path.expandvars(local_raw)
        local_cfg = yaml.safe_load(local_raw) or {}
        cfg = _deep_merge(cfg, local_cfg)
    return cfg


def _set_config_key(key: str, value: str | int | bool | list) -> None:
    """支持嵌套键，如 rank_lists.repo、schedule.hour、github.token。写入 config.local.yaml。"""
    parts = key.split(".")
    data: dict = {}
    if LOCAL_CONFIG_PATH.exists():
        try:
            with open(LOCAL_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            data = {}
    cur = data
    for i, p in enumerate(parts[:-1]):
        if p not in cur or not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value
    LOCAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCAL_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _get_config_key(key: str, config: Optional[dict] = None) -> Optional[str | int | bool | list]:
    config = config or _load_config()
    parts = key.split(".")
    cur = config
    for p in parts:
        cur = cur.get(p) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


def create_github_repo(token: str, repo_name: str, private: bool = False) -> tuple[bool, str]:
    """在 GitHub 上创建仓库（如 meta-skills-rank-lists）。若已存在则返回成功。"""
    if "/" in repo_name:
        repo_name = repo_name.split("/")[-1]
    url = "https://api.github.com/user/repos"
    headers = {"Accept": "application/vnd.github.v3+json", "Authorization": f"token {token}"}
    body = json.dumps({"name": repo_name, "private": private, "auto_init": True}).encode()
    try:
        import urllib.request
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            r = json.loads(resp.read().decode())
        return True, r.get("html_url", "")
    except Exception as e:
        err = str(e)
        if "422" in err or "name already exists" in err.lower() or "Repository creation failed" in err:
            return True, f"https://github.com/.../{repo_name}"
        return False, err


def _expand_path(p: str) -> Path:
    return Path(os.path.expanduser(p))


def _get_token(config: Optional[dict] = None) -> str:
    config = config or _load_config()
    t = (config.get("github") or {}).get("token") or os.environ.get("GITHUB_TOKEN", "")
    if isinstance(t, str) and t.startswith("${") and t.endswith("}"):
        t = os.environ.get("GITHUB_TOKEN", "")
    return (t or "").strip()


# ---------- 发现模块 ----------


@dataclass
class RepoCandidate:
    full_name: str
    html_url: str
    clone_url: str
    description: str
    stars: int
    updated_at: str
    default_branch: str = "main"


def _github_search(
    token: str,
    query: str,
    sort: str = "stars",
    order: str = "desc",
    per_page: int = 20,
) -> list[dict]:
    url = "https://api.github.com/search/repositories"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    params = {"q": query, "sort": sort, "order": order, "per_page": per_page}
    try:
        import urllib.request
        from urllib.parse import urlencode
        req = urllib.request.Request(
            url + "?" + urlencode(params),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return []
    return data.get("items", [])


def _read_cached(path: Path, ttl_hours: float) -> Optional[str]:
    if not path.exists():
        return None
    try:
        if (time.time() - path.stat().st_mtime) / 3600 > ttl_hours:
            return None
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def discovery(
    keywords: str,
    token: str = "",
    min_stars: int = 50,
    updated_within_days: int = 90,  # 最近 3 个月
    topic: str = "openclaw-skill",
    max_results: int = 20,
    cache_dir: Optional[Path] = None,
    cache_ttl_hours: float = 24,
) -> list[RepoCandidate]:
    token = token or os.environ.get("GITHUB_TOKEN", "")
    since = (datetime.utcnow() - timedelta(days=updated_within_days)).strftime("%Y-%m-%d")
    # 若关键词含 " / " 或 ","，只取第一段用于搜索，避免整串过严导致 0 结果
    kw_clean = keywords.strip()
    if kw_clean and (" / " in kw_clean or "," in kw_clean):
        first = (kw_clean.replace(",", " / ").split(" / ")[0] or "").strip()
        if first:
            kw_clean = first
    q_parts = [f"topic:{topic}", f"stars:>{min_stars}", f"pushed:>={since}"]
    if kw_clean:
        q_parts.insert(0, kw_clean)
    query = " ".join(q_parts)
    cache_path = None
    if cache_dir:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe_q = re.sub(r"[^\w\-]", "_", query)[:100]
        cache_path = cache_dir / f"search_{safe_q}.json"
    cached = _read_cached(cache_path, cache_ttl_hours) if cache_path else None
    if cached:
        try:
            items = json.loads(cached)
        except Exception:
            items = None
    else:
        items = None
    if items is None:
        items = _github_search(token, query, sort="stars", order="desc", per_page=max_results)
        # 若有关键词但结果为 0，可能是关键词过严（如整串 "a / b / c"），回退为仅 topic+stars+pushed
        if not items and keywords.strip():
            fallback_query = " ".join([f"topic:{topic}", f"stars:>{min_stars}", f"pushed:>={since}"])
            items = _github_search(token, fallback_query, sort="stars", order="desc", per_page=max_results)
        if cache_path and items:
            cache_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    out = []
    for r in items:
        out.append(
            RepoCandidate(
                full_name=r["full_name"],
                html_url=r["html_url"],
                clone_url=r["clone_url"],
                description=r.get("description") or "",
                stars=r.get("stargazers_count", 0),
                updated_at=r.get("pushed_at", r.get("updated_at", "")),
                default_branch=r.get("default_branch", "main"),
            )
        )
    return out


def discovery_from_rank_list(
    rank_lists_repo: str,
    token: str,
    min_use_count: int = 1,
) -> list[dict]:
    """从用户或他人的 meta-skills-rank-lists 拉取高使用量技能列表，用于每日自动配置。"""
    skills = fetch_rank_list_from_github(rank_lists_repo, token)
    return [s for s in skills if s.get("use_count", 0) >= min_use_count]


# ---------- 沙箱验证 ----------


def _run_validate_docker(skill_dir: Path, image: str = "python:3.11-slim", timeout: int = 60) -> tuple[bool, str]:
    validate_py = skill_dir / "validate.py"
    if not validate_py.exists():
        return True, "no validate.py, skip"
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{skill_dir}:/skill:ro",
        image, "sh", "-c", "cd /skill && python validate.py",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "non-zero exit")
        return True, (r.stdout or "ok")
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except FileNotFoundError:
        return False, "docker not found"
    except Exception as e:
        return False, str(e)


def validate_in_sandbox(skill_dir: Path, config: dict) -> tuple[bool, str]:
    sb = config.get("sandbox", {})
    docker_cfg = sb.get("docker", {})
    if docker_cfg.get("enabled", True):
        return _run_validate_docker(
            skill_dir,
            image=docker_cfg.get("image", "python:3.11-slim"),
            timeout=docker_cfg.get("timeout_seconds", 60),
        )
    return True, "sandbox disabled, skip"


# ---------- 安装与热加载 ----------


def _clone_repo(clone_url: str, branch: str, dest: Path, token: str = "") -> bool:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    url = clone_url.replace("https://", f"https://{token}@") if token and "github.com" in clone_url else clone_url
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "-b", branch, url, str(dest)],
            check=True, capture_output=True, timeout=120,
        )
        return True
    except Exception:
        return False


def _skill_name_from_dir(skill_dir: Path) -> Optional[str]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None
    m = re.search(r"^name:\s*(\S+)", skill_md.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1).strip() if m else skill_dir.name


def install_skill(
    repo: RepoCandidate,
    skills_dir: Path,
    config: dict,
    run_validate: bool = True,
) -> tuple[bool, str]:
    tmp = Path(tempfile.mkdtemp(prefix="meta_skills_"))
    try:
        token = os.environ.get("GITHUB_TOKEN", "")
        if not _clone_repo(repo.clone_url, repo.default_branch, tmp, token):
            return False, "clone failed"
        skill_name = _skill_name_from_dir(tmp)
        if not skill_name:
            return False, "no SKILL.md or name"
        if run_validate:
            v_ok, v_msg = validate_in_sandbox(tmp, config)
            if not v_ok:
                return False, f"validate failed: {v_msg}"
        dest = skills_dir / skill_name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(tmp, dest)
        return True, skill_name
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def signal_reload(config: dict) -> None:
    skills_dir = config.get("openclaw", {}).get("skills_dir", "~/.openclaw/skills")
    path = _expand_path(skills_dir)
    if path.exists():
        try:
            (path / ".reload").touch()
        except Exception:
            pass


# ---------- 首次安装后更新 rank-lists ----------


def update_rank_lists_after_install(
    skill_name: str,
    source_url: str,
    config: Optional[dict] = None,
) -> tuple[bool, str]:
    """首次安装（或每次安装）后，将已安装情况写入本地 JSON 并上传到 meta-skills-rank-lists。"""
    config = config or _load_config()
    repo = (config.get("rank_lists") or {}).get("repo", "").strip()
    if not repo:
        return False, "rank_lists.repo 未配置"
    data = load_rank_data(BASE_DIR)
    now = datetime.utcnow().isoformat() + "Z"
    ensure_skill_entry(data, skill_name, source_url=source_url, installed_at=now)
    save_rank_data(data, BASE_DIR)
    token = _get_token(config)
    return push_to_github(repo, token, BASE_DIR, data)


# ---------- 每日任务：检索安装 + 上传使用与评分 ----------


def daily_run(config: Optional[dict] = None) -> dict:
    """
    每日默认 21:00 执行：
    1. 从 GitHub + meta-skills-rank-lists 检索，安装推荐技能（受 max_skills 上限）
    2. 若有今日使用，将本地 JSON 上传到 meta-skills-rank-lists
    """
    config = config or _load_config()
    result = {"installed": [], "uploaded": False, "upload_error": None}
    token = _get_token(config)
    gh = config.get("github", {})
    disc = gh.get("discovery", {})
    rank_cfg = config.get("rank_lists", {})
    rank_repo = rank_cfg.get("repo", "").strip()
    discovery_repos = rank_cfg.get("discovery_repos") or []
    if rank_repo and rank_repo not in discovery_repos:
        discovery_repos = [rank_repo] + list(discovery_repos)
    skills_dir = _expand_path(config.get("openclaw", {}).get("skills_dir", str(DEFAULT_SKILLS_DIR)))
    skills_dir.mkdir(parents=True, exist_ok=True)
    db = DBHandler(base_dir=BASE_DIR)
    installed_names = {r.name for r in db.list_skills()}
    max_skills = (gh.get("discovery") or {}).get("max_skills", 100)

    # 1) 从配置的 rank-lists 仓库取高使用量技能（需有 source_url 才能克隆）
    rank_skills = []
    for r in discovery_repos:
        rank_skills.extend(discovery_from_rank_list(r, token))
    seen = set()
    deduped = []
    for s in rank_skills:
        k = s.get("name") or s.get("source_url")
        if k and k not in seen:
            seen.add(k)
            deduped.append(s)
    rank_skills_sorted = sorted(deduped, key=lambda s: s.get("use_count", 0), reverse=True)

    keywords = rank_cfg.get("keywords", []) or get_keywords(BASE_DIR)
    for s in rank_skills_sorted:
        name = s.get("name", "")
        url = s.get("source_url", "")
        if not name or name in installed_names or not url:
            continue
        if "/" not in url:
            continue
        clone_url = url + ".git" if not url.endswith(".git") else url
        if "github.com" not in clone_url:
            continue
        fake_repo = RepoCandidate(
            full_name=name,
            html_url=url,
            clone_url=clone_url,
            description="",
            stars=0,
            updated_at="",
            default_branch="main",
        )
        ok, msg = install_skill(fake_repo, skills_dir, config, run_validate=True)
        if ok:
            db.register_skill(msg, url)
            data = load_rank_data(BASE_DIR)
            ensure_skill_entry(data, msg, source_url=url)
            save_rank_data(data, BASE_DIR)
            installed_names.add(msg)
            result["installed"].append({"name": msg, "source": "rank_list"})
        if len(installed_names) >= max_skills:
            break

    # 2) GitHub 搜索（用用户关键词）
    for kw in (keywords or ["openclaw"])[:3]:
        if len(installed_names) >= max_skills:
            break
        repos = discovery(kw, token=token, min_stars=disc.get("min_stars", 50),
                          updated_within_days=disc.get("updated_within_days", 90),
                          max_results=disc.get("max_results_per_search", 10),
                          cache_dir=BASE_DIR / gh.get("cache_dir", ".github_cache"),
                          cache_ttl_hours=gh.get("cache_ttl_hours", 24))
        for repo in repos:
            if len(installed_names) >= max_skills:
                break
            ok, msg = install_skill(repo, skills_dir, config, run_validate=True)
            if ok and msg not in installed_names:
                db.register_skill(msg, repo.html_url)
                data = load_rank_data(BASE_DIR)
                ensure_skill_entry(data, msg, source_url=repo.html_url)
                save_rank_data(data, BASE_DIR)
                installed_names.add(msg)
                result["installed"].append({"name": msg, "source": "github", "repo": repo.full_name})

    signal_reload(config)

    # 3) 上传今日排名数据到 meta-skills-rank-lists
    if rank_repo:
        data = load_rank_data(BASE_DIR)
        if not data.get("keywords") and rank_cfg.get("keywords"):
            set_keywords(rank_cfg["keywords"], BASE_DIR)
            data = load_rank_data(BASE_DIR)
        if data.get("skills") or data.get("keywords"):
            ok, err = push_to_github(rank_repo, token, BASE_DIR, data)
            result["uploaded"] = ok
            if not ok:
                result["upload_error"] = err

    return result


# ---------- 记录使用（唯一打分来源：调用次数） ----------


def record_skill_use(skill_name: str, source_url: str = "") -> None:
    """用户每次调用某技能时记录到本地 JSON，每日统一上传。"""
    record_use(BASE_DIR, skill_name=skill_name, source_url=source_url)
    db = DBHandler(base_dir=BASE_DIR)
    try:
        db.record_invocation(skill_name, True)
    except KeyError:
        pass


def record_skill_edit(skill_name: str, source_url: str = "") -> None:
    record_edit(BASE_DIR, skill_name=skill_name, source_url=source_url)


# ---------- 优先级、评分查询等 ----------


def main_scores(skill_name: Optional[str] = None) -> list[SkillScore] | Optional[SkillScore]:
    config = _load_config()
    grading = config.get("grading", {})
    db = DBHandler(base_dir=BASE_DIR)
    if skill_name:
        return db.get_score(
            skill_name,
            success_weight=grading.get("success_weight", 0.4),
            feedback_weight=grading.get("feedback_weight", 0.6),
            min_invocations=grading.get("min_invocations", 3),
        )
    return db.get_all_scores(
        success_weight=grading.get("success_weight", 0.4),
        feedback_weight=grading.get("feedback_weight", 0.6),
        min_invocations=grading.get("min_invocations", 3),
    )


def main_priority(skill_name: str, priority: int) -> str:
    DBHandler(base_dir=BASE_DIR).set_priority(skill_name, priority)
    return f"priority set to {priority}"


def get_installed_summary(config: Optional[dict] = None) -> list[dict]:
    """返回已安装技能列表及简要能力（从 SKILL.md 的 description 提取）。"""
    config = config or _load_config()
    skills_dir = _expand_path(config.get("openclaw", {}).get("skills_dir", str(DEFAULT_SKILLS_DIR)))
    db = DBHandler(base_dir=BASE_DIR)
    out = []
    for rec in db.list_skills():
        skill_md = skills_dir / rec.name / "SKILL.md"
        desc = ""
        if skill_md.exists():
            try:
                text = skill_md.read_text(encoding="utf-8")
                m = re.search(r"^description:\s*(.+?)(?:\n|---)", text, re.DOTALL)
                if m:
                    desc = m.group(1).strip().split("\n")[0][:200]
            except Exception:
                pass
        out.append({"name": rec.name, "source_url": rec.source_url, "description": desc or "—"})
    return out


def create_rank_lists_repo_and_init(token: str, username: str, repo_name: str = "meta-skills-rank-lists") -> tuple[bool, str]:
    """创建用户个人的 meta-skills-rank-lists 仓库并初始化 README + rank_data.json。"""
    if "/" in username:
        username = username.split("/")[0]
    full_repo = f"{username}/{repo_name}"
    ok, _ = create_github_repo(token, full_repo, private=False)
    if not ok:
        return False, "create repo failed"
    _set_config_key("rank_lists.repo", full_repo)
    data = load_rank_data(BASE_DIR)
    ok2, err = push_to_github(full_repo, token, BASE_DIR, data)
    return ok2, err if not ok2 else full_repo


# ---------- CLI ----------


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: search_install <keywords> | scores [skill_name] | record <skill_name> [source_url] | priority <skill_name> <n> | daily_run | upload_rank | config get <key> | config set <key> <value> | keywords add k1 k2 | keywords update k1 k2 | keywords get | create_rank_repo <username> | installed_summary | max_skills [n] | schedule [hour] [minute]")
        sys.exit(1)
    cmd = sys.argv[1].lower()
    config = _load_config()
    token = _get_token(config)

    if cmd == "search_install":
        kw = " ".join(sys.argv[2:]) or "openclaw"
        gh = config.get("github", {})
        disc = gh.get("discovery", {})
        max_skills = disc.get("max_skills", 100)
        skills_dir = _expand_path(config.get("openclaw", {}).get("skills_dir", str(DEFAULT_SKILLS_DIR)))
        skills_dir.mkdir(parents=True, exist_ok=True)
        db = DBHandler(base_dir=BASE_DIR)
        installed_names = {r.name for r in db.list_skills()}
        repos = discovery(kw, token=token, min_stars=disc.get("min_stars", 50),
                          updated_within_days=disc.get("updated_within_days", 90),
                          max_results=min(disc.get("max_results_per_search", 20), max(1, max_skills - len(installed_names))),
                          cache_dir=BASE_DIR / gh.get("cache_dir", ".github_cache"),
                          cache_ttl_hours=gh.get("cache_ttl_hours", 24))
        rank_repo = (config.get("rank_lists") or {}).get("repo", "").strip()
        installed = []
        for repo in repos:
            if len(installed_names) >= max_skills:
                break
            ok, msg = install_skill(repo, skills_dir, config, run_validate=True)
            if ok and msg not in installed_names:
                db.register_skill(msg, repo.html_url)
                data = load_rank_data(BASE_DIR)
                ensure_skill_entry(data, msg, source_url=repo.html_url)
                save_rank_data(data, BASE_DIR)
                installed_names.add(msg)
                installed.append({"name": msg, "repo": repo.full_name})
                if rank_repo:
                    update_rank_lists_after_install(msg, repo.html_url, config)
        signal_reload(config)
        out = {"installed": installed, "total_installed": len(installed_names)}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        # 输出本次安装的技能简介（从 SKILL.md 的 description 解析）
        if installed:
            summary = get_installed_summary(config)
            by_name = {s["name"]: s["description"] for s in summary}
            print("\n# 本次安装的技能简介（来自 SKILL.md description）\n")
            for it in installed:
                name = it["name"]
                desc = by_name.get(name, "—")
                print(f"- **{name}**\n  {desc}\n")

    elif cmd == "scores":
        name = sys.argv[2] if len(sys.argv) > 2 else None
        out = main_scores(name)
        if out is None:
            print("[]")
        elif isinstance(out, list):
            print(json.dumps([{"name": s.skill_name, "score": s.composite_score, "invocations": s.total_invocations, "feedback": s.total_feedback} for s in out], ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"name": out.skill_name, "score": out.composite_score, "invocations": out.total_invocations, "feedback": out.total_feedback}, ensure_ascii=False, indent=2))

    elif cmd == "record" and len(sys.argv) >= 3:
        skill_name = sys.argv[2]
        source_url = sys.argv[3] if len(sys.argv) > 3 else ""
        record_skill_use(skill_name, source_url=source_url)
        print("recorded")

    elif cmd == "priority" and len(sys.argv) >= 4:
        print(main_priority(sys.argv[2], int(sys.argv[3])))

    elif cmd == "daily_run":
        print(json.dumps(daily_run(config), ensure_ascii=False, indent=2))

    elif cmd == "upload_rank":
        rank_repo = (config.get("rank_lists") or {}).get("repo", "").strip()
        if not rank_repo:
            print(json.dumps({"ok": False, "error": "rank_lists.repo not set"}))
        else:
            ok, err = push_to_github(rank_repo, token, BASE_DIR)
            print(json.dumps({"ok": ok, "error": err}))

    elif cmd == "config" and len(sys.argv) >= 4:
        sub = sys.argv[2].lower()
        key = sys.argv[3]
        if sub == "get":
            val = _get_config_key(key, config)
            print(json.dumps({"key": key, "value": val}))
        elif sub == "set" and len(sys.argv) >= 5:
            val = sys.argv[4]
            if val.isdigit():
                val = int(val)
            elif val.lower() in ("true", "false"):
                val = val.lower() == "true"
            _set_config_key(key, val)
            print(json.dumps({"ok": True, "key": key}))

    elif cmd == "keywords":
        if len(sys.argv) < 3:
            print(json.dumps({"keywords": get_keywords(BASE_DIR)}))
        else:
            sub = sys.argv[2].lower()
            if sub == "get":
                print(json.dumps({"keywords": get_keywords(BASE_DIR)}))
            elif sub == "add" and len(sys.argv) >= 4:
                added = sys.argv[3:]
                current = add_keywords(added, BASE_DIR)
                print(json.dumps({"added": added, "keywords": current}))
            elif sub == "update" and len(sys.argv) >= 4:
                new_list = sys.argv[3:]
                current = update_keywords(new_list, BASE_DIR)
                print(json.dumps({"keywords": current}))

    elif cmd == "create_rank_repo" and len(sys.argv) >= 3:
        username = sys.argv[2]
        if not token:
            print(json.dumps({"ok": False, "error": "GITHUB_TOKEN or github.token required"}))
        else:
            ok, msg = create_rank_lists_repo_and_init(token, username)
            print(json.dumps({"ok": ok, "repo_or_error": msg}))

    elif cmd == "installed_summary":
        print(json.dumps(get_installed_summary(config), ensure_ascii=False, indent=2))

    elif cmd == "max_skills":
        if len(sys.argv) >= 3:
            n = int(sys.argv[2])
            _set_config_key("github.discovery.max_skills", n)
            print(json.dumps({"max_skills": n}))
        else:
            print(json.dumps({"max_skills": ((config.get("github") or {}).get("discovery") or {}).get("max_skills", 100)}))

    elif cmd == "schedule":
        if len(sys.argv) >= 4:
            h, m = int(sys.argv[2]), int(sys.argv[3])
            _set_config_key("schedule.hour", h)
            _set_config_key("schedule.minute", m)
            print(json.dumps({"hour": h, "minute": m}))
        else:
            s = config.get("schedule", {})
            print(json.dumps({"enabled": s.get("enabled", True), "hour": s.get("hour", 21), "minute": s.get("minute", 0)}))

    else:
        print("unknown command or missing args")
        sys.exit(1)
