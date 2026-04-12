# Job Hunter

> 面向有专业经验者的岗位发现工作台  
> A local-first job discovery workspace for experienced professionals

## 项目定位 / Project Positioning

Job Hunter 不是一个面向大众求职场景的职位推荐器。它更适合已经工作一段时间、拥有明确专业技能和职业方向的求职者：先从你的经验、目标方向和地域偏好出发，识别更值得跟踪的公司，再持续发现这些公司公开放出的岗位。

Job Hunter is not a generic job recommendation tool for mass-market job search. It is designed for professionals with real domain experience: start from your existing skills, career direction, and location preferences, identify companies that are more likely to need that expertise, and then discover open roles worth tracking.

很多通用求职平台更擅长把热门职位推给大量用户，但对系统工程、验证测试、MBSE、可靠性、数字孪生、能源装备等细分方向来说，岗位名称、组织结构和招聘表达往往并不标准化。真正匹配的机会，经常藏在“公司需要这类能力，但岗位标题不完全一样”的场景里。

Mainstream job platforms are often optimized for broad job discovery and high-volume recommendations. In specialized fields such as systems engineering, V&V, MBSE, reliability, digital twin, or energy equipment, job titles and hiring language are often inconsistent. Better opportunities are often hidden in companies that need the capability even when the role title does not match the resume exactly.

这个项目的核心思路是：先理解候选人已有的经验和可迁移能力，再推导哪些公司更可能需要这些能力，然后优先抓取公司官网、ATS 和有限的 Web 信号，最后把岗位匹配、状态维护和后续行动集中到一个本地工作台里。

The core idea is simple: understand the candidate's experience and transferable skills first, infer which companies are likely to value those skills, search company career pages and ATS sources before broad job boards, and then manage matching, review state, and follow-up actions in one local workspace.

一句话总结：**先找对公司，再找对岗位。**

In one sentence: **find the right companies before chasing the right roles.**

## 适合谁 / Who It's For

中文：

- 已经工作一段时间、具备明确专业能力的中高级求职者
- 细分行业或专业岗位人群，如系统工程、验证测试、MBSE、数字孪生、可靠性、能源装备等方向
- 希望跨行业迁移，但不想丢掉核心能力的人
- 不满足于“职位平台推荐流”，更希望主动建立目标公司池的人

English:

- Experienced candidates with clear professional skills and domain depth
- Specialists in areas such as systems engineering, validation and verification, MBSE, digital twin, reliability, or energy equipment
- People who want to move into adjacent industries without abandoning their core strengths
- People who prefer building a focused target-company pipeline instead of relying on generic platform feeds

## 当前仓库能做什么 / What The Repository Can Do Today

当前仓库主要包含一个正在持续迭代的本地桌面工作台，以及一套旧版岗位发现引擎参考实现。

Today the repository contains two practical layers: an evolving local desktop workspace and a legacy job discovery engine that still powers part of the search pipeline.

已落地能力包括：

- 本地候选人管理：维护姓名、邮箱、当前所在地、目标地区、备注和简历路径
- AI 目标岗位设立：辅助生成更具体的岗位方向，并维护中英文岗位名称和说明
- 本地 AI 设置：支持直接填写 API Key 或绑定环境变量，并验证模型可用性
- 公司优先的岗位发现流程：根据候选人的岗位方向和偏好，调用旧版搜索引擎做公司与岗位发现
- 搜索结果工作台：查看匹配结果，并维护关注、投递、Offer、放弃等状态
- 本地优先数据存储：通过 SQLite 保存候选人、搜索配置、结果状态和运行数据

Current implemented capabilities include:

- Local candidate management for names, contact info, location preferences, notes, and resume paths
- AI-assisted target-role setup with bilingual role names and descriptions
- Local AI settings with API key handling, environment-variable support, and model validation
- Company-first job discovery through the legacy engine based on candidate direction and preferences
- A results workspace for reviewing matches and maintaining focus, applied, offer, rejected, or dropped states
- Local-first persistence through SQLite for candidate data, search settings, review states, and runtime data

## 仓库结构 / Repository Structure

