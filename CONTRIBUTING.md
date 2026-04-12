# Contributing / 贡献指南

感谢你愿意改进这个项目。

Thank you for considering a contribution to this project.

## 开始前先看什么 / Read This First

开始之前，建议先读：

Before contributing, please read:

- [README.md](README.md)
- [CHANGELOG.md](CHANGELOG.md)
- [docs/PRODUCT_POSITIONING.md](docs/PRODUCT_POSITIONING.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/ROADMAP.md](docs/ROADMAP.md)

这个项目目前处于“产品定位和桌面工作台一起演进”的阶段，贡献时请优先保证叙事、界面和代码行为是一致的。

This project is still evolving at both the product and implementation level. Contributions should keep the product story, user workflow, and actual code behavior aligned.

## 你可以如何参与 / How You Can Contribute

你不需要从代码开始参与。问题定义、场景分析、研究假设、失败案例、产品判断和方法讨论，同样属于高价值贡献。

You do not need to start with code. Problem framing, scenario analysis, research hypotheses, failure cases, product thinking, and method discussions are also high-value contributions.

我们希望这个仓库不仅接收代码，也接收判断、经验和方法。只要它能帮助有专业经验的人更高效地发现更匹配的公司和岗位，就值得被讨论。

We want this repository to accept not only code, but also judgment, experience, and methods. If an idea helps experienced professionals discover better-fit companies and roles more effectively, it is worth discussing.

## 当前最欢迎的贡献方向 / High-Value Contribution Areas

我们尤其欢迎以下方向：

- 新 AI 功能，例如候选人画像理解、岗位归一化、匹配解释、公司优先级排序、工作流自动化
- 新搜索引擎、新 ATS 适配器、公司官网来源和其他职位发现渠道
- 匹配逻辑、排序规则、启发式方法、失败案例分析和评估指标
- 职业发现方法、跨行业迁移策略，以及如何帮助用户先找到更匹配的公司
- 产品讨论、用户流程优化、交互设计和信息架构
- 文档、双语表达和 GitHub 展示优化
- 研究型贡献，例如数据集、实验设计、用户访谈总结和误判分析

We especially welcome:

- new AI capabilities such as profile understanding, role normalization, match explanation, company prioritization, and workflow automation
- new search engines, ATS adapters, company career-site sources, and other discovery channels
- matching logic, ranking rules, heuristics, failure-case analysis, and evaluation criteria
- career discovery methods, cross-industry transition strategies, and ideas that help users find better-fit companies earlier
- product discussion, workflow design, UX refinement, and information architecture
- documentation, bilingual writing, and GitHub presentation improvements
- research-oriented contributions such as datasets, experiment design, interview findings, and false-positive analysis

## 建议先讨论的议题 / Discussion-First Topics

如果你准备改动以下内容，建议先开 Issue 说明想法：

- 产品定位
- 搜索主流程
- 数据模型
- 搜索引擎或数据源接入方式
- 大范围目录重构
- 会改变用户理解方式的 UI 或工作流调整

For the following changes, opening an Issue first is strongly preferred:

- product positioning
- core search workflow
- data model changes
- search-engine or data-source integration strategy
- broad directory or architecture refactors
- UI or workflow changes that affect how users understand the product

如果你的贡献更像一个研究方向或产品假设，请先说明：你想解决什么问题、适用于什么人群、核心思路是什么、为什么可能有效、准备如何验证。

If your contribution is more of a research direction or product hypothesis, please explain the problem, target users, core idea, why it may work, and how it could be evaluated.

如果 GitHub Discussions 没有启用，也可以直接使用 Issue，把标题写成 `[idea]`、`[proposal]`、`[research]` 或 `[search]` 都可以。

If GitHub Discussions is not enabled, Issues are still a good discussion channel. Titles such as `[idea]`, `[proposal]`, `[research]`, or `[search]` are perfectly acceptable.

## 一个好提案应包含什么 / What Makes A Good Proposal

一个高质量的提案通常会说明：

- 你试图解决的用户问题是什么
- 适用于什么人群、行业或职业背景
- 你的核心思路、方法或数据源是什么
- 它为什么可能有效，也可能带来什么风险
- 你准备如何验证它是否真的更好

A strong proposal usually explains:

- what user problem it is trying to solve
- who it helps and under what professional context
- what method, workflow, or data source it suggests
- why it may work and what risk it may introduce
- how the idea could be validated in practice

对于新搜索引擎或新数据源，我们更关注它是否能帮助用户更早发现真正相关的公司和职位，而不只是增加结果数量。

For new search engines or data sources, we care more about whether they help users discover genuinely relevant companies and roles earlier, not just whether they increase result volume.

对于新的匹配逻辑，我们欢迎规则、特征、提示词、评估集、误判分析和人工 review 流程方面的改进。

For new matching logic, we welcome improvements in rules, features, prompts, evaluation sets, false-positive analysis, and review workflows.

如果你想讨论“如何定义更匹配的职位”，这本身就是值得贡献的议题。

