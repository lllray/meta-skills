---
name: deploy-meta-skills
description: 当用户说「帮我部署 meta-skills」或给出其 GitHub 链接时，在 OpenClaw 所在环境中将 meta-skills 安装为元技能并执行部署。不要当作服务器/Docker 部署，不要询问部署目标，直接按本技能步骤执行。meta-skills 用于发现、安装与每日自动更新 OpenClaw 技能，扩展用户 OpenClaw 能力。
---

# 部署 meta-skills 引导技能

**触发条件**：用户消息中包含「帮我部署 meta-skills」或「部署 meta-skills」且带有本仓库链接（如 `https://github.com/lllray/meta-skills`）时，**直接**按本技能执行，不要先回复「本机/服务器/Docker 选哪种」或「部署到哪里」。

---

## 执行方式：用 Exec 在本机（gateway）跑命令

部署需在本机执行 `git clone`、`pip install`、`python manager.py`。OpenClaw 的 **Exec 工具** 可在 **gateway**（本机）执行，**直接调用**，指定 `host=gateway`；工作目录为 OpenClaw 技能目录（如 `~/.openclaw/skills/`）。

**若无法在本机执行**：说明当前 Exec 审批（`~/.openclaw/exec-approvals.json`）不允许本机执行。请让用户任选其一：
- 会话内发送：`/exec host=gateway security=full`，然后重试部署；
- 或编辑 `exec-approvals.json` 将 `security` 设为 `full`；
- 或手动复制你给出的命令到终端执行，把输出贴回继续。

克隆与后续命令的目录：克隆到 `~/.openclaw/skills/meta-skills`，后续所有 `python manager.py` 在该目录下执行。

---

## 必须理解（执行前必读）

当用户说「**帮我部署 meta-skills**」或给出 meta-skills 链接时：

- **含义**：在 **当前 OpenClaw 所在环境** 安装 meta-skills，作为 **元技能**，用于 **发现、安装、每日自动更新** 其他 OpenClaw 技能，从而持续扩展用户能力。
- **不要** 理解为在服务器或 Docker 部署独立服务；**不要** 询问部署目标；**直接** 按以下步骤执行。

若尚未克隆，先执行：
```bash
git clone https://github.com/lllray/meta-skills.git ~/.openclaw/skills/meta-skills
cd ~/.openclaw/skills/meta-skills
pip install -r requirements.txt
```
此后所有 `manager.py` 命令均在 **该目录** 下执行。

---

## (a) 部署前：向用户告知步骤与所需信息

简要说明：

1. **部署步骤**
   - 了解您主要用 OpenClaw 做什么 → 拆解为搜索关键词
   - 配置 **GitHub Token**（仅用于在 GitHub 上搜索并安装 skills）
   - 根据关键词安装全网高质量 OpenClaw skills（默认最多 100 个，可改）
   - 汇报已安装的 skills 及简要能力
   - 说明**每日 21:00 自动更新**（会再次检索并安装新 skills，时间可改）；更新结果写入报告文档，**OpenClaw 可通过飞书**将当日新安装列表与说明发给用户。

2. **需要准备**
   - 一个 **GitHub Token**（需 repo 权限，用于搜索与 clone 仓库）

3. **部署后具备**
   - 按使用场景关键词发现并安装高星、近期活跃的 skills
   - **每日定时**自动检索并安装新技能，无需手动维护
   - **每日更新报告**：新安装的技能列表与说明写入 `reports/daily_update_YYYY-MM-DD.md`，可由 OpenClaw 通过飞书发给用户

---

## (b) 收集使用场景并拆解关键词

1. **向用户提问**  
   「您平时主要用 OpenClaw 做什么？请用一两句话描述（例如：写代码、写文档、做数据分析、自动化推特等）。」

2. **拆解技术关键词**  
   根据描述拆解出**技术关键词**（如：twitter、calendar、pdf、git、api、notion、database）。不要编造用户没提到的领域。

3. **后续用法**  
   这些关键词将直接用于本次「搜索并安装」；若用户之后想用新关键词追加安装，可再次执行 `search_install "新关键词"`。如需让**每日自动更新**也使用固定关键词，可在 `config.yaml` 的 `rank_lists.keywords` 中配置列表（可选）。

---

## (c) 索要 GITHUB_TOKEN 并写入配置

1. **说明用途**  
   「需要您提供一个 **GitHub Token**，仅用于在 GitHub 上搜索并安装 OpenClaw skills，不会上传任何数据。」

2. **写入本地配置**  
   用户提供 Token 后，在 meta-skills 项目根目录执行：
   ```bash
   python manager.py config set github.token <用户提供的 token>
   ```
   Token 会写入 `config.local.yaml`（不提交到 Git）。

---

## (d) 安装全网高质量 skills（默认上限 100）

1. **说明**  
   告知用户：「将根据刚才的关键词为您安装全网高质量的 OpenClaw skills，默认**最多 100 个**。之后可通过 OpenClaw 让我帮您改上限或追加关键词安装。」

2. **首次检索提示**  
   在执行 `search_install` 之前，务必告知用户：**「首次检索 SKILL 需要较长时间，请耐心等待。」**（会从 GitHub 与配置的 awesome 列表拉取并校验大量仓库，可能持续数分钟。）

