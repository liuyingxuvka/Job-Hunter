# Architecture Overview / 架构概览

## 目标 / Goal

当前仓库的架构目标，是把一套偏脚本化的岗位发现流程，逐步沉淀成一个可长期维护的本地桌面工作台。

The architectural goal of this repository is to evolve a script-heavy job discovery flow into a maintainable local desktop workspace.

这个架构不是一次性推倒重来，而是分层演进：用桌面端承载候选人管理、AI 设置、搜索执行和结果维护，通过本地数据库沉淀用户状态、搜索方向和运行记录，并持续把搜索主线收敛在 Python 原生模块内。

This architecture is not a one-shot rewrite. It is an incremental transition: use the desktop app for candidate management, AI settings, search execution, and result handling; persist user state, search direction, and runtime records through a local database; and keep consolidating the search path inside Python-native modules.

## 当前系统组成 / Current System Layers

### 1. 桌面应用层 / Desktop Application Layer

路径 / Path: `desktop_app/src/jobflow_desktop_app/`

当前桌面端基于 PySide6，负责候选人目录、候选人工作台、基础信息管理、目标岗位方向设立、AI 设置与模型验证，以及搜索结果查看和状态维护。

The current desktop app is based on PySide6 and handles the candidate directory, candidate workspace, basics management, target-role setup, AI settings and model validation, plus result review and status maintenance.

在页面内部，像 `search_results.py` 这样曾经偏重的页面，已经继续把纯状态文案、搜索时长/倒计时状态推导、搜索生命周期、实时结果刷新、倒计时/进度阶段格式化、临时结果排序签名、单行结果表格装配、纯渲染 helper 和轻量状态持久化逻辑下沉到同目录的小模块，例如 `search_results_controls_state.py`、`search_results_status.py`、`search_results_live_state.py`、`search_results_live_runtime.py`、`search_results_search_flow.py`、`search_results_row_rendering.py`、`search_results_prerequisites.py`、`search_results_links.py`、`search_results_rendering.py`、`search_results_candidate_state.py`、`search_results_review_status.py`、`search_results_review_store.py`、`search_results_review_state.py` 与 `search_results_runtime_state.py`；`ai_settings.py` 也开始把 API key source / env-var 切换行为下沉到 `ai_settings_api_key_source.py`；同时像 `target_direction.py` 这种同时承载多个页面角色的文件，也已经把双语岗位补全逻辑下沉到 `target_direction_bilingual.py`，把岗位名/岗位说明的双语补全管线下沉到 `target_direction_profile_completion.py`，把只读预览补全下沉到 `target_direction_profile_preview.py`，把岗位内容标准化与记录组装下沉到 `target_direction_profile_records.py`，把 AI 推荐结果处理下沉到 `target_direction_recommendations.py`，把 AI 推荐岗位异步流程下沉到 `target_direction_role_suggestion_flow.py`，把手动新增岗位与 AI 补全过程下沉到 `target_direction_manual_add_flow.py`，并把候选人切换、列表重载和表单同步收敛到 `target_direction_workspace_state.py`。工作台内部当前直接围绕 `TargetDirectionStep` 和 `CandidateRecord` 驱动，而不是保留旧的共享策略页模块。对外页面入口现在直接指向真实页面模块，而不是通过额外转发层。

Inside the page layer, formerly heavier files such as `search_results.py` are being trimmed further by moving status text, search lifecycle, live-result refresh, prerequisite gating, pure render helpers, and light state-persistence helpers into same-directory helper modules such as `search_results_status.py`, `search_results_live_runtime.py`, `search_results_search_flow.py`, `search_results_prerequisites.py`, `search_results_links.py`, `search_results_rendering.py`, `search_results_candidate_state.py`, `search_results_review_status.py`, `search_results_review_store.py`, `search_results_review_state.py`, and `search_results_runtime_state.py`; `ai_settings.py` has also started shedding credential-source handling into `ai_settings_api_key_source.py`; and multi-role files such as `target_direction.py` have already shed bilingual role-completion logic into `target_direction_bilingual.py`, bilingual role/description completion flow into `target_direction_profile_completion.py`, read-only preview completion into `target_direction_profile_preview.py`, profile-content normalization and record-building into `target_direction_profile_records.py`, AI recommendation result handling into `target_direction_recommendations.py`, asynchronous role-suggestion flow into `target_direction_role_suggestion_flow.py`, manual role-add / enrichment flow into `target_direction_manual_add_flow.py`, and candidate-switch / list-sync workspace state into `target_direction_workspace_state.py`. The workspace now drives that page directly through `TargetDirectionStep` and `CandidateRecord`, instead of keeping an old shared strategy-page module around. External page entry points now point directly at the real page modules instead of extra forwarding layers.