If you want to discuss how to define a better match, that is itself a valuable contribution topic.

## 什么时候可以直接提 PR / When A Direct PR Is Fine

以下类型通常可以直接提交 PR：

- 文档修正和双语补充
- 文案优化
- 小范围 bug 修复
- 集中、低风险的界面细节改进
- 明确边界内的小型工程整理

These changes are usually safe to submit directly as a PR:

- documentation fixes and bilingual improvements
- copywriting improvements
- small bug fixes
- focused, low-risk UI refinements
- small engineering cleanups within a clear scope

## 本地开发 / Local Development

当前以 Windows 本地开发为主。

The current project setup is primarily Windows-first.

```powershell
cd .\desktop_app
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\jobflow-desktop
```

如果需要调用旧版岗位发现引擎，还需要可用的 Node.js，或者使用 `desktop_app/runtime/tools/` 下的便携 Node。

If you need to run the legacy discovery engine, you will also need a usable Node.js runtime, or the portable Node binaries under `desktop_app/runtime/tools/`.

## 数据边界 / Repository Data Boundary

请把公开仓库理解为“产品代码与公共文档”，而不是任何人的真实求职工作区。

Treat the public repository as product code plus public documentation, not as anyone's live job-search workspace.

以下内容必须留在本地：

The following must remain local:

- `legacy_jobflow_reference/config.json`、`config.adjacent.json`
- `legacy_jobflow_reference/companies.json`、`companies_adjacent.json`
- `legacy_jobflow_reference/resume.md`
- `legacy_jobflow_reference/jobs*.json`、`jobs*.xlsx`
- `desktop_app/runtime/data/jobflow_desktop.db*`
- `desktop_app/runtime/backups/**`

- `legacy_jobflow_reference/config.json` and `config.adjacent.json`
- `legacy_jobflow_reference/companies.json` and `companies_adjacent.json`
- `legacy_jobflow_reference/resume.md`
- `legacy_jobflow_reference/jobs*.json` and `jobs*.xlsx`
- `desktop_app/runtime/data/jobflow_desktop.db*`
- `desktop_app/runtime/backups/**`

如果你需要从模板开始，请复制 `*.example.*` 文件到本地工作副本，不要把个人资料写回模板。

If you need a starting point, copy the `*.example.*` files into local working copies and keep personal data out of the templates.

提交前请运行：

Run this before you push:

```powershell
.\scripts\privacy_audit.ps1
```

更完整的边界说明见 [docs/REPOSITORY_BOUNDARY.md](docs/REPOSITORY_BOUNDARY.md)。

See [docs/REPOSITORY_BOUNDARY.md](docs/REPOSITORY_BOUNDARY.md) for the full boundary policy.

## PR 期望 / PR Expectations

提交 PR 时，请尽量说明：

- 为什么要改
- 改了什么
- 如何验证
- 是否影响产品定位、搜索逻辑或数据结构

When opening a PR, please explain:

- why the change is needed
- what changed
- how you validated it
- whether it affects product positioning, search logic, or data structures

如果改动涉及 UI，请附截图；如果涉及搜索结果或数据流程，请说明输入和输出变化。

If the change affects UI, include screenshots. If it affects search results or data flow, explain the expected input and output changes.

## 双语协作 / Bilingual Collaboration

核心入口文档默认建议中英双语，至少包括 README、CONTRIBUTING、Issue 模板和面向仓库访客的说明页面。

Core entry-point documents should be bilingual by default, at minimum README, CONTRIBUTING, issue templates, and repository-facing guidance.

建议遵守：

- 文件名尽量用英文
- 关键对外文档尽量中英对照
- 不要让 README、子目录 README 和实际代码状态互相矛盾

Recommended conventions:

- prefer English file names
- keep important public-facing docs bilingual when practical
- do not let the README, subdirectory READMEs, and actual code state contradict each other

## 当前现实约束 / Current Practical Constraints

请在贡献时注意：

- 项目还在演进中，不要把尚未稳定的规划写成已完成能力
- 旧版引擎仍在承担部分关键能力，不要忽略这层依赖
- 自动化测试目前并不完整，必要时请补充手工验证说明

Please keep these constraints in mind:

- the project is still evolving, so avoid presenting planned work as completed capability
- the legacy engine still carries part of the functional load
- automated test coverage is still incomplete, so manual verification notes are often necessary

## 版本与更新记录 / Versioning And Changelog

当前采用轻量级版本管理：

- 小修复、说明文档更新、低风险维护，通常走 `patch`
- 新的用户可见能力或明显的流程扩展，通常走 `minor`
- 破坏兼容性或大的架构调整，再考虑 `major`

The project currently follows lightweight semantic versioning:

- use `patch` for small fixes, documentation updates, and low-risk maintenance
- use `minor` for meaningful new user-facing capabilities or workflow expansion
- use `major` for breaking changes or large architectural shifts

版本记录集中维护在 [CHANGELOG.md](CHANGELOG.md)。

Version history is maintained in [CHANGELOG.md](CHANGELOG.md).
