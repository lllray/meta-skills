# meta-skills

OpenClaw 的 **元技能** 仓库：根据关键词从 GitHub 与 **meta-skills-rank-lists** 发现高使用量技能、安装、并将**用户调用次数**作为打分来源记录到本地 JSON，**每日统一上传**到用户自己的 GitHub 仓库 **meta-skills-rank-lists**。默认**每天 21:00** 自动执行一次：检索安装推荐技能 + 上传当日使用与评分。

---

## OpenClaw 自动部署（推荐）

当用户说：「帮我部署 meta-skills，链接在 https://github.com/lllray/meta-skills 」时，OpenClaw 应：

1. **克隆本仓库**  
   将 `https://github.com/lllray/meta-skills` 或 `git@github.com:lllray/meta-skills.git` 克隆到本地（例如 `~/.openclaw/skills/meta-skills` 或当前工作目录下的 `meta-skills`）。

2. **安装依赖**  
   在克隆后的项目根目录执行：  
   `pip install -r requirements.txt`

3. **按「部署引导技能」完成部署**  
   打开并**严格按步骤执行**本仓库中的部署引导技能：  
   **`skills/deploy-meta-skills/SKILL.md`**  
   该技能会引导你：向用户说明部署步骤与所需信息 → 询问用户主要用途并拆解关键词（写入 meta-skills，支持后续添加/更新）→ 索要 GITHUB_TOKEN 并询问是否上传使用数据到个人 GitHub → 若愿意则创建并初始化用户的 **meta-skills-rank-lists** 仓库 → 安装全网高质量 skills（默认上限 100，可改）→ 汇报已安装 skills 及简要能力 → 说明每日 21:00 自动更新（时间可改）。  
   所有对 meta-skills 的配置与操作（关键词、Token、rank-lists 链接、安装上限、定时）均通过该技能中列出的 `manager.py` 命令完成。

4. **可选：启动每日定时**  
   部署完成后，若需每日自动更新，可在项目根目录执行：  
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

## 依赖

- Python 3.10+
- PyYAML（见 `requirements.txt`）
- Git、可选 Docker（沙箱验证）

```bash
pip install -r requirements.txt
```