| Path | 中文说明 | English Description |
| --- | --- | --- |
| `desktop_app/` | 新版桌面应用，负责候选人工作台、AI 设置、搜索结果查看与后续维护 | The main desktop application for candidate workspaces, AI settings, result review, and follow-up workflows |
| `legacy_jobflow_reference/` | 旧版岗位发现参考引擎，当前仍作为搜索执行层被桌面应用调用 | The legacy job discovery engine still used as the current search execution layer |
| `docs/` | GitHub 说明文档，包括产品定位、架构和路线图 | Repository-facing documentation for positioning, architecture, roadmap, and setup guidance |
| `README_RELEASE.txt` | 面向打包发布目录的简要启动说明 | A short release-package startup note |
| `START_JOBFLOW_DESKTOP.cmd` | Windows 下的快速启动入口 | A Windows entry point to start the desktop app quickly |

## 核心流程 / Core Workflow

1. 创建候选人档案，导入简历，填写当前所在地、目标地区和补充说明。  
   Create a candidate profile, attach a resume, and set current location, preferred locations, and notes.
2. 通过 AI 推荐或手动补充方式，建立真正值得追踪的目标岗位方向。  
   Use AI suggestions or manual input to define role directions that are actually worth tracking.
3. 基于这些岗位方向生成搜索上下文，优先发现相关公司，再抓取公司官网或 ATS 的公开职位。  
   Build search context from those role directions, prioritize relevant companies, and then search official career pages or ATS listings.
4. 对岗位进行匹配分析和结果整理，在工作台里持续维护关注、投递和反馈状态。  
   Review job matches, organize results, and maintain interest, application, and outcome states inside the workspace.

## 当前边界 / Current Scope And Non-Goals

这个项目目前更接近一个“发现 + 筛选 + 管理”的本地工作台，而不是一个“全自动找工作/自动投递平台”。

At the moment this project is much closer to a local workspace for discovery, filtering, and tracking than to a fully automated job-search or auto-apply platform.

当前明确不应过度承诺的内容：

- 不是面向所有求职者的大众化职位推荐产品
- 不是自动投递系统
- 不是已经完成商业化打磨的成品桌面软件
- 不是完全脱离旧版引擎的独立新架构，当前搜索执行仍依赖 `legacy_jobflow_reference/`

Things the project should not overclaim today:

- It is not a broad, mass-market job recommendation product
- It is not an automatic application system
- It is not yet a fully polished commercial desktop product
- It is not yet a fully independent architecture divorced from the legacy engine

## 快速开始 / Quick Start

### 面向普通用户的下载方式 / Download For Non-Developers

如果你不是开发者，而是想直接双击使用，请优先到 GitHub Releases 下载最新的 Windows 发布包：

If you are not working from source and just want a double-clickable app, download the latest Windows release package from GitHub Releases:

- `Job-Hunter-<version>-win64.zip`
- 解压后启动 `Jobflow Desktop.exe`
- 这个发布包会自带桌面运行时、便携 Node、demo 候选人种子和安全模板

- unzip it and launch `Jobflow Desktop.exe`
- the package ships with the desktop runtime, portable Node, demo candidate seed, and safe templates

发布包不会包含真实候选人数据库、客户数据、搜索历史、导出结果或运行备份。

The release package does not include real candidate databases, customer data, search history, exports, or runtime backups.

### 方式一：直接启动桌面应用 / Option 1: Start The Desktop App Directly

在仓库根目录双击：

Double-click this entry point from the repository root:

```bat
START_JOBFLOW_DESKTOP.cmd
```

这个入口会调用 `desktop_app/run_release.ps1`，自动寻找本地 Python，并启动桌面应用。

This entry point calls `desktop_app/run_release.ps1`, locates a usable local Python installation, and starts the desktop app.

### 方式二：开发模式运行 / Option 2: Run In Development Mode

```powershell
cd .\desktop_app
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\jobflow-desktop
```

## 环境要求 / Environment

中文：

- Windows 开发环境优先
- Python 3.10+
- OpenAI API Key
- 如果要运行旧版搜索引擎，需要可用的 Node.js；也可以使用 `desktop_app/runtime/tools/` 下的便携 Node