它是项目未来的主入口。

It is the intended primary entry point for the product.

### 2. 本地存储层 / Local Persistence Layer

主要文件 / Main files:

- `desktop_app/src/jobflow_desktop_app/db/schema.sql`
- `desktop_app/src/jobflow_desktop_app/db/repositories/`

当前使用 SQLite 保存本地数据，核心表包括 `candidates`、`resumes`、`search_profiles`、`search_profile_queries`、`companies`、`jobs`、`search_runs`、`job_analyses`、`job_review_states`、`candidate_companies`、`search_run_jobs`、`candidate_semantic_profiles` 和 `app_settings`。

SQLite is used for local persistence. Core tables include `candidates`, `resumes`, `search_profiles`, `search_profile_queries`, `companies`, `jobs`, `search_runs`, `job_analyses`, `job_review_states`, `candidate_companies`, `search_run_jobs`, `candidate_semantic_profiles`, and `app_settings`.

这意味着项目已经从“一次性脚本输出文件”转向“本地持续维护的数据工作台”。

That shift matters because the project is no longer just script output. It is becoming a persistent workspace with state over time.

### 3. AI 辅助层 / AI Assistance Layer

主要文件 / Main files:

- `desktop_app/src/jobflow_desktop_app/ai/client.py`
- `desktop_app/src/jobflow_desktop_app/ai/model_catalog.py`
- `desktop_app/src/jobflow_desktop_app/ai/role_recommendations.py`
- `desktop_app/src/jobflow_desktop_app/ai/role_recommendations_*.py`

AI 在当前架构里主要负责两类工作：一是校验 API Key 与模型可用性，二是帮助用户设立更具体、更可执行的目标岗位方向，并补全中英文岗位名称与描述。`role_recommendations.py` 现在保留为稳定导出门面，内部已经继续按职责拆成 models、text、resume、profile、prompts、parse、service 等小模块，避免把文本规范化、简历读取、AI 响应解析和网络调用继续堆在一个文件里；其中稳定的大段 prompt prose 也已开始迁到包内 `resources/prompts/` 资源文件，由 `prompt_assets.py` 统一读取，而不是继续内嵌在主模块里。

Within the current architecture, AI mainly handles two tasks: validating API keys and model availability, and helping users define more specific, actionable target-role directions with bilingual role names and descriptions.

当前 AI 不是整个系统的唯一核心，而是增强候选人定位和搜索上下文质量的辅助层。

AI is not the whole product. It is an assistive layer that improves candidate positioning and the quality of search context.

### 4. 搜索执行层 / Search Execution Layer

主要文件 / Main files:

