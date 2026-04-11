# Job Hunter

> 面向有专业经验者的岗位发现工作台  
> A local-first job discovery workspace for experienced professionals.

## 项目定位

Job Hunter 不是一个面向大众求职场景的职位推荐器。它更适合已经工作一段时间、拥有明确专业技能和职业方向的求职者：先从你的经验、目标方向和地域偏好出发，识别更值得跟踪的公司，再持续发现这些公司公开放出的岗位。

很多通用求职平台更擅长把热门职位推给大量用户，但对系统工程、验证测试、MBSE、可靠性、数字孪生、能源装备、氢能等细分方向来说，岗位名称、组织结构和招聘表达往往并不标准化。真正匹配的机会，经常藏在“公司需要这类能力，但岗位标题不完全一样”的场景里。

这个项目的核心思路是：

1. 先理解候选人已经具备的经验和可迁移能力。
2. 再推导哪些公司更可能需要这些能力。
3. 然后优先抓取公司官网、ATS 和有限的 Web 信号。
4. 最后把岗位匹配、状态维护和后续行动集中到一个本地工作台里。

一句话总结：**先找对公司，再找对岗位。**

## 适合谁

- 已经工作一段时间、具备明确专业能力的中高级求职者
- 细分行业或专业岗位人群，如系统工程、验证测试、MBSE、数字孪生、可靠性、能源装备、氢能等方向
- 希望跨行业迁移，但不想丢掉核心能力的人
- 不满足于“职位平台推荐流”，更希望主动建立目标公司池的人

## 当前仓库能做什么

当前仓库主要包含一个正在持续迭代的本地桌面工作台，以及一套旧版岗位发现引擎参考实现。

已落地能力包括：

- 本地候选人管理：维护姓名、邮箱、当前所在地、目标地区、备注和简历路径
- AI 目标岗位设立：辅助生成更具体的岗位方向，并维护中英文岗位名称和说明
- 本地 AI 设置：支持直接填写 API Key 或绑定环境变量，并验证模型可用性
- 公司优先的岗位发现流程：根据候选人的岗位方向和偏好，调用旧版搜索引擎做公司与岗位发现
- 搜索结果工作台：查看匹配结果，并维护关注、投递、Offer、放弃等状态
- 本地优先数据存储：通过 SQLite 保存候选人、搜索配置、结果状态和运行数据

## 仓库结构

| 路径 | 说明 |
| --- | --- |
| `desktop_app/` | 新版桌面应用，负责候选人工作台、AI 设置、搜索结果查看与后续维护 |
| `legacy_jobflow_reference/` | 旧版岗位发现参考引擎，当前仍作为搜索执行层被桌面应用调用 |
| `docs/` | GitHub 说明文档，包括产品定位、架构和路线图 |
| `README_RELEASE.txt` | 面向打包发布目录的简要启动说明 |
| `START_JOBFLOW_DESKTOP.cmd` | Windows 下的快速启动入口 |

## 核心流程

1. 创建候选人档案，导入简历，填写当前所在地、目标地区和补充说明。
2. 通过 AI 推荐或手动补充方式，建立真正值得追踪的目标岗位方向。
3. 基于这些岗位方向生成搜索上下文，优先发现相关公司，再抓取公司官网或 ATS 的公开职位。
4. 对岗位进行匹配分析和结果整理，在工作台里持续维护关注、投递和反馈状态。

## 当前边界

这个项目目前更接近一个“发现 + 筛选 + 管理”的本地工作台，而不是一个“全自动找工作/自动投递平台”。

当前明确不应过度承诺的内容：

- 不是面向所有求职者的大众化职位推荐产品
- 不是自动投递系统
- 不是已经完成商业化打磨的成品桌面软件
- 不是完全脱离旧版引擎的独立新架构，当前搜索执行仍依赖 `legacy_jobflow_reference/`

## 快速开始

### 方式一：直接启动桌面应用

在仓库根目录双击：

```bat
START_JOBFLOW_DESKTOP.cmd
```

这个入口会调用 `desktop_app/run_release.ps1`，自动寻找本地 Python，并启动桌面应用。

### 方式二：开发模式运行

```powershell
cd .\desktop_app
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\jobflow-desktop
```

## 环境要求

- Windows 开发环境优先
- Python 3.10+
- OpenAI API Key
- 如果要运行旧版搜索引擎，需要可用的 Node.js；也可以使用 `desktop_app/runtime/tools/` 下的便携 Node

可用环境变量包括：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `JOBFLOW_OPENAI_MODEL`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`
- `JOBFLOW_NODE_PATH`
- `JOBFLOW_PYTHON_PATH`

## 文档导航

- [更新记录](CHANGELOG.md)
- [产品定位](docs/PRODUCT_POSITIONING.md)
- [架构概览](docs/ARCHITECTURE.md)
- [路线图](docs/ROADMAP.md)
- [GitHub 仓库设置建议](docs/GITHUB_REPO_SETUP.md)
- [贡献说明](CONTRIBUTING.md)

## 贡献建议

如果你想一起完善这个项目，建议优先从以下方向参与：

- 改进 README 和产品叙事，让 GitHub 首页更容易看懂
- 补强桌面工作台的稳定性和可维护性
- 继续把旧版搜索引擎能力迁移到新的桌面架构中
- 优化结果筛选、状态管理和导出链路

提 Issue 或 PR 前，建议先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
