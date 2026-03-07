# -*- coding: utf-8 -*-
"""
meta-skills - 核心逻辑
发现（GitHub + meta-skills-rank-lists）、安装、本地 JSON 记录使用、每日上传排名、每日 21:00 定时任务。
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

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
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
TIMER_UNIT = "meta-skills-daily.timer"
SERVICE_UNIT = "meta-skills-daily.service"


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
    min_stars: int = 10,
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


# 用于 awesome 校验时的请求限速（多线程共享）
_github_rate_limit_lock: Optional[object] = None
_github_last_request_time: float = 0.0


def _github_get(
    token: str,
    path: str,
    rate_limit_interval: float = 0.0,
) -> Optional[dict]:
    """GET https://api.github.com/{path}，返回 JSON 或 None。支持限速与 429/403 重试。"""
    import urllib.request
    import urllib.error
    url = f"https://api.github.com/{path}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    def do_request():
        global _github_rate_limit_lock, _github_last_request_time
        if rate_limit_interval > 0 and _github_rate_limit_lock is not None:
            with _github_rate_limit_lock:
                now = time.monotonic()
                wait = _github_last_request_time + rate_limit_interval - now
                if wait > 0:
                    time.sleep(wait)
                _github_last_request_time = time.monotonic()
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())

    for attempt in range(2):
        try:
            return do_request()
        except urllib.error.HTTPError as e:
            if attempt == 0 and e.code in (403, 429):
                wait_sec = 60
                ra = e.headers.get("Retry-After", "")
                if ra.isdigit():
                    wait_sec = min(300, int(ra))
                print(f"[meta-skills] GitHub API {e.code}，{wait_sec}s 后重试...", file=sys.stderr)
                time.sleep(wait_sec)
                continue
            return None
        except Exception:
            return None
    return None


def _fetch_readme_from_repo(token: str, owner: str, repo: str) -> Optional[str]:
    """拉取仓库 README 内容（优先 README.md）。返回解码后的文本。"""
    data = _github_get(token, f"repos/{owner}/{repo}/readme")
    if not data or data.get("encoding") != "base64":
        return None
    try:
        return base64.b64decode(data.get("content", "")).decode("utf-8")
    except Exception:
        return None


def _parse_github_repo_links_from_markdown(markdown: str) -> set[str]:
    """从 Markdown 文本中解析出 GitHub 仓库 full_name 集合（owner/repo）。"""
    # 匹配 https://github.com/owner/repo 或 https://github.com/owner/repo/ 或 /owner/repo 等
    pattern = re.compile(
        r"github\.com[/:]([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+?)(?:/|$|\s|\)|\]|#)",
        re.IGNORECASE,
    )
    seen = set()
    for m in pattern.finditer(markdown):
        owner, name = m.group(1), m.group(2)
        if name.lower() in ("blob", "tree", "edit", "raw", "commits", "issues", "pull"):
            continue
        seen.add(f"{owner}/{name}")
    return seen


def _repo_has_skill(
    token: str,
    owner: str,
    repo: str,
    repo_data: Optional[dict] = None,
    rate_limit_interval: float = 0.0,
) -> bool:
    """通过 API 判断仓库是否包含根目录 SKILL.md 或 topic 含 openclaw-skill。可传入 repo_data 避免重复请求。"""
    if repo_data is None:
        repo_data = _github_get(token, f"repos/{owner}/{repo}", rate_limit_interval=rate_limit_interval)
    if not repo_data:
        return False
    topics = repo_data.get("topics") or []
    if "openclaw-skill" in topics:
        return True
    contents = _github_get(token, f"repos/{owner}/{repo}/contents/", rate_limit_interval=rate_limit_interval)
    if not isinstance(contents, list):
        return False
    for item in contents:
        if isinstance(item, dict) and (item.get("name") or "").upper() == "SKILL.MD":
            return True
    return False