- `desktop_app/src/jobflow_desktop_app/search/orchestration/job_search_runner.py`
- `desktop_app/src/jobflow_desktop_app/search/orchestration/job_search_runner_runtime_io.py`
- `desktop_app/src/jobflow_desktop_app/search/orchestration/job_search_runner_session.py`
- `desktop_app/src/jobflow_desktop_app/search/orchestration/search_session_orchestrator.py`
- `desktop_app/src/jobflow_desktop_app/search/orchestration/search_session_runtime.py`
- `desktop_app/src/jobflow_desktop_app/search/orchestration/search_session_resume_gate.py`
- `desktop_app/src/jobflow_desktop_app/search/orchestration/runtime_config_builder.py`
- `desktop_app/src/jobflow_desktop_app/search/orchestration/job_search_runner_records.py`
- `desktop_app/src/jobflow_desktop_app/search/orchestration/company_discovery_queries.py`
- `desktop_app/src/jobflow_desktop_app/search/orchestration/candidate_search_signals.py`
- `desktop_app/src/jobflow_desktop_app/search/companies/selection.py`
- `desktop_app/src/jobflow_desktop_app/search/stages/executor.py`
- `desktop_app/src/jobflow_desktop_app/search/stages/executor_company_stages.py`
- `desktop_app/src/jobflow_desktop_app/search/stages/executor_common.py`
- `desktop_app/src/jobflow_desktop_app/search/stages/resume_pending_support.py`
- `desktop_app/src/jobflow_desktop_app/search/companies/company_sources_ats.py`
- `desktop_app/src/jobflow_desktop_app/search/companies/company_sources_careers.py`
- `desktop_app/src/jobflow_desktop_app/search/companies/company_sources_enrichment.py`
- `desktop_app/src/jobflow_desktop_app/search/companies/sources_fetchers.py`
- `desktop_app/src/jobflow_desktop_app/search/companies/sources_helpers.py`
- `desktop_app/src/jobflow_desktop_app/search/state/search_progress_state.py`
- `desktop_app/src/jobflow_desktop_app/search/state/runtime_candidate_state.py`
- `desktop_app/src/jobflow_desktop_app/search/state/runtime_run_state.py`
- `desktop_app/src/jobflow_desktop_app/search/state/runtime_run_artifacts.py`
- `desktop_app/src/jobflow_desktop_app/search/state/runtime_run_locator.py`
- `desktop_app/src/jobflow_desktop_app/search/state/runtime_job_sync.py`
- `desktop_app/src/jobflow_desktop_app/search/`

桌面应用当前已经把搜索主线迁移到 Python：根据候选人信息和搜索配置生成运行时配置，在 `search/` 下完成公司发现、公司筛选、岗位抓取、待补分析续跑、结果整理与推荐输出，再把结果带回桌面工作台进行展示和维护。现在 `search/orchestration/` 是搜索编排的规范入口，其中 `job_search_runner.py` 保留桌面入口与更薄的 runner 门面，`job_search_runner_runtime_io.py` 承接读侧结果加载、SQLite 运行态写侧同步和推荐输出刷新，`job_search_runner_session.py` 承接单次搜索会话的生命周期与进度写入，并且已经开始直接对接 `runtime_config_builder.py` / `candidate_search_signals.py` 这些真实 owner，而不是继续通过 runner 转发壳，`job_search_runner_records.py` 承接岗位过滤、链接选择和结果记录构建，`search_session_orchestrator.py` 现在进一步收敛为 timed search session 的主循环门面，并通过 round-level outcome/helper 组织 company round 与 finalize/output 刷新；其中 `RoundProgress` 也已经把推荐岗位增长计入真实进展，并在 discovery round 尚未开始前就超时时显式记为失败。`search_session_runtime.py` 承接 session runtime dataclass、阶段执行与终态 helper，`search_session_resume_gate.py` 承接 resume/finalize gate 状态机，`runtime_config_builder.py` 负责运行时配置组装，并通过 `RuntimeCandidateInputPrep` / `RuntimeCandidateConfigContext` / `RuntimeConfigSections` 这些 seam 组织 runtime config；其中候选人稳定输入现在以 `scopeProfiles` 与 `targetRoles` 为主语义，不再把单一 `scopeProfile` / `targetRole` 当作主配置字段，轮次相关 company-discovery queries 按 rotation seed 显式生成，而不是继续混在 context 内部，同时 `maxCompaniesPerRun` 的有效值也已经收成单一解析点，避免 session/runtime/stage 再各自重复 clamp。`runtime_defaults.py` 现在保留的是较稳定的 canonical 用户配置面，而像 `maxJobsPerQuery`、`maxCompaniesPerRun`、`maxJobsPerCompany`、`companyRotationIntervalDays`、`maxNewCompaniesPerRun` 以及分析阶段 job caps 这类运行时派生字段，则由 builder/stategy 在 main 或 resume-pending 阶段显式注入，不再伪装成长期默认参数；`runtime_strategy.py` 现在把 adaptive search 高层参数收成四个规范 knob：`passWorkBudgetSeconds`、`companyBatchSize`、`discoveryBreadth`、`cooldownBaseDays`，并用共享的 `analysis_work_cap` 派生三个分析阶段预算，不再长期保留两套公司批次概念和三份相同分析上限；`company_discovery_queries.py` 现在承担的是更薄的 anchor 前置规划和纯 query planner：先根据已解析的 semantic / candidate / profile search signals 直接形成 core / adjacent / explore 三桶 anchor，再按共享模板转成 query list，不再回到原文本上做第二轮 regex/anchor-library 解释；其中 discovery bucket 里剩下的 `phrase_limit`、`query_limit`、`minimum_anchor_count` 目前仍保持显式，因为它们分别控制 anchor 截断上限、query 预算/轮转分布，以及稀疏输入下默认 anchor 的多样性回填，并不是同一件事的重复表达；`candidate_search_signals.py` 负责候选人搜索信号，并已经按 semantic / candidate / profile 三组语义分段组织 discovery 输入；在阶段执行侧，`search/stages/executor.py` 保留 DB-only 分阶段执行器门面，`executor_company_stages.py` 承接公司发现 / 公司筛选 / 公司来源抓取三段实现体，`executor_common.py` 承接跨阶段的超时和客户端 helper，`resume_pending_support.py` 承接 resume-pending 阶段的候选人画像读取与队列 merge helper；同时 `search/companies/sources.py` 继续作为来源抓取门面，`company_sources_ats.py` 承接 ATS 识别与支持来源分流，`company_sources_careers.py` 承接 careers discovery / fallback query / AI web-search fallback，`company_sources_enrichment.py` 承接岗位合并、JD enrichment 和最终 found-record 组装，`sources_fetchers.py` 下沉 ATS / careers-page fetchers 与 transport helper，`sources_helpers.py` 保留 careers page 解析、coverage 归一化和纯 URL/标题信号判断；`search/companies/selection.py` 现在已经回到单一启发式选择主线，产品入口收敛到 `select_companies_for_run()`：先经过 cooldown/eligibility gate，再按 lifecycle、pending 和支持信号排序，不再夹带默认 LLM company-fit 支路，而且 selection 内的支持信号当前主要就是 manual priority 和 region weight，`majorCompanyKeywords` 已从主链退场；`search/state/` 是进度和运行 bucket/pool 状态簿记的规范入口，其中 `runtime_run_state.py` 负责 run 进度、配置和语义画像，`runtime_candidate_state.py` 负责候选人长期池和相关 candidate-state，`runtime_run_artifacts.py` 负责 bucket/pool 持久化，`runtime_run_locator.py` 负责 runtime run 定位，`runtime_job_sync.py` 负责 runtime job row 物化与 upsert，而 `runtime_run_feedback.py` 专门负责从最近的健康 run bucket 采样反馈关键词和公司名，供 runtime config / discovery 规划复用。

