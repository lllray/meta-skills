---
name: deploy-meta-skills
description: 引导 OpenClaw 完成 meta-skills 的完整部署。包括向用户说明部署步骤与所需信息、收集使用场景并拆解关键词、配置 GITHUB_TOKEN 与 meta-skills-rank-lists、安装全网高质量 skills（可配置上限与定时更新）。仅在用户要求部署 meta-skills 或“帮我部署 meta-skills”时使用。
---

# 部署 meta-skills 引导技能

当用户说「帮我部署 meta-skills」或给出 meta-skills 的 Git 链接（如 `git@github.com:lllray/meta-skills.git`）时，按以下步骤引导完成部署。

## 部署前：先告知用户

在开始前，向用户简要说明**整个部署过程**和**需要的信息**，让用户心里有数：

1. **部署步骤概览**
   - 了解您主要用 OpenClaw 做什么 → 拆解为技术关键词并保存（用于后续搜索优质 skills）
   - 配置 GitHub Token（用于搜索全网 skills，以及可选的上传使用数据）
   - 若您愿意回馈社区：在您 GitHub 下创建并初始化 **meta-skills-rank-lists** 仓库
   - 安装全网高质量 OpenClaw skills（默认上限 100 个，可改）
   - 汇报已安装的 skills 及简要能力
   - 说明每日 21:00 自动更新（时间可改）

2. **您需要准备**
   - 一个 **GitHub Token**（需 repo 权限，用于搜索与可选的上传）
   - 决定是否愿意将「本机对 skills 的使用情况」自动上传到您个人的 GitHub 仓库（用于回馈社区）

3. **部署后您将具备**
   - 根据您的使用场景关键词，自动发现并安装高星、高使用量 skills
   - 本机使用次数记录到本地，每日统一上传到您的 meta-skills-rank-lists（若已配置）
   - 每日定时（默认 21:00）自动检索并安装新技能、上传使用数据

---

## 步骤一：收集使用场景并拆解关键词

1. **向用户提问**  
   「您平时主要用 OpenClaw 做什么？请用一两句话描述（例如：写代码、写文档、做数据分析、自动化推特等）。」

2. **拆解技术关键词**  
   根据用户描述，拆解出可用来搜索 skills 的**技术关键词**（如：twitter、calendar、pdf、git、api、notion、database 等）。不要编造用户没提到的领域。

3. **写入 meta-skills 并支持后续维护**
   - **首次**：将拆解出的关键词写入 meta-skills。在 meta-skills 项目根目录执行：
     ```bash
     cd <meta-skills 根目录>
     python manager.py keywords update 关键词1 关键词2 关键词3
     ```
   - **用户之后想增加关键词**：执行
     ```bash
     python manager.py keywords add 新关键词1 新关键词2
     ```
   - **用户之后想整体更换描述/关键词**：再次拆解后执行
     ```bash
     python manager.py keywords update 新关键词1 新关键词2
     ```
   - 查看当前关键词：`python manager.py keywords get`

---

## 步骤二：索要 GITHUB_TOKEN 与是否上传使用数据

1. **说明 Token 用途**  
   向用户说明：「需要您提供一个 **GitHub Token**，用于在 GitHub 上搜索全网优秀的 OpenClaw skills，以及（若您同意）将您本机对 skills 的使用情况自动上传到您个人的 GitHub 仓库。」

2. **询问是否愿意上传**  
   「您是否愿意把本机对 skills 的**使用情况**（如调用次数）自动上传到您个人的 GitHub 仓库，用于回馈社区？若愿意，我会在您账号下创建仓库 **meta-skills-rank-lists** 并完成初始化。」

3. **若用户提供 Token**
   - 将 Token 写入 meta-skills 的**本地配置**（不提交到 Git），便于后续使用：
     ```bash
     cd <meta-skills 根目录>
     python manager.py config set github.token <用户提供的 token>
     ```
   - 若用户**愿意上传**：为其创建 **meta-skills-rank-lists** 并初始化。需要先确定用户的 GitHub 用户名（可从 Token 对应账号或直接问用户）。然后执行：
     ```bash
     python manager.py create_rank_repo <GitHub 用户名>
     ```
     成功后，meta-skills 会自动把 `rank_lists.repo` 记为 `用户名/meta-skills-rank-lists`。
   - 若用户**不愿意上传**：跳过创建仓库，仅用 Token 做搜索；`rank_lists.repo` 可留空（后续用户也可通过 OpenClaw 再配置）。

4. **记录/修改 rank-lists 链接**  
   若用户之后要改用其他仓库或修改链接，可执行：
   ```bash
   python manager.py config set rank_lists.repo 用户名/meta-skills-rank-lists
   ```

---

## 步骤三：安装全网高质量 skills（默认上限 100）

1. **说明上限**  
   告知用户：「将为您安装全网高质量的 OpenClaw skills，默认**最多安装 100 个**。您之后可以通过 OpenClaw 让我帮您修改这个上限。」

2. **执行安装**  
   使用已保存的关键词进行搜索并安装（会受 `max_skills` 上限限制）。若当前关键词为空，可用 `openclaw` 等默认词：
   ```bash
   cd <meta-skills 根目录>
   python manager.py search_install "关键词1 关键词2"
   ```
   可对多个关键词分别执行，直到达到上限或没有更多结果。

3. **修改上限（用户提出时）**  
   meta-skills 支持通过配置修改上限，例如改为 50 或 200：
   ```bash
   python manager.py max_skills 50
   ```
   查看当前上限：`python manager.py max_skills`

---

## 步骤四：汇报安装结果与简要能力

安装完成后：

1. **拉取已安装列表及简要能力**  
   执行：
   ```bash
   python manager.py installed_summary
   ```
   输出为 JSON，包含每个 skill 的 `name`、`source_url`、`description`（来自 SKILL.md 的 description，简要能力）。

2. **向用户汇报**  
   用自然语言汇总：共安装了多少个 skills，并**按类别或名称列出**，每个附一句简要能力说明（来自上面的 description）。

---

## 步骤五：说明每日自动更新与修改时间

1. **告知默认行为**  
   「已安装的 skills 会在**每天 21:00** 自动更新：会再次检索并安装新的高质量 skills（仍受上限限制），并将您本日的使用情况上传到您的 meta-skills-rank-lists（若已配置）。」

2. **修改时间（用户提出时）**  
   meta-skills 支持修改每日执行时间，例如改为 22:30：
   ```bash
   python manager.py schedule 22 30
   ```
   查看当前定时：`python manager.py schedule`

---

## 与 meta-skills 的接口汇总（供 OpenClaw 调用）

| 目的 | 命令 |
|------|------|
| 设置/替换关键词 | `python manager.py keywords update k1 k2 k3` |
| 追加关键词 | `python manager.py keywords add k1 k2` |
| 查看关键词 | `python manager.py keywords get` |
| 写入 Token（本地） | `python manager.py config set github.token <token>` |
| 创建并初始化 rank-lists 仓库 | `python manager.py create_rank_repo <GitHub用户名>` |
| 设置 rank-lists 仓库 | `python manager.py config set rank_lists.repo 用户名/meta-skills-rank-lists` |
| 查看/设置安装上限 | `python manager.py max_skills` / `python manager.py max_skills 100` |
| 搜索并安装 | `python manager.py search_install "关键词"` |
| 已安装列表与能力 | `python manager.py installed_summary` |
| 查看/设置每日执行时间 | `python manager.py schedule` / `python manager.py schedule 21 0` |

以上命令均需在 **meta-skills 项目根目录**下执行（即包含 `manager.py`、`config.yaml` 的目录）。
