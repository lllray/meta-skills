# meta-skills

OpenClaw 的 **元技能** 仓库：根据关键词从 GitHub 与 **meta-skills-rank-lists** 发现高使用量技能、安装、并将**用户调用次数**作为打分来源记录到本地 JSON，**每日统一上传**到用户自己的 GitHub 仓库 **meta-skills-rank-lists**。默认**每天 21:00** 自动执行一次：检索安装推荐技能 + 上传当日使用与评分。

---

## 自动部署（将链接复制粘贴给AI/opnclaw，推荐）

1. **克隆到 OpenClaw 技能目录**  
   将本仓库克隆到 **OpenClaw 的技能目录**（即当前 OpenClaw 环境加载 skills 的位置，通常为 `~/.openclaw/skills/`），目录名为 `meta-skills`：
   ```bash
   git clone https://github.com/lllray/meta-skills.git ~/.openclaw/skills/meta-skills
   ```
   若当前工作目录即为 OpenClaw 工作区，也可克隆到 `./skills/meta-skills`，确保 OpenClaw 能加载到该技能。

2. **安装依赖**  
   进入克隆后的 **meta-skills 项目根目录** 执行：
   ```bash
   cd ~/.openclaw/skills/meta-skills
   pip install -r requirements.txt
   ```

3. **按部署引导技能执行完整流程（不要跳过、不要改顺序）**  
   打开本仓库内的 **`skills/deploy-meta-skills/SKILL.md`**，**从第一段「必须理解」开始，严格按步骤执行**：
   - **(a)** 向用户告知整个部署过程的步骤、需要的信息、部署后具备的功能；
   - **(b)** 询问用户主要用 OpenClaw 做什么，拆解为技术关键词，用 `manager.py keywords update/add` 写入 meta-skills（支持后续添加或更新）；
   - **(c)** 索要 GITHUB_TOKEN，说明用于搜索全网优秀 skills；询问是否愿意将本机对 skills 的使用情况自动上传到个人 GitHub；若愿意，用 `create_rank_repo` 创建并初始化用户的 **meta-skills-rank-lists**，并存储 token 与 rank-lists 链接（支持后续修改）；
   - **(d)** 安装全网高质量 skills，默认上限 100（用户可通过 OpenClaw 修改）；
   - **(e)** 安装完毕后汇报已安装的 skills 及简要能力；
   - **(f)** 告知用户每日 21:00 自动更新（时间可通过 OpenClaw 修改）。

4. **可选：启动每日定时**  
   在 meta-skills 项目根目录执行（若需每日自动更新）：  
   `python scheduler.py`  
   （需常驻进程或配合 systemd/cron）

---

## 手动一键部署

1. **安装到 OpenClaw**

   ```bash
   # 克隆到 OpenClaw 技能目录（仓库名为 meta-skills）
   git clone https://github.com/lllray/meta-skills ~/.openclaw/skills/meta-skills
   ```

2. **配置**

   - 环境变量 **GITHUB_TOKEN**（必填）：用于 GitHub API 与推送 meta-skills-rank-lists。
   - 在 GitHub 创建仓库 **meta-skills-rank-lists**，在 `config.yaml` 中设置：
     - `rank_lists.repo`: `yourname/meta-skills-rank-lists`
     - `rank_lists.keywords`: 如 `["twitter", "calendar", "pdf"]`（用于 README 展示与搜索偏好）
     - 可选 `rank_lists.discovery_repos`: 其他用户的 rank 仓库，用于发现高使用量技能。

3. **使用**

   - 搜索并安装：`python manager.py search_install "关键词"`
   - 每次用户调用某技能后记录（打分）：`python manager.py record <skill_name> [source_url]`
   - 每日自动任务：`python manager.py daily_run`
   - 启动每天 21:00 定时：`python scheduler.py`（需常驻进程或配合 cron/systemd）

## 目录结构

```
meta-skills/
├── SKILL.md          # 元技能描述
├── manager.py        # 发现、安装、每日任务、CLI
├── db_handler.py     # SQLite 已安装技能与优先级
├── rank_store.py     # 本地 JSON 与 meta-skills-rank-lists 同步、README 生成
├── scheduler.py      # 每日 21:00 定时
├── config.yaml       # GitHub、rank_lists、schedule、openclaw 等
├── validate.py       # 自检
├── requirements.txt
└── README.md
```

## 打分与上传

- **打分来源**：目前仅**用户调用该 skill 的次数**。每次调用通过 `python manager.py record <skill_name>` 记录到本地 **rank_data.json**。
- **上传**：每日任务（或手动 `upload_rank`）将 **rank_data.json** 与自动生成的 **README.md** 推送到用户的 **meta-skills-rank-lists** 仓库。
- **README 首页**：展示用户关键词、已安装技能数、技能平均使用次数、表格（Skill 名称、原 GitHub 链接、用户修改次数、用户使用次数）。

## 每日 21:00 任务

- 默认在 **21:00** 执行一次（由 `scheduler.py` 或系统 cron 触发）：
  1. 从 **rank_lists.repo** 与 **rank_lists.discovery_repos** 拉取 rank 数据，安装高使用量且本机未安装的技能；
  2. 用 **rank_lists.keywords** 做 GitHub 搜索并安装新技能；
  3. 将本地 **rank_data.json** 与 README 推送到 **meta-skills-rank-lists**（若有使用数据）。

可在 `config.yaml` 的 `schedule` 中修改 `hour`、`minute` 或关闭 `enabled`。

## 安装流程说明（search_install 做了什么）

不是「只 clone」：会按顺序执行以下步骤，**只有全部通过**才会计入「已安装」：

1. **GitHub 搜索**：按关键词 + `topic:openclaw-skill` + stars>50 + 最近 3 个月有推送，取最多 `max_results_per_search`（默认 20）个仓库。
2. **逐个安装**：对每个仓库 **clone** → **解析 SKILL.md 取 name**（无则跳过）→ **若有 validate.py 则在 Docker 中执行**（失败则跳过）→ **复制到 `~/.openclaw/skills/<技能名>/`** 并登记。

OpenClaw 会从该目录扫描并加载 SKILL.md，无需再跑其他安装命令。安装结束后会打印本次安装的技能简介；也可执行 `python manager.py installed_summary` 查看全部已安装技能及 description。

## 依赖

- Python 3.10+
- PyYAML（见 `requirements.txt`）
- Git、可选 Docker（沙箱验证）

```bash
pip install -r requirements.txt
```