The desktop app now runs the primary search pipeline in Python: it generates runtime config from candidate data and search settings, executes company discovery, company selection, job sourcing, resume-pending continuation, final scoring, and recommendation output under `search/`, then brings the results back into the desktop workspace for review and maintenance. `search/orchestration/` is now the canonical orchestration home, where `job_search_runner.py` keeps the desktop-facing runner as a thinner facade, `job_search_runner_runtime_io.py` handles read-side result loading plus SQLite-backed recommendation refresh, `job_search_runner_session.py` owns the single-run session lifecycle and progress persistence and now talks more directly to the real config/signal owners, `job_search_runner_records.py` handles job filtering, link selection, and result record building, `search_session_orchestrator.py` owns timed search-session progression through round-level helpers/outcomes and now treats recommended-job growth as real progress while reporting pre-round timeout as an explicit failure, `search_session_runtime.py` carries the session runtime helpers, `runtime_config_builder.py` assembles runtime config around explicit `RuntimeCandidateInputPrep`, `RuntimeCandidateConfigContext`, and `RuntimeConfigSections` seams, `company_discovery_queries.py` now owns both the anchor pre-planning and the pure query planner built on resolved discovery-profile inputs, and `candidate_search_signals.py` derives candidate search signals from semantic, candidate-input, and profile signal groups rather than a track-mix chain; on the company-source side, `selection.py` now owns pure heuristic company selection with cooldown gating, lifecycle ordering, pending prioritization, and support-signal tie-breaking, `sources.py` remains the facade while `sources_fetchers.py` owns ATS/careers-page fetchers and transport helpers, and `sources_helpers.py` owns parsing and normalization helpers; `search/state/` is the canonical bookkeeping/state home, where `runtime_run_state.py`, `runtime_candidate_state.py`, `runtime_run_artifacts.py`, `runtime_run_locator.py`, `runtime_job_sync.py`, and `runtime_run_feedback.py` now form the clearer runtime state seams.

