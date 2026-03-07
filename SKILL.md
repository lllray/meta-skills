---
name: meta-skills
description: 元技能，管理 OpenClaw 技能的发现、安装与排名。根据关键词从 GitHub 和 meta-skills-rank-lists 检索高使用量技能并安装；用户调用次数记录到本地 JSON，每日上传到用户自己的 meta-skills-rank-lists 仓库；默认每天 21:00 自动运行检索安装与上传。Use when the user wants to find, install, or manage OpenClaw skills; search by keyword; or sync rank data to GitHub.
metadata:
  {"openclaw":{"primaryEnv":"GITHUB_TOKEN","emoji":"📦"}}
---

# meta-skills（元技能）

本仓库名为 **meta-skills**，是一个 Meta-Skill，用于管理其他 OpenClaw 技能的发现、安装与排名数据同步。

## 何时使用

- 用户说：「帮我找找 GitHub 上最火的 XXX 技能并自动安装」
- 需要根据关键词搜索并安装技能
- 查看已安装技能或排名
- 将本机使用情况同步到自己的 meta-skills-rank-lists 仓库

## 能力概览

| 模块 | 说明 |
|------|------|
| **发现** | GitHub API 搜索 + 从 meta-skills-rank-lists 检索使用/评价较高的技能 |
| **安装** | 沙箱验证后放入 `~/.openclaw/skills/`，首次安装后更新 rank-lists |
| **打分** | 目前唯一来源：用户调用次数，记录到本地 JSON，每日统一上传 |
| **定时** | 默认每天 21:00 运行：检索安装推荐技能 + 上传今日使用与评分 |

## 配置

- 配置文件：`{baseDir}/config.yaml`
- **GitHub Token**：`GITHUB_TOKEN`（必填，用于 API 与推送 rank-lists）
- **meta-skills-rank-lists**：在 config 中配置 `rank_lists.repo`（如 `yourname/meta-skills-rank-lists`），需先在 GitHub 创建该空仓库。首次安装技能后会更新该仓库的已安装情况与 README。

## 常用指令

1. **搜索并安装**  
   `python manager.py search_install "关键词"`  
   会同时写入本地 `rank_data.json`，并在配置了 rank_lists 时上传已安装情况。

2. **记录一次使用（打分来源）**  
   `python manager.py record <skill_name> [source_url]`  
   每次用户调用某技能后执行，写入本地 JSON，每日任务会统一上传。

3. **查看评分**  
   `python manager.py scores [skill_name]`

4. **优先级**  
   `python manager.py priority <skill_name> <数字>`

5. **手动执行每日任务**  
   `python manager.py daily_run`  
   执行检索安装 + 上传 rank 到 meta-skills-rank-lists。

6. **仅上传排名数据**  
   `python manager.py upload_rank`

7. **启动每日 21:00 定时**  
   `python scheduler.py`  
   会一直运行，每天 21:00 执行一次 daily_run。

## meta-skills-rank-lists 仓库说明

- 用户需在 GitHub 创建仓库 **meta-skills-rank-lists**，并在 config 中填写 `rank_lists.repo`。
- 自动上传内容：`rank_data.json`（技能列表、使用次数、修改次数）与 **README.md**。
- README 首页展示：用户关键词、已自动安装技能数量、技能平均使用次数（排名依据）、表格：Skill 名称、原 GitHub 链接、用户修改次数、用户使用次数。

实现与数据：`manager.py`、`rank_store.py`、`db_handler.py`；本地数据 `rank_data.json`、`meta_skills.db`。
