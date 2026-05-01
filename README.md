# Job Hunter

<!-- README HERO START -->
<p align="center">
  <img src="./assets/readme-hero/hero.png" alt="Job Hunter concept hero image" width="100%" />
</p>

<p align="center">
  <strong>A local-first desktop workspace for building persistent job-search evidence and company memory.</strong>
</p>
<!-- README HERO END -->

<p align="center">
  <img src="desktop_app/assets/app_icon.png" alt="Job Hunter app icon" width="96">
</p>

> A local-first job discovery workspace for experienced professionals: not a one-shot job search, but a persistent place to build company memory, role evidence, and follow-up state over time.
>
> English lead content comes first; a full Chinese mirror follows below. End users should go straight to the Windows download section; developers and collaborators can use the source instructions.

## Product Preview

Screenshots are generated from the built-in demo candidate and public example links, with no real candidate data.

### Candidate Directory

![Job Hunter candidate entry](docs/images/readme-screenshot-directory.png)

### Job Search Workspace

![Job Hunter search workspace](docs/images/readme-screenshot-search-results.png)

### Search Feedback Loop

![Job Hunter workflow](docs/images/readme-workflow.svg)

## English

### Project Positioning

Job Hunter is not another job-list tool. It is built for a longer search problem: when you are not casually browsing, but trying to find a better long-term direction, one-shot search results age quickly. You need to remember which companies are worth tracking, which roles were already reviewed, which patterns keep appearing, and which leads were noise.

The product turns each search round into accumulated evidence. It discovers and verifies concrete roles, scores fit, then writes strong role signals back into a local company pool. The next round starts from that history instead of starting from zero.

In one sentence: **search engines are useful for today; Job Hunter is for building a reusable job-search workspace over time.**

### Who It's For

- Experienced candidates with clear professional skills and domain depth
- Specialists in areas such as systems engineering, validation and verification, MBSE, digital twin, reliability, or energy equipment
- People who want to move into adjacent industries without abandoning their core strengths
- People who prefer building a focused target-company pipeline instead of relying on generic platform feeds

### Why Use It Repeatedly

- You do not re-explain yourself every time: candidate profile, target direction, location preferences, and result state stay local.
- You do not repeatedly review the same roles: discovered, analyzed, and handled jobs are tracked.
- You are not limited to popular job titles: the app follows companies, career endpoints, and historical match signals.
- Good jobs improve the company pool: a strong role can make its company a better future source.
- Follow-up stays in one place: interest, applied, offer, rejected, and dropped states are maintained in the workspace.

### Search Logic

1. Read the candidate profile and target role directions.
2. Discover new concrete roles, deduplicate them, and verify that they are still open.
3. Score verified roles and decide whether they are worth recommending.
4. Write strong role sources back into the company pool.
5. Continue from that company pool through company career pages, ATS sources, and public hiring signals.
6. Save role results, company evidence, and user follow-up state locally for the next round.

The flow stays sequential and inspectable. The difference is that it can learn from both directions: companies lead to jobs, and good jobs strengthen the company pool.

### AI-Native Search Logic

Job Hunter does not treat AI as a chat layer wrapped around a fixed keyword search. It uses AI where hard-coded software rules are weakest: unstable role titles, ambiguous responsibility descriptions, adjacent-domain transitions, whether a company may need a capability, and why a role is worth tracking.

The deterministic parts still stay ordinary and inspectable: local storage, run state, result history, review status, and desktop workflow. AI handles the open-ended language and matching layer so complex job-search judgment does not have to be forced into fixed keywords and exhaustive rule lists.

### Where To Start

If you only want to use the app, follow the end-user path first. If you want to modify code, debug behavior, or collaborate on development, use the developer path below.

#### End Users: Download The Windows Build

If you are not a developer and just want to use the app, do not start from the source code and do not install Python locally. Go straight to GitHub Releases and download the Windows build.