### 5. 运行时目录 / Runtime Directories

路径 / Path: `desktop_app/runtime/`

当前用于存放：

- `data/`：本地数据库等运行数据
- `exports/`：导出文件
- `search_runs/`：按候选人划分的运行工作目录，主要保留导出与临时工作文件，而不是搜索主状态源
- `logs/`：运行日志

Current runtime usage:

- `data/`: local database and runtime data
- `exports/`: generated export artifacts
- `search_runs/`: per-candidate runtime workspace for exports and transient working files, not the search source of truth
- `logs/`: runtime logs

这里的一个重要约束是：`search_runs/` 目录本身是候选人级工作目录，不是“一次搜索一个新目录”的 run-id 目录。因此代码里的 latest-run 语义以最新创建的 `search_runs` 行为准，而不是看哪个旧 run 因为后续写入进度或计数而更新了 `updated_at`。

One important constraint here: `search_runs/` is a per-candidate working directory, not a one-directory-per-run id layout. As a result, latest-run semantics in code follow the newest created `search_runs` row, not whichever older row most recently touched `updated_at` during later progress/count writes.
## 当前数据流 / Current Data Flow

一个典型运行链路如下：

1. 用户在桌面端创建候选人档案。  
   A user creates a candidate profile in the desktop app.
2. 用户补充简历、目标地区、岗位方向和搜索配置。  
   The user adds resume data, location preferences, role directions, and search settings.
3. 桌面端把这些信息写入 SQLite。  
   The desktop app writes that state into SQLite.
4. 当触发搜索时，桌面端生成 Python 搜索引擎的运行时配置。  
   When a search is triggered, the desktop app creates runtime config for the Python search engine.
5. Python 搜索引擎在 `search/orchestration/` 下执行公司发现、岗位抓取、续跑分析与推荐结果整理。  
   The Python search engine executes company discovery, job retrieval, pending-job continuation, and recommendation output assembly under `search/orchestration/`.
6. 桌面端通过规范 `search/` 模块直接读取 SQLite 运行态与评审状态，并生成需要的 Excel 导出。  
   The desktop app reads SQLite-backed runtime and review state directly through the canonical `search/` modules and generates Excel exports when needed.
7. 用户在结果页继续维护关注、投递和去留状态。  
   The user continues to manage focus, applied, and keep-or-drop states in the results view.

## 目录角色划分 / Directory Roles

| Path | 中文角色 | English Role |
| --- | --- | --- |
| `desktop_app/` | 新版产品主线，承载桌面工作台和本地数据层 | Main product line for the desktop workspace and local data layer |
| `docs/` | 项目对外说明文档 | Public-facing project documentation |

## 当前架构边界 / Current Architectural Boundaries

当前架构仍处于演进阶段，主要边界包括：

- 运行方式以 Windows 本地使用为主
- 自动化测试和工程化保护在持续补强中
- GitHub 层面的产品说明刚开始补齐，代码能力与外部叙事仍在对齐中

The current architecture is still in transition. Key limitations include:

- the default operating mode is still local Windows usage
- automated testing and engineering guardrails are still being strengthened
- the repository story on GitHub is still being aligned with the real code state

## 后续演进方向 / Next Architectural Direction

更合理的下一步不是继续堆脚本，而是：

- 补强桌面端对运行状态、结果解释和导出的控制力
- 继续压缩运行时噪音和多余回退逻辑，让 Python 搜索主线与桌面端边界更简单
- 让“按候选人维护求职 pipeline”成为系统的中心，而不是附属功能

The better next step is not to add more loose scripts. It is to:

- improve desktop control over run state, explanation, and export behavior
- keep reducing runtime noise and unnecessary fallback logic so the Python search path and desktop boundaries stay simple
- make candidate-centered job-search pipeline maintenance the real center of the system