def _validate_one_awesome_link(
    token: str,
    full_name: str,
    min_stars: int,
    skip_owner: str,
    skip_repo: str,
    rate_limit_interval: float = 0.0,
    skill_check_cache: Optional[dict] = None,
    skill_check_cache_lock: Optional[object] = None,
    link_store: Optional[dict] = None,
    link_store_lock: Optional[object] = None,
) -> Optional[RepoCandidate]:
    """校验单个链接是否为满足星数且含 SKILL 的仓库。若链接存储中该仓库 pushed_at 未变则直接用缓存，不再请求 contents。"""
    if "/" not in full_name:
        return None
    o, r = full_name.split("/", 1)
    if o == skip_owner and r == skip_repo:
        return None
    if skill_check_cache is not None and skill_check_cache_lock is not None:
        with skill_check_cache_lock:
            if full_name in skill_check_cache:
                return skill_check_cache[full_name]
    elif skill_check_cache is not None:
        if full_name in skill_check_cache:
            return skill_check_cache[full_name]
    repo_data = _github_get(token, f"repos/{o}/{r}", rate_limit_interval=rate_limit_interval)
    if not repo_data or (repo_data.get("stargazers_count") or 0) < min_stars:
        if skill_check_cache is not None:
            if skill_check_cache_lock:
                with skill_check_cache_lock:
                    skill_check_cache[full_name] = None
            else:
                skill_check_cache[full_name] = None
        if link_store is not None:
            pushed = repo_data.get("pushed_at") if repo_data else None
            entry = {"pushed_at": pushed, "has_skill": False}
            if link_store_lock:
                with link_store_lock:
                    link_store[full_name] = entry
            else:
                link_store[full_name] = entry
        return None
    pushed_at = repo_data.get("pushed_at") or repo_data.get("updated_at") or ""
    # 仅当仓库相比上次有更新时才请求 contents 做 skill 校验
    if link_store is not None:
        if link_store_lock:
            with link_store_lock:
                stored = link_store.get(full_name)
        else:
            stored = link_store.get(full_name)
        if isinstance(stored, dict) and stored.get("pushed_at") == pushed_at:
            if not stored.get("has_skill"):
                if skill_check_cache is not None:
                    if skill_check_cache_lock:
                        with skill_check_cache_lock:
                            skill_check_cache[full_name] = None
                    else:
                        skill_check_cache[full_name] = None
                return None
            c = stored.get("candidate")
            if isinstance(c, dict) and c.get("full_name") == full_name:
                out = RepoCandidate(
                    full_name=c["full_name"],
                    html_url=c.get("html_url", ""),
                    clone_url=c.get("clone_url", ""),
                    description=c.get("description", ""),
                    stars=c.get("stargazers_count", 0),
                    updated_at=c.get("pushed_at", ""),
                    default_branch=c.get("default_branch", "main"),
                )
                if skill_check_cache is not None:
                    if skill_check_cache_lock:
                        with skill_check_cache_lock:
                            skill_check_cache[full_name] = out
                    else:
                        skill_check_cache[full_name] = out
                return out
    has_skill = _repo_has_skill(token, o, r, repo_data=repo_data, rate_limit_interval=rate_limit_interval)
    if link_store is not None:
        entry = {
            "pushed_at": pushed_at,
            "has_skill": has_skill,
            "candidate": None,
        }
        if has_skill:
            entry["candidate"] = {
                "full_name": repo_data["full_name"],
                "html_url": repo_data["html_url"],
                "clone_url": repo_data.get("clone_url", repo_data["html_url"] + ".git"),
                "description": repo_data.get("description") or "",
                "stargazers_count": repo_data.get("stargazers_count", 0),
                "pushed_at": pushed_at,
                "default_branch": repo_data.get("default_branch", "main"),
            }
        if link_store_lock:
            with link_store_lock:
                link_store[full_name] = entry
        else:
            link_store[full_name] = entry
    if not has_skill:
        if skill_check_cache is not None:
            if skill_check_cache_lock:
                with skill_check_cache_lock:
                    skill_check_cache[full_name] = None
            else:
                skill_check_cache[full_name] = None
        return None
    candidate = RepoCandidate(
        full_name=repo_data["full_name"],
        html_url=repo_data["html_url"],
        clone_url=repo_data.get("clone_url", repo_data["html_url"] + ".git"),
        description=repo_data.get("description") or "",
        stars=repo_data.get("stargazers_count", 0),
        updated_at=pushed_at,
        default_branch=repo_data.get("default_branch", "main"),
    )
    if skill_check_cache is not None:
        if skill_check_cache_lock:
            with skill_check_cache_lock:
                skill_check_cache[full_name] = candidate
        else:
            skill_check_cache[full_name] = candidate
    return candidate


