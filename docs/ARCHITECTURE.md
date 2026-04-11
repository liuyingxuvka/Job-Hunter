# Architecture Overview / 架构概览

## 目标 / Goal

当前仓库的架构目标，是把一套偏脚本化的岗位发现流程，逐步沉淀成一个可长期维护的本地桌面工作台。

The architectural goal of this repository is to evolve a script-heavy job discovery flow into a maintainable local desktop workspace.

这个架构不是一次性推倒重来，而是分层演进：用新的桌面端承载候选人管理、AI 设置和结果维护，保留旧版搜索引擎作为当前执行层，并通过本地数据库沉淀用户状态、搜索方向和运行记录。

This architecture is not a one-shot rewrite. It is an incremental transition: use the new desktop app for candidate management, AI settings, and result handling; keep the legacy engine as the current execution layer; and persist user state, search direction, and runtime records through a local database.

## 当前系统组成 / Current System Layers

### 1. 桌面应用层 / Desktop Application Layer

路径 / Path: `desktop_app/src/jobflow_desktop_app/`

当前桌面端基于 PySide6，负责候选人目录、候选人工作台、基础信息管理、目标岗位方向设立、AI 设置与模型验证，以及搜索结果查看和状态维护。

The current desktop app is based on PySide6 and handles the candidate directory, candidate workspace, basics management, target-role setup, AI settings and model validation, plus result review and status maintenance.

它是项目未来的主入口。

It is the intended primary entry point for the product.

### 2. 本地存储层 / Local Persistence Layer

主要文件 / Main files:

- `desktop_app/src/jobflow_desktop_app/db/schema.sql`
- `desktop_app/src/jobflow_desktop_app/db/repositories/`

当前使用 SQLite 保存本地数据，核心表包括 `candidates`、`resumes`、`search_profiles`、`search_profile_queries`、`companies`、`jobs`、`search_runs`、`job_analyses`、`job_review_states` 和 `app_settings`。

SQLite is used for local persistence. Core tables include `candidates`, `resumes`, `search_profiles`, `search_profile_queries`, `companies`, `jobs`, `search_runs`, `job_analyses`, `job_review_states`, and `app_settings`.

这意味着项目已经从“一次性脚本输出文件”转向“本地持续维护的数据工作台”。

That shift matters because the project is no longer just script output. It is becoming a persistent workspace with state over time.

### 3. AI 辅助层 / AI Assistance Layer

主要文件 / Main files:

- `desktop_app/src/jobflow_desktop_app/services/model_catalog.py`
- `desktop_app/src/jobflow_desktop_app/services/role_recommendations.py`

AI 在当前架构里主要负责两类工作：一是校验 API Key 与模型可用性，二是帮助用户设立更具体、更可执行的目标岗位方向，并补全中英文岗位名称与描述。

Within the current architecture, AI mainly handles two tasks: validating API keys and model availability, and helping users define more specific, actionable target-role directions with bilingual role names and descriptions.

当前 AI 不是整个系统的唯一核心，而是增强候选人定位和搜索上下文质量的辅助层。

AI is not the whole product. It is an assistive layer that improves candidate positioning and the quality of search context.

### 4. 搜索执行层 / Search Execution Layer

主要文件 / Main files:

- `desktop_app/src/jobflow_desktop_app/services/legacy_runner.py`
- `legacy_jobflow_reference/jobflow.mjs`

桌面应用当前并不直接实现完整的岗位抓取与分析引擎，而是根据候选人信息和搜索配置生成运行时配置，调用 `legacy_jobflow_reference/` 中的 Node.js 搜索引擎，读取其 JSON 输出，再把结果带回桌面工作台进行展示和维护。

The desktop app does not yet implement the full search-and-analysis engine directly. Instead, it generates runtime config from candidate data and search settings, calls the Node.js engine in `legacy_jobflow_reference/`, reads its JSON output, and brings the results back into the desktop workspace for review and maintenance.

这是一种现实可用的过渡架构：先把用户工作台做好，再逐步替换旧版执行层。

This is a pragmatic transitional architecture: build the user workspace first, then gradually replace the legacy execution layer.

### 5. 运行时目录 / Runtime Directories

路径 / Path: `desktop_app/runtime/`

当前用于存放：

- `data/`：本地数据库等运行数据
- `exports/`：导出文件
- `legacy_runs/`：旧版搜索引擎的按候选人运行结果
- `logs/`：运行日志
- `tools/`：可选的本地工具依赖，例如便携 Node

Current runtime usage:

- `data/`: local database and runtime data
- `exports/`: generated export artifacts
- `legacy_runs/`: per-candidate run outputs from the legacy engine
- `logs/`: runtime logs
- `tools/`: optional local runtime dependencies such as portable Node binaries

## 当前数据流 / Current Data Flow

一个典型运行链路如下：

1. 用户在桌面端创建候选人档案。  
   A user creates a candidate profile in the desktop app.
2. 用户补充简历、目标地区、岗位方向和搜索配置。  
   The user adds resume data, location preferences, role directions, and search settings.
3. 桌面端把这些信息写入 SQLite。  
   The desktop app writes that state into SQLite.
4. 当触发搜索时，桌面端生成面向旧版引擎的运行时配置。  
   When a search is triggered, the desktop app creates runtime config for the legacy engine.
5. `legacy_jobflow_reference/` 执行公司发现、岗位抓取和匹配分析。  
   `legacy_jobflow_reference/` performs company discovery, job retrieval, and matching analysis.
6. 桌面端读取 JSON 结果并展示给用户。  
   The desktop app reads the JSON results and shows them to the user.
7. 用户在结果页继续维护关注、投递和去留状态。  
   The user continues to manage focus, applied, and keep-or-drop states in the results view.

## 目录角色划分 / Directory Roles

| Path | 中文角色 | English Role |
| --- | --- | --- |
| `desktop_app/` | 新版产品主线，承载桌面工作台和本地数据层 | Main product line for the desktop workspace and local data layer |
| `legacy_jobflow_reference/` | 旧版搜索与分析引擎参考实现 | Legacy search and analysis engine reference implementation |
| `docs/` | 项目对外说明文档 | Public-facing project documentation |

## 当前架构边界 / Current Architectural Boundaries

当前架构仍处于演进阶段，主要边界包括：

- 搜索执行仍依赖旧版 Node.js 引擎
- 运行方式以 Windows 本地使用为主
- 自动化测试和工程化保护还不完整
- GitHub 层面的产品说明刚开始补齐，代码能力与外部叙事仍在对齐中

The current architecture is still in transition. Key limitations include:

- search execution still depends on the legacy Node.js engine
- the default operating mode is still local Windows usage
- automated testing and engineering guardrails are not yet complete
- the repository story on GitHub is still being aligned with the real code state

## 后续演进方向 / Next Architectural Direction

更合理的下一步不是继续堆脚本，而是：

- 补强桌面端对运行状态、结果解释和导出的控制力
- 逐步把旧版引擎里的关键能力抽离并迁移到新架构
- 让“按候选人维护求职 pipeline”成为系统的中心，而不是附属功能

The better next step is not to add more loose scripts. It is to:

- improve desktop control over run state, explanation, and export behavior
- gradually migrate key capabilities out of the legacy engine
- make candidate-centered job-search pipeline maintenance the real center of the system