- Latest release:
  [https://github.com/liuyingxuvka/Job-Hunter/releases/latest](https://github.com/liuyingxuvka/Job-Hunter/releases/latest)
- All releases:
  [https://github.com/liuyingxuvka/Job-Hunter/releases](https://github.com/liuyingxuvka/Job-Hunter/releases)
- File to download:
  `Job-Hunter-[version]-win64.zip`

Recommended steps:

1. Open the `latest release` link above.
2. Download `Job-Hunter-[version]-win64.zip`.
3. Extract it to a normal folder instead of running it inside the zip archive.
4. Double-click `Jobflow Desktop.exe`.
5. If Windows is cautious about launching the `.exe` directly, use `START_JOBFLOW_DESKTOP.cmd` instead.
6. After the first launch, enter your API settings in the app and start using it.

The release package already includes the desktop runtime, demo candidate seed, and safe templates. End users do not need to install Python locally.

The release package does not include real candidate databases, customer data, search history, exports, or runtime backups.

The packaged app stores user databases, exports, logs, and search runtime state under the local user profile. On startup, it quietly checks GitHub Releases for newer versions. When an update is downloaded and verified, the workspace-header update capsule lets you install it now with a restart or leave it for later.

#### Developers: Run From Source

The rest of this section is only for developers and collaborators.

Development environment requirements:

- Windows-first development environment
- Python 3.10+
- An OpenAI API key

The Windows release package downloaded from GitHub Releases does not require a separate local Python installation.

Supported environment variables:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `JOBFLOW_OPENAI_MODEL`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`
- `JOBFLOW_PYTHON_PATH`

Source-start option A: start from the repository root

Double-click this entry point from the repository root:

```bat
START_JOBFLOW_DESKTOP.cmd
```

This entry point calls `desktop_app/run_release.ps1`, locates a usable local Python installation, and starts the desktop app.

Source-start option B: run in development mode under `desktop_app/`

```powershell
cd .\desktop_app
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\jobflow-desktop
```

If you are working on CLI, automation, or AI integrations, go straight to:

- [AI Agent Discovery](docs/AI_AGENT_DISCOVERY.md)
- [AI Integration Notes](docs/AI_INTEGRATION.md)

Those details stay in a separate document instead of being expanded on the GitHub front page.

### What The Repository Can Do Today

Today the repository contains an evolving local desktop workspace plus a Python-native search pipeline for role discovery, company-pool maintenance, and result tracking.

Current implemented capabilities include:

- Local candidate management for names, contact info, location preferences, notes, and resume paths
- AI-assisted target-role setup with bilingual role names and descriptions
- Local AI settings with API key handling, environment-variable support, and model validation
- Role discovery plus company-pool feedback, where strong verified roles can improve the future source set
- A results workspace for reviewing matches and maintaining focus, applied, offer, rejected, or dropped states
- Local-first persistence with SQLite as the primary store for candidate data, search settings, review states, and runtime data; `desktop_app/runtime/search_runs/` is only a per-candidate transient workspace

### Core Workflow

1. Create a candidate profile, attach a resume, and set current location, preferred locations, and notes.
2. Use AI suggestions or manual input to define role directions that are actually worth tracking.
3. Build search context from those role directions, discover and verify concrete roles, and maintain a reusable company pool.
4. Review job matches, organize results, and maintain interest, application, and outcome states inside the workspace.

### Repository Structure

| Path | Description |
| --- | --- |
| `desktop_app/` | The main desktop application for candidate workspaces, AI settings, result review, and follow-up workflows |
| `docs/` | Repository-facing documentation for positioning, architecture, roadmap, and setup guidance |
| `README_RELEASE.txt` | A short release-package startup note |
| `START_JOBFLOW_DESKTOP.cmd` | A Windows entry point to start the desktop app quickly |

### Current Scope And Non-Goals

At the moment this project is much closer to a local workspace for discovery, filtering, and tracking than to a fully automated job-search or auto-apply platform.

Things the project should not overclaim today:

- It is not a broad, mass-market job recommendation product
- It is not an automatic application system
- It is not yet a fully polished commercial desktop product
- It is not yet the final long-term architecture for every platform and operating mode

### Public Repository Boundary

The public repository keeps only source code, documentation, demo seeds, and safe example templates. Personal resumes, company pools, search outputs, SQLite data, and runtime backups must remain local.

This boundary is now enforced through `.gitignore`, `scripts/privacy_audit.ps1`, and GitHub Actions so future collaborators follow the same rules by default.

### Support

If this project is useful to you, you're welcome to buy the developer a coffee here:

[Buy me a coffee via PayPal](https://paypal.me/Yingxuliu)

This is voluntary support for project maintenance. It does not purchase technical support, warranty, priority service, commercial rights, or feature requests.

### License

This project is released under the [MIT License](LICENSE). Code, documentation, or example contributions are expected to be contributed under the same license; no separate CLA is currently required.

### Documentation

- [Changelog](CHANGELOG.md)
- [License](LICENSE)
- [Product Positioning](docs/PRODUCT_POSITIONING.md)
- [Architecture Overview](docs/ARCHITECTURE.md)
- [llms.txt](llms.txt)
- [AI Agent Discovery](docs/AI_AGENT_DISCOVERY.md)
- [Automation And AI Integration Notes](docs/AI_INTEGRATION.md)
- [Roadmap](docs/ROADMAP.md)
- [Repository Boundary](docs/REPOSITORY_BOUNDARY.md)
- [GitHub Repo Setup Suggestions](docs/GITHUB_REPO_SETUP.md)
- [Contributing](CONTRIBUTING.md)

### Contribute And Discuss

This project welcomes more than code. It also welcomes ideas, experiments, product thinking, and research around one central question: how professionals can find better-fit opportunities more effectively.

Useful contribution areas include:

- README, documentation, and GitHub presentation improvements
- New AI capabilities such as role generation, match explanation, ranking rationale, company summaries, or workflow automation
- New search engines or data sources, including more ATS systems, company-career parsing strategies, or domain-specific sources
- Better matching logic, scoring strategies, company discovery methods, and job-search heuristics
- Career discovery methods, cross-industry transition strategies, and ideas for finding better-fit companies earlier
- Better result review flows, status tracking, exports, and workspace UX
- New product ideas, research questions, evaluation methods, datasets, and user insights

If GitHub Discussions is not enabled, opening an Issue is still a valid way to discuss ideas. A proposal can be framed as a feature suggestion, a research hypothesis, a data-source idea, or an analysis of why existing platforms fail to match certain professional profiles well.

Before opening an Issue or PR, please read [CONTRIBUTING.md](CONTRIBUTING.md).

## 中文说明

### 项目定位

Job Hunter 不是又一个职位列表工具。它解决的是一个更长期的问题：当你不是随便跳槽，而是在认真寻找更值得投入的长期方向时，单次搜索很快会失效。你需要记住哪些公司值得盯、哪些岗位已经看过、哪些方向反复出现、哪些线索只是噪音。

所以 Job Hunter 的核心不是“今天帮你抓一批岗位”，而是把每一轮搜索变成积累：先发现和验证具体岗位，再把高质量岗位背后的公司、招聘入口和匹配理由写回本地公司池。下一轮搜索会基于这些历史判断继续推进，而不是重新从零开始。

一句话总结：**短期可以用搜索引擎找岗位；长期需要一个会积累公司判断的求职工作台。**

### 为什么值得长期用

- 不用每次重新解释自己：候选人画像、目标方向、地域偏好和结果状态都保存在本地。
- 不重复看同一批岗位：系统会保留已发现、已分析和已处理的岗位记录。
- 不只追热门职位标题：它会跟踪公司、招聘入口和历史匹配信号，更适合专业型、跨领域或命名不标准的岗位。
- 好岗位会反哺公司池：当某家公司出现高匹配岗位，它会成为后续轮次更值得关注的来源。
- 结果可持续维护：关注、投递、放弃、Offer 等状态都在同一个工作台里跟进。

### 适合谁

- 已经工作一段时间、具备明确专业能力的中高级求职者
- 细分行业或专业岗位人群，如系统工程、验证测试、MBSE、数字孪生、可靠性、能源装备等方向
- 希望跨行业迁移，但不想丢掉核心能力的人
- 不满足于“职位平台推荐流”，更希望主动建立目标公司池的人

### 搜索逻辑

1. 读取候选人画像和目标岗位方向。
2. 先发现新的具体岗位，去重并验证岗位是否仍然有效。
3. 对有效岗位做匹配评分和推荐判断。
4. 将高质量岗位对应的公司写入或激活公司池。
5. 再基于公司池继续发现公司官网、ATS 和公开招聘入口。
6. 把岗位结果、公司信号和用户后续状态写回本地数据库，供下一轮继续使用。

这套流程仍然保持单线、可解释、可调试；区别在于它不是只消费公司池，也会从好岗位反向补强公司池。

### AI 原生搜索逻辑

Job Hunter 不是在传统关键词搜索外面套一层 AI 聊天助手，而是把 AI 用在传统硬编码规则最难覆盖的地方：岗位标题不稳定、职责描述模糊、相邻行业迁移、公司是否可能需要某种能力，以及为什么一个岗位值得继续追踪。

确定性的部分仍然由普通软件负责：本地数据库、运行状态、结果历史、评审状态和桌面工作流。AI 负责的是开放式语言理解和匹配判断层，避免把复杂求职场景硬塞进固定关键词和穷举规则里。

### 你应该从哪里开始

如果你只是想直接使用软件，请先看“普通用户”路径；如果你要改代码、排查问题或参与协作，再看“开发者”路径。

#### 普通用户：直接下载 Windows 版本

如果你不是开发者，只是想直接使用软件，请不要从源码开始，也不需要在本地安装 Python。你应该直接去 GitHub Releases 下载 Windows 发布包。

- 最新发布页：
  [https://github.com/liuyingxuvka/Job-Hunter/releases/latest](https://github.com/liuyingxuvka/Job-Hunter/releases/latest)
- 所有发布版本：
  [https://github.com/liuyingxuvka/Job-Hunter/releases](https://github.com/liuyingxuvka/Job-Hunter/releases)
- 应下载的文件：
  `Job-Hunter-[version]-win64.zip`

推荐步骤：

1. 打开上面的 `latest release` 链接。
2. 下载 `Job-Hunter-[version]-win64.zip`。
3. 解压到一个普通文件夹，不要直接在 zip 压缩包里运行。
4. 双击 `Jobflow Desktop.exe`。
5. 如果 Windows 对直接启动 `.exe` 比较严格，再改用 `START_JOBFLOW_DESKTOP.cmd`。
6. 第一次打开后，在应用里填写 API 设置即可开始使用。

这个发布包已经包含桌面运行时、demo 候选人种子和安全模板。普通用户本地不需要额外安装 Python。

发布包不会包含真实候选人数据库、客户数据、搜索历史、导出结果或运行备份。

打包版会把用户数据库、导出、日志和搜索运行状态保存在本机用户目录下，并在启动后静默检查 GitHub Releases 是否有新版本。有可用更新时，工作台顶部版本胶囊旁边会提示；更新包下载并校验完成后，你可以选择现在重启安装或以后再安装。

#### 开发者：从源码运行

下面这部分只面向开发者和协作者。

开发环境要求：

- Windows 开发环境优先
- Python 3.10+
- OpenAI API Key

从 GitHub Release 下载的 Windows 发布包不要求本地单独安装 Python。

可用环境变量：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `JOBFLOW_OPENAI_MODEL`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`
- `JOBFLOW_PYTHON_PATH`

源码启动方式 A：从仓库根目录快速启动

在仓库根目录双击：

```bat
START_JOBFLOW_DESKTOP.cmd
```

这个入口会调用 `desktop_app/run_release.ps1`，自动寻找本地 Python，并启动桌面应用。

源码启动方式 B：在 `desktop_app/` 下以开发模式运行

```powershell
cd .\desktop_app
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\jobflow-desktop
```

如果你是在做 CLI、自动化或 AI 集成，请直接阅读：

- [AI Agent Discovery (English)](docs/AI_AGENT_DISCOVERY.md)
- [AI Integration Notes](docs/AI_INTEGRATION.md)

这些内容保留在单独文档里，不放在 GitHub 首页 README 展开。

### 当前仓库能做什么

当前仓库主要包含一个正在持续迭代的本地桌面工作台，以及一套 Python 原生的岗位发现、公司池维护和结果管理链路。

已落地能力包括：

- 本地候选人管理：维护姓名、邮箱、当前所在地、目标地区、备注和简历路径
- AI 目标岗位设立：辅助生成更具体的岗位方向，并维护中英文岗位名称和说明
- 本地 AI 设置：支持直接填写 API Key 或绑定环境变量，并验证模型可用性
- 岗位发现与公司池闭环：先发现和验证具体岗位，再将高质量岗位背后的公司写入公司池，后续继续跟踪
- 搜索结果工作台：查看匹配结果，并维护关注、投递、Offer、放弃等状态
- 本地优先数据存储：以 SQLite 作为候选人、搜索配置、结果状态和运行数据的主存储；`desktop_app/runtime/search_runs/` 只保留按候选人划分的临时工作目录

### 核心流程

1. 创建候选人档案，导入简历，填写当前所在地、目标地区和补充说明。
2. 通过 AI 推荐或手动补充方式，建立真正值得追踪的目标岗位方向。
3. 基于这些岗位方向生成搜索上下文，发现并验证具体岗位，同时维护可复用的目标公司池。
4. 对岗位进行匹配分析和结果整理，在工作台里持续维护关注、投递和反馈状态。

### 仓库结构

| Path | 说明 |
| --- | --- |
| `desktop_app/` | 新版桌面应用，负责候选人工作台、AI 设置、搜索结果查看与后续维护 |
| `docs/` | GitHub 说明文档，包括产品定位、架构和路线图 |
| `README_RELEASE.txt` | 面向打包发布目录的简要启动说明 |
| `START_JOBFLOW_DESKTOP.cmd` | Windows 下的快速启动入口 |

### 当前边界

这个项目目前更接近一个“发现 + 筛选 + 管理”的本地工作台，而不是一个“全自动找工作/自动投递平台”。

当前明确不应过度承诺的内容：

- 不是面向所有求职者的大众化职位推荐产品
- 不是自动投递系统
- 不是已经完成商业化打磨的成品桌面软件
- 不是已经为所有平台和长期维护场景都做完最终工程化定型的产品

### 公开仓库边界

这个仓库公开部分只保留源码、文档、演示种子和安全示例模板；个人简历、公司池、搜索结果、SQLite 数据和运行备份必须留在本地。

当前已经把这套边界写进 `.gitignore`、`scripts/privacy_audit.ps1` 和 GitHub Actions，所以未来同事协作时也会按同一规则执行。

### 支持

如果这个项目对你有帮助，欢迎通过下面的链接请开发者喝杯咖啡：

[通过 PayPal 请开发者喝杯咖啡](https://paypal.me/Yingxuliu)

这只是自愿支持项目维护，不代表购买技术支持、质保、优先服务、商业授权或功能定制。

### 许可

本项目以 [MIT License](LICENSE) 发布。贡献代码、文档或示例时，默认同意将贡献内容按同一许可发布；当前不要求单独签署 CLA。

### 文档导航

- [更新记录](CHANGELOG.md)
- [许可](LICENSE)
- [产品定位](docs/PRODUCT_POSITIONING.md)
- [架构概览](docs/ARCHITECTURE.md)
- [llms.txt](llms.txt)
- [AI agent 检索说明（English）](docs/AI_AGENT_DISCOVERY.md)
- [自动化与 AI 集成说明](docs/AI_INTEGRATION.md)
- [路线图](docs/ROADMAP.md)
- [仓库边界](docs/REPOSITORY_BOUNDARY.md)
- [GitHub 仓库设置建议](docs/GITHUB_REPO_SETUP.md)
- [贡献说明](CONTRIBUTING.md)

### 贡献与讨论

这个项目欢迎的不只是代码提交，也欢迎思路、实验方向和产品判断。我们尤其欢迎围绕“专业型人才如何更高效找到更匹配职位”这个问题展开合作。

欢迎贡献的方向包括：

- README、文档和 GitHub 展示信息的改进
- 新的 AI 功能，例如岗位方向生成、匹配解释、排序理由、候选公司摘要、工作流自动化
- 新的搜索引擎或数据源接入，例如更多 ATS、官网抓取方式、行业特定来源或地区性来源
- 更好的匹配逻辑、评分策略、候选公司发现方法和搜索启发式
- 职业发现方法、跨行业迁移策略，以及如何先找到更匹配公司的思路
- 更好的结果管理、审核状态、导出和工作流体验
- 新的产品想法、研究问题、评估方法、数据集和用户访谈结论

如果 GitHub Discussions 还没有启用，也欢迎直接开 Issue 来讨论想法。你可以把问题写成功能提案、研究假设、数据源建议，或者“为什么某类专业人才很难被现有平台正确匹配”的分析。

提 Issue 或 PR 前，建议先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