English:

- Windows-first development environment for source checkout
- Python 3.10+ for source checkout
- An OpenAI API key
- A usable Node.js runtime for the legacy engine, or the portable Node binaries under `desktop_app/runtime/tools/`

从 GitHub Release 下载的 Windows 发布包不要求本地单独安装 Python。

The Windows release package downloaded from GitHub Releases does not require a separate local Python installation.

可用环境变量 / Supported environment variables:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `JOBFLOW_OPENAI_MODEL`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`
- `JOBFLOW_NODE_PATH`
- `JOBFLOW_PYTHON_PATH`

## 公开仓库边界 / Public Repository Boundary

这个仓库公开部分只保留源码、文档、演示种子和安全示例模板；个人简历、公司池、搜索结果、SQLite 数据和运行备份必须留在本地。

The public repository keeps only source code, documentation, demo seeds, and safe example templates. Personal resumes, company pools, search outputs, SQLite data, and runtime backups must remain local.

当前已经把这套边界写进 `.gitignore`、`legacy_jobflow_reference/.gitignore`、`scripts/privacy_audit.ps1` 和 GitHub Actions，所以未来同事协作时也会按同一规则执行。

This boundary is now enforced through `.gitignore`, `legacy_jobflow_reference/.gitignore`, `scripts/privacy_audit.ps1`, and GitHub Actions so future collaborators follow the same rules by default.

## 文档导航 / Documentation

- [更新记录 / Changelog](CHANGELOG.md)
- [产品定位 / Product Positioning](docs/PRODUCT_POSITIONING.md)
- [架构概览 / Architecture Overview](docs/ARCHITECTURE.md)
- [路线图 / Roadmap](docs/ROADMAP.md)
- [仓库边界 / Repository Boundary](docs/REPOSITORY_BOUNDARY.md)
- [GitHub 仓库设置建议 / GitHub Repo Setup Suggestions](docs/GITHUB_REPO_SETUP.md)
- [贡献说明 / Contributing](CONTRIBUTING.md)

## 贡献与讨论 / Contribute And Discuss

这个项目欢迎的不只是代码提交，也欢迎思路、实验方向和产品判断。我们尤其欢迎围绕“专业型人才如何更高效找到更匹配职位”这个问题展开合作。

This project welcomes more than code. It also welcomes ideas, experiments, product thinking, and research around one central question: how professionals can find better-fit opportunities more effectively.

欢迎贡献的方向包括：

- README、文档和 GitHub 展示信息的改进
- 新的 AI 功能，例如岗位方向生成、匹配解释、排序理由、候选公司摘要、工作流自动化
- 新的搜索引擎或数据源接入，例如更多 ATS、官网抓取方式、行业特定来源或地区性来源
- 更好的匹配逻辑、评分策略、候选公司发现方法和搜索启发式
- 职业发现方法、跨行业迁移策略，以及如何先找到更匹配公司的思路
- 更好的结果管理、审核状态、导出和工作流体验
- 新的产品想法、研究问题、评估方法、数据集和用户访谈结论

Useful contribution areas include:

- README, documentation, and GitHub presentation improvements
- New AI capabilities such as role generation, match explanation, ranking rationale, company summaries, or workflow automation
- New search engines or data sources, including more ATS systems, company-career parsing strategies, or domain-specific sources
- Better matching logic, scoring strategies, company discovery methods, and job-search heuristics
- Career discovery methods, cross-industry transition strategies, and ideas for finding better-fit companies earlier
- Better result review flows, status tracking, exports, and workspace UX
- New product ideas, research questions, evaluation methods, datasets, and user insights

如果 GitHub Discussions 还没有启用，也欢迎直接开 Issue 来讨论想法。你可以把问题写成功能提案、研究假设、数据源建议，或者“为什么某类专业人才很难被现有平台正确匹配”的分析。

If GitHub Discussions is not enabled, opening an Issue is still a valid way to discuss ideas. A proposal can be framed as a feature suggestion, a research hypothesis, a data-source idea, or an analysis of why existing platforms fail to match certain professional profiles well.

提 Issue 或 PR 前，建议先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