3. **执行安装**  
   用 (b) 中拆解的关键词执行（多个关键词可空格连接）：
   ```bash
   python manager.py search_install "关键词1 关键词2 关键词3"
   ```
   若用户未提供用途，可用默认：`python manager.py search_install "openclaw"`  
   **重要**：执行后请将 **Exec 返回的完整标准输出与标准错误** 作为你的回复内容展示在对话中（可整理为代码块或引用），方便用户与 AI 基于安装结果继续对话。同时结果会写入 `meta-skills/reports/search_install_YYYY-MM-DD_HH-MM-SS.md`，若用户是在终端手动执行的 search_install，可在对话中说「读取 meta-skills/reports 下最新的 search_install 报告并发到对话」，你读取该文件并把内容发到对话即可。

4. **修改上限（用户提出时）**  
   ```bash
   python manager.py max_skills 50
   ```
   查看当前上限：`python manager.py max_skills`

---

## (e) 汇报安装结果与简要能力

1. **拉取列表**  
   ```bash
   python manager.py installed_summary
   ```
   输出为 JSON：每个 skill 的 `name`、`source_url`、`description`（来自 SKILL.md）。

2. **向用户汇报**  
   用自然语言汇总：共安装了多少个 skills，按名称列出并附一句简要能力（来自 description）。若本次是通过 Exec 执行了 `search_install`，**请将 Exec 返回的完整输出（含进度与技能简介）一并展示在对话中**，便于用户与 AI 基于结果继续对话。

3. **结果报告文件**  
   `search_install` 执行完成后会自动写入 `meta-skills/reports/search_install_YYYY-MM-DD_HH-MM-SS.md`。若用户是在终端手动执行的，可在对话中让 AI「读取 reports 下最新的 search_install 报告并发到对话」即可把结果发到对话窗口。

---

## (f) 说明每日自动更新与飞书通知

1. **告知**  
   「已安装的 skills 会在**每天 21:00** 自动更新：再次从 GitHub 检索并安装新的高质量 skills（仍受上限限制）。定时任务使用**系统 systemd** 运行，**首次安装时会默认启用并启动**，无需手动跑进程。」

2. **定时任务（systemd）**  
   - **安装并默认启动**（部署时在完成 search_install 后执行一次，首次即启用）：
     ```bash
     python manager.py schedule install
     ```
     会将用户级 systemd 单元写入 `~/.config/systemd/user/`，按当前配置时间（默认 21:00）每天执行 `daily_run`，并立即 enable + start 定时器。
   - **启动定时器**：`python manager.py schedule start`
   - **关闭定时器**：`python manager.py schedule stop`
   - **查询定时任务状态**：`python manager.py schedule status`  
     可据此回复用户「定时任务已启用/未运行」或贴出状态输出。
   - **修改执行时间（用户提出时）**：先改配置再重装定时器：
     ```bash
     python manager.py schedule 22 30
     python manager.py schedule install
     ```
   - 仅查看当前配置时间：`python manager.py schedule`

3. **更新结果写入文档**  
   每日更新若有**新安装**的技能，会自动生成报告文档：
   - **路径**：`~/.openclaw/skills/meta-skills/reports/daily_update_YYYY-MM-DD.md`
   - **内容**：当日新安装的技能列表、来源、以及每个技能的简要说明（来自 SKILL.md 的 description）。

4. **通过飞书发给用户**  
   - 部署时告知用户：**每日更新完成后，OpenClaw 可通过飞书把当日更新报告发给您**（发到您指定的飞书群或会话）。
   - 若用户已配置飞书（或使用 OpenClaw 的飞书相关技能），在**每日定时任务执行后**，OpenClaw 应：
     1. 读取当日报告文件（路径同上，日期为当日 `YYYY-MM-DD`）；
     2. 使用飞书技能/机器人将报告内容发送给用户（或用户指定的飞书群/会话）。
   - 若用户尚未配置飞书，可说明：配置飞书后即可在每日更新后自动收到新安装技能列表与说明。

---

## 与 meta-skills 的接口汇总（供 OpenClaw 调用）

| 目的 | 命令 |
|------|------|
| 写入 Token（本地） | `python manager.py config set github.token <token>` |
| 搜索并安装 | `python manager.py search_install "关键词1 关键词2"` |
| 已安装列表与能力 | `python manager.py installed_summary` |
| 查看/设置安装上限 | `python manager.py max_skills` / `python manager.py max_skills 100` |
| 查看/设置每日执行时间 | `python manager.py schedule` / `python manager.py schedule 21 0` |
| **定时任务：安装并默认启动** | `python manager.py schedule install` |
| **定时任务：启动** | `python manager.py schedule start` |
| **定时任务：关闭** | `python manager.py schedule stop` |
| **定时任务：查询状态** | `python manager.py schedule status` |
| 手动执行一次「每日更新」 | `python manager.py daily_run` |

**search_install 结果发到对话**：执行 `search_install` 后，请将 Exec 返回的**完整输出**展示在对话中；若用户在终端手动执行了 search_install，可让用户说「读取 meta-skills/reports 下最新的 search_install 报告并发到对话」，你读取 `~/.openclaw/skills/meta-skills/reports/` 下最新一份 `search_install_*.md` 并把内容发到对话，即可将结果发给 AI。

**每日更新报告**：`daily_run` 若有新安装，会写入 `meta-skills/reports/daily_update_YYYY-MM-DD.md`；OpenClaw 应读取该文件并通过飞书发给用户。

以上命令均在 **meta-skills 项目根目录**（`~/.openclaw/skills/meta-skills`）下执行。