def _normalize_awesome_list_entry(entry: str) -> Optional[str]:
    """将配置项转为 owner/repo：支持 'owner/repo' 或 'https://github.com/owner/repo'。"""
    s = (entry or "").strip()
    if not s:
        return None
    if "github.com" not in s and not s.startswith("http"):
        return s if "/" in s else None  # 已是 owner/repo
    m = re.search(r"github\.com[/:]([^/]+)/([^/#?\s]+)", s)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _load_repo_links_store(path: Path) -> dict:
    """加载链接信息存储：full_name -> { pushed_at, has_skill, candidate? }。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_repo_links_store(path: Path, store: dict) -> None:
    """保存链接信息存储到 JSON。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(store, ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception:
        pass


def discovery_from_awesome_lists(
    config: Optional[dict] = None,
    token: str = "",
    cache_dir: Optional[Path] = None,
    cache_ttl_hours: float = 24,
    min_stars: int = 10,
    max_repos_per_list: int = 100,
) -> list[RepoCandidate]:
    """
    从 config 中的 awesome_lists 配置读取仓库列表，拉取各仓库 README，解析 GitHub 链接，
    校验是否为 skill 后返回候选列表。用于扩展 discovery 的检索范围（不依赖 topic:openclaw-skill）。
    """
    config = config or _load_config()
    token = token or _get_token(config)
    gh = config.get("github", {})
    disc = gh.get("discovery", {})
    raw_lists = disc.get("awesome_lists") or []
    awesome_lists = []
    for e in raw_lists:
        if isinstance(e, str):
            spec = _normalize_awesome_list_entry(e)
            if spec and spec not in awesome_lists:
                awesome_lists.append(spec)
    if not awesome_lists:
        print("[meta-skills] 未配置 awesome_lists 或列表为空", file=sys.stderr)
        return []
    cache_dir = Path(cache_dir or BASE_DIR / gh.get("cache_dir", ".github_cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    link_store_path = cache_dir / "repo_links_store.json"
    link_store = _load_repo_links_store(link_store_path)
    link_store_lock = threading.Lock()
    print(f"[meta-skills] 从配置的 {len(awesome_lists)} 个 awesome 仓库解析 README 链接...（链接存储 {len(link_store)} 条，仅对有更新的仓库校验 skill）", file=sys.stderr)
    all_candidates: list[RepoCandidate] = []
    seen_full_name: set[str] = set()

    for list_spec in awesome_lists:
        if "/" not in list_spec:
            continue
        owner, list_repo = list_spec.strip().split("/", 1)
        list_name = f"{owner}/{list_repo}"
        print(f"[meta-skills]   正在处理: {list_name}", file=sys.stderr)
        # awesome 仓库自身也作为检索对象：若其根目录含 SKILL.md 则加入候选
        if list_name not in seen_full_name:
            c = _validate_one_awesome_link(
                token, list_name, min_stars, skip_owner="", skip_repo="",
                link_store=link_store, link_store_lock=link_store_lock,
            )
            if c:
                seen_full_name.add(c.full_name)
                all_candidates.append(c)
                print(f"[meta-skills]     -> 本仓库为 skill，已加入候选", file=sys.stderr)
        cache_path = cache_dir / f"awesome_{owner}_{list_repo.replace('/', '_')}.json"
        cached = _read_cached(cache_path, cache_ttl_hours) if cache_path else None
        if cached:
            try:
                items = json.loads(cached)
                n_from_cache = 0
                for r in items:
                    full_name = r.get("full_name", "")
                    if full_name and full_name not in seen_full_name:
                        seen_full_name.add(full_name)
                        all_candidates.append(
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
                        n_from_cache += 1
                print(f"[meta-skills]     -> 使用缓存，{n_from_cache} 个新候选", file=sys.stderr)
                continue
            except Exception:
                pass
        readme = _fetch_readme_from_repo(token, owner, list_repo)
        if not readme:
            print(f"[meta-skills]     -> 拉取 README 失败", file=sys.stderr)
            continue
        links = _parse_github_repo_links_from_markdown(readme)
        max_try = max(1, min(500, disc.get("max_links_to_try_per_list", 50)))
        to_try = [f for f in links if "/" in f and f != f"{owner}/{list_repo}"][:max_try]
        parallel = max(1, int(disc.get("awesome_parallel", 1)))
        print(f"[meta-skills]     -> 解析出 {len(links)} 个链接，尝试校验前 {len(to_try)} 个（并行 {parallel}）...", file=sys.stderr)
        list_candidates: list[RepoCandidate] = []
        if parallel <= 1:
            for full_name in to_try:
                if len(list_candidates) >= max_repos_per_list:
                    break
                c = _validate_one_awesome_link(
                    token, full_name, min_stars, owner, list_repo,
                    link_store=link_store, link_store_lock=link_store_lock,
                )
                if c:
                    list_candidates.append(c)
        else:
            with ThreadPoolExecutor(max_workers=parallel) as ex:
                futures = {
                    ex.submit(
                        _validate_one_awesome_link,
                        token, fn, min_stars, owner, list_repo,
                        link_store=link_store, link_store_lock=link_store_lock,
                    ): fn
                    for fn in to_try
                }
                for fut in as_completed(futures):
                    if len(list_candidates) >= max_repos_per_list:
                        break
                    try:
                        c = fut.result()
                        if c and c.full_name not in {x.full_name for x in list_candidates}:
                            list_candidates.append(c)
                    except Exception:
                        pass
        print(f"[meta-skills]     -> 通过校验 {len(list_candidates)} 个 skill", file=sys.stderr)
        _save_repo_links_store(link_store_path, link_store)
        if cache_path and list_candidates:
            cache_path.write_text(
                json.dumps(
                    [
                        {
                            "full_name": c.full_name,
                            "html_url": c.html_url,
                            "clone_url": c.clone_url,
                            "description": c.description,
                            "stargazers_count": c.stars,
                            "pushed_at": c.updated_at,
                            "default_branch": c.default_branch,
                        }
                    for c in list_candidates
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        for c in list_candidates:
            if c.full_name not in seen_full_name:
                seen_full_name.add(c.full_name)
                all_candidates.append(c)
    print(f"[meta-skills] awesome 扩展共得到 {len(all_candidates)} 个候选", file=sys.stderr)
    return all_candidates


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


def _write_search_install_report(
    installed: list[dict],
    total_installed: int,
    summary_by_name: dict[str, str],
    base_dir: Path,
) -> Optional[Path]:
    """
    将 search_install 的结果（JSON + 技能简介）写入 Markdown 报告。
    路径: base_dir/reports/search_install_YYYY-MM-DD_HH-MM-SS.md
    便于在 OpenClaw 对话中让 AI 读取该文件并发送到对话窗口。
    """
    reports_dir = base_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    report_path = reports_dir / f"search_install_{ts}.md"
    lines = [
        "# search_install 结果",
        "",
        f"**本次新安装**：{len(installed)} 个",
        f"**当前共安装**：{total_installed} 个",
        "",
        "## 本次安装列表（JSON）",
        "```json",
        json.dumps({"installed": installed, "total_installed": total_installed}, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 本次安装的技能简介（来自 SKILL.md description）",
        "",
    ]
    for it in installed:
        name = it.get("name", "")
        desc = summary_by_name.get(name, "—")
        lines.append(f"- **{name}**")
        lines.append(f"  {desc}")
        lines.append("")
    try:
        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path
    except Exception:
        return None


def _write_daily_update_report(
    installed: list[dict],
    config: Optional[dict],
    base_dir: Path,
) -> Optional[Path]:
    """
    将本次每日更新中新安装的技能列表与说明写入 Markdown 报告。
    路径: base_dir/reports/daily_update_YYYY-MM-DD.md
    返回报告文件路径；若无新安装或写入失败则返回 None。
    """
    if not installed:
        return None
    config = config or _load_config()
    summary = get_installed_summary(config)
    by_name = {s["name"]: {"description": s["description"], "source_url": s["source_url"]} for s in summary}
    date_str = datetime.now().strftime("%Y-%m-%d")
    reports_dir = base_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"daily_update_{date_str}.md"
    lines = [
        "# Meta-Skills 每日更新报告",
        "",
        f"**日期**：{date_str}",
        f"**新安装技能数**：{len(installed)}",
        "",
        "| 技能名 | 来源 | 说明 |",
        "| --- | --- | --- |",
    ]
    for item in installed:
        name = item.get("name", "")
        source = item.get("source", "—")
        if source == "github" and item.get("repo"):
            source = item["repo"]
        info = by_name.get(name, {})
        desc = (info.get("description") or "—").replace("\n", " ").strip()[:200]
        lines.append(f"| {name} | {source} | {desc} |")
    lines.append("")
    try:
        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path
    except Exception:
        return None


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
        repos = discovery(kw, token=token, min_stars=disc.get("min_stars", 10),
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

    # 2.2) 从配置的 awesome 仓库列表扩展发现（拉取各 README 解析链接）
    if len(installed_names) < max_skills:
        repos_aw = discovery_from_awesome_lists(
            config=config, token=token,
            cache_dir=BASE_DIR / gh.get("cache_dir", ".github_cache"),
            cache_ttl_hours=gh.get("cache_ttl_hours", 24),
            min_stars=disc.get("min_stars_awesome", 10),
        )
        for repo in repos_aw:
            if len(installed_names) >= max_skills:
                break
            ok, msg = install_skill(repo, skills_dir, config, run_validate=True)
            if ok and msg not in installed_names:
                db.register_skill(msg, repo.html_url)
                data = load_rank_data(BASE_DIR)
                ensure_skill_entry(data, msg, source_url=repo.html_url)
                save_rank_data(data, BASE_DIR)
                installed_names.add(msg)
                result["installed"].append({"name": msg, "source": "awesome", "repo": repo.full_name})

    signal_reload(config)

    # 2.5) 若有新安装，将更新结果写入报告文档（供 OpenClaw 通过飞书发给用户）
    if result["installed"]:
        report_path = _write_daily_update_report(result["installed"], config, BASE_DIR)
        if report_path:
            result["report_path"] = str(report_path)

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
        # 默认结构：~/.openclaw/skills/<skill_name>/SKILL.md
        # 为兼容部分环境下的多级目录（如 ~/.openclaw/skills/<分类>/<skill_name>/SKILL.md），
        # 若根目录未找到，则向下最多再查两层子目录中的 SKILL.md。
        skill_root = skills_dir / rec.name
        skill_md = skill_root / "SKILL.md"
        if not skill_md.exists() and skill_root.exists():
            found: Optional[Path] = None
            try:
                for lvl1 in skill_root.iterdir():
                    if not lvl1.is_dir():
                        continue
                    candidate = lvl1 / "SKILL.md"
                    if candidate.exists():
                        found = candidate
                        break
                    # 第二层
                    for lvl2 in lvl1.iterdir():
                        if not lvl2.is_dir():
                            continue
                        candidate2 = lvl2 / "SKILL.md"
                        if candidate2.exists():
                            found = candidate2
                            break
                    if found:
                        break
            except Exception:
                found = None
            if found:
                skill_md = found
        desc = ""
        if skill_md.exists():
            try:
                text = skill_md.read_text(encoding="utf-8")
                # frontmatter 中 description: 可能在任意行，需 MULTILINE 使 ^ 匹配行首
                m = re.search(r"^description:\s*(.+?)(?:\n|---)", text, re.DOTALL | re.MULTILINE)
                if m:
                    desc = m.group(1).strip().split("\n")[0][:200]
                if not desc and text.strip().startswith("#"):
                    first_line = text.strip().split("\n")[0]
                    desc = re.sub(r"^#+\s*", "", first_line).strip()[:200]
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


# ---------- systemd 定时任务 ----------


def _systemd_user_run(args: list[str], capture: bool = True) -> tuple[bool, str]:
    """执行 systemctl --user 命令。返回 (成功, 输出或错误信息)。"""
    cmd = ["systemctl", "--user"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        out = (r.stdout or "").strip() + ("\n" + (r.stderr or "").strip() if r.stderr else "")
        return r.returncode == 0, out or ("ok" if r.returncode == 0 else "failed")
    except FileNotFoundError:
        return False, "systemctl not found"
    except Exception as e:
        return False, str(e)


def schedule_systemd_install(config: Optional[dict] = None) -> tuple[bool, str]:
    """
    安装并启用 systemd 用户级定时任务：将 unit 写入 ~/.config/systemd/user/，
    daemon-reload 后 enable + start timer。首次安装时默认启动。
    返回 (成功, 消息)。
    """
    config = config or _load_config()
    schedule = config.get("schedule", {})
    hour = schedule.get("hour", 21)
    minute = schedule.get("minute", 0)
    meta_dir = str(BASE_DIR.resolve())
    python_exe = sys.executable

    on_calendar = f"*-*-* {hour:02d}:{minute:02d}:00"
    desc_time = f"{hour:02d}:{minute:02d}"

    service_in = BASE_DIR / "systemd" / "meta-skills-daily.service.in"
    timer_in = BASE_DIR / "systemd" / "meta-skills-daily.timer.in"
    if not service_in.exists() or not timer_in.exists():
        return False, "systemd templates not found under systemd/"

    service_content = service_in.read_text(encoding="utf-8").replace("@META_SKILLS_DIR@", meta_dir).replace("@PYTHON@", python_exe)
    timer_content = timer_in.read_text(encoding="utf-8").replace("@ON_CALENDAR@", on_calendar).replace("@DESCRIPTION_TIME@", desc_time)

    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    service_path = SYSTEMD_USER_DIR / SERVICE_UNIT
    timer_path = SYSTEMD_USER_DIR / TIMER_UNIT
    try:
        service_path.write_text(service_content, encoding="utf-8")
        timer_path.write_text(timer_content, encoding="utf-8")
    except Exception as e:
        return False, f"write unit files: {e}"

    ok, out = _systemd_user_run(["daemon-reload"])
    if not ok:
        return False, f"daemon-reload: {out}"
    ok2, out2 = _systemd_user_run(["enable", "--now", TIMER_UNIT])
    if not ok2:
        return False, f"enable timer: {out2}"
    return True, f"installed and started: {timer_path} (daily at {desc_time})"


def schedule_systemd_start() -> tuple[bool, str]:
    """启动 systemd 定时器。"""
    return _systemd_user_run(["start", TIMER_UNIT])


def schedule_systemd_stop() -> tuple[bool, str]:
    """停止 systemd 定时器。"""
    return _systemd_user_run(["stop", TIMER_UNIT])


def schedule_systemd_status() -> tuple[bool, str]:
    """查询 systemd 定时器状态。"""
    return _systemd_user_run(["status", TIMER_UNIT])


# ---------- CLI ----------


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: search_install <keywords> | scores [skill_name] | record <skill_name> [source_url] | record_edit <skill_name> [source_url] | usage_stats | priority <skill_name> <n> | daily_run | upload_rank | schedule [install|start|stop|status] | schedule <hour> <minute> | ...")
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
        print(f"[meta-skills] 关键词: {kw!r}，当前已安装 {len(installed_names)} 个，上限 {max_skills}", file=sys.stderr)
        print("[meta-skills] 首次检索 SKILL 需要较长时间，请耐心等待。", file=sys.stderr)
        print("[meta-skills] 正在 GitHub 搜索...", file=sys.stderr)
        repos_gh = discovery(kw, token=token, min_stars=disc.get("min_stars", 10),
                             updated_within_days=disc.get("updated_within_days", 90),
                             max_results=min(disc.get("max_results_per_search", 20), max(1, max_skills - len(installed_names))),
                             cache_dir=BASE_DIR / gh.get("cache_dir", ".github_cache"),
                             cache_ttl_hours=gh.get("cache_ttl_hours", 24))
        seen = {r.full_name for r in repos_gh}
        repos = list(repos_gh)
        awesome_lists_cfg = disc.get("awesome_lists") or []
        if awesome_lists_cfg:
            print("[meta-skills] 正在从配置的 awesome 仓库列表解析 README 链接...", file=sys.stderr)
        repos_aw = discovery_from_awesome_lists(
            config=config, token=token,
            cache_dir=BASE_DIR / gh.get("cache_dir", ".github_cache"),
            cache_ttl_hours=gh.get("cache_ttl_hours", 24),
            min_stars=disc.get("min_stars_awesome", 10),
        )
        for r in repos_aw:
            if r.full_name not in seen:
                seen.add(r.full_name)
                repos.append(r)
        added_from_awesome = len(repos) - len(repos_gh)
        if added_from_awesome:
            print(f"[meta-skills] 从 awesome 列表补充 {added_from_awesome} 个候选（共 {len(repos)} 个）", file=sys.stderr)
        print(f"[meta-skills] 找到 {len(repos)} 个仓库，开始逐个尝试安装", file=sys.stderr)
        rank_repo = (config.get("rank_lists") or {}).get("repo", "").strip()
        installed = []
        for i, repo in enumerate(repos, 1):
            if len(installed_names) >= max_skills:
                print(f"[meta-skills] 已达上限 {max_skills}，停止安装", file=sys.stderr)
                break
            print(f"[meta-skills] [{i}/{len(repos)}] 正在处理: {repo.full_name} (stars: {repo.stars}) ...", file=sys.stderr)
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
                print(f"[meta-skills]   -> 已安装: {msg}", file=sys.stderr)
            else:
                reason = msg if not ok else "已存在"
                print(f"[meta-skills]   -> 跳过: {reason}", file=sys.stderr)
        print(f"[meta-skills] 完成。本次新安装 {len(installed)} 个，当前共 {len(installed_names)} 个。", file=sys.stderr)
        signal_reload(config)
        out = {"installed": installed, "total_installed": len(installed_names)}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        # 输出本次安装的技能简介（从 SKILL.md 的 description 解析）
        summary = get_installed_summary(config)
        by_name = {s["name"]: s["description"] for s in summary}
        if installed:
            print("\n# 本次安装的技能简介（来自 SKILL.md description）\n")
            for it in installed:
                name = it["name"]
                desc = by_name.get(name, "—")
                print(f"- **{name}**\n  {desc}\n")
        # 写入报告文件，便于在 OpenClaw 对话中让 AI 读取并发送到对话
        report_path = _write_search_install_report(
            installed, len(installed_names), by_name, BASE_DIR,
        )
        if report_path:
            print(f"\n[meta-skills] 结果已写入 {report_path}", file=sys.stderr)
            print("[meta-skills] 在 OpenClaw 对话中可说：「读取 meta-skills/reports 下最新的 search_install 报告并发到对话」即可将结果发给 AI。", file=sys.stderr)

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

    elif cmd == "record_edit" and len(sys.argv) >= 3:
        skill_name = sys.argv[2]
        source_url = sys.argv[3] if len(sys.argv) > 3 else ""
        record_skill_edit(skill_name, source_url=source_url)
        print("record_edit recorded")

    elif cmd == "usage_stats":
        data = load_rank_data(BASE_DIR)
        skills = data.get("skills", [])
        out = [{"name": s["name"], "use_count": s.get("use_count", 0), "edit_count": s.get("edit_count", 0)} for s in skills]
        print(json.dumps(out, ensure_ascii=False, indent=2))

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
        argc = len(sys.argv)
        sub = (sys.argv[2].lower() if argc >= 3 else "")
        if sub == "install":
            ok, msg = schedule_systemd_install(config)
            print(json.dumps({"ok": ok, "message": msg}))
        elif sub == "start":
            ok, msg = schedule_systemd_start()
            print(json.dumps({"ok": ok, "message": msg}))
        elif sub == "stop":
            ok, msg = schedule_systemd_stop()
            print(json.dumps({"ok": ok, "message": msg}))
        elif sub == "status":
            ok, msg = schedule_systemd_status()
            print(json.dumps({"ok": ok, "output": msg}))
        elif len(sys.argv) >= 4 and sys.argv[2].isdigit() and sys.argv[3].isdigit():
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
