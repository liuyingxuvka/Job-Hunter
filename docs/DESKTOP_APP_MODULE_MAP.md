# Desktop App Module Map / 桌面端模块地图

## 目的 / Goal

这份文档只回答一个问题：`desktop_app/src/jobflow_desktop_app/` 里每个目录现在是干什么的，模块之间应该怎么理解。

This document answers one question only: what each directory under `desktop_app/src/jobflow_desktop_app/` is responsible for, and how the modules should be read together.

## 当前目标目录树 / Current Target Tree

```text
desktop_app/src/jobflow_desktop_app/
├─ main.py
├─ bootstrap.py
├─ paths.py
├─ prompt_assets.py
├─ resources/
├─ app/
│  ├─ context.py
│  ├─ main_window.py
│  ├─ theme.py
│  ├─ dialogs/
│  ├─ pages/
│  └─ widgets/
├─ ai/
│  ├─ client.py
│  ├─ model_catalog.py
│  ├─ role_recommendations.py
│  ├─ role_recommendations_models.py
│  ├─ role_recommendations_parse.py
│  ├─ role_recommendations_profile.py
│  ├─ role_recommendations_prompts.py
│  ├─ role_recommendations_resume.py
│  ├─ role_recommendations_service.py
│  └─ role_recommendations_text.py
├─ cli/
│  └─ agent.py
├─ common/
│  └─ location_codec.py
├─ db/
│  ├─ bootstrap.py
│  ├─ connection.py
│  ├─ schema.sql
│  ├─ repositories/
│  └─ seeds/
├─ search/
│  ├─ analysis/
│  ├─ companies/
│  │  ├─ selection.py
│  │  ├─ company_sources_ats.py
│  │  ├─ company_sources_careers.py
│  │  ├─ company_sources_enrichment.py
│  │  ├─ sources_fetchers.py
│  │  └─ sources_helpers.py
│  ├─ orchestration/
│  │  ├─ __init__.py
│  │  ├─ candidate_search_signals.py
│  │  ├─ company_discovery_queries.py
│  │  ├─ job_search_runner.py
│  │  ├─ job_search_runner_records.py
│  │  ├─ job_search_runner_runtime_io.py
│  │  ├─ job_search_runner_session.py
│  │  ├─ search_session_orchestrator.py
│  │  ├─ search_session_resume_gate.py
│  │  ├─ search_session_runtime.py
│  │  └─ runtime_config_builder.py
│  ├─ output/
│  ├─ stages/
│  │  ├─ executor.py
│  │  ├─ executor_company_stages.py
│  │  ├─ executor_common.py
│  │  └─ resume_pending_support.py
│  ├─ state/
│  │  ├─ __init__.py
│  │  ├─ runtime_candidate_state.py
│  │  ├─ runtime_db_mirror.py
│  │  ├─ runtime_job_sync.py
│  │  ├─ runtime_run_artifacts.py
│  │  ├─ runtime_run_feedback.py
│  │  ├─ runtime_run_locator.py
│  │  ├─ runtime_run_state.py
│  │  └─ search_progress_state.py
│  ├─ run_state.py
│  ├─ runtime_defaults.py
│  └─ runtime_strategy.py
```

运行时目录单独位于：

```text
desktop_app/runtime/
├─ data/
├─ exports/
├─ search_runs/
└─ logs/
```

## 模块职责 / Module Responsibilities

### `main.py`

- 桌面应用源码入口。
- 负责启动 QApplication、接住顶层异常、进入主窗口。

### `bootstrap.py`

- 负责把运行路径、数据库、repositories、settings、demo seed 组装成应用上下文。
- 这是“程序启动时做依赖装配”的地方，不放业务流程。

### `paths.py`

- 统一定义桌面端运行目录和资源路径。
- 所有运行时文件位置都应该先经过这里，而不是在页面或搜索逻辑里拼路径。

### `app/`

- 纯桌面 UI 层。
- 这里负责窗口、页面、对话框、控件组合、用户交互和页面间状态切换。
- 这里不负责真正的搜索算法，也不负责持久化 schema 细节。

#### `app/context.py`

- 把数据库连接、repositories、路径对象打包成 UI 可用的 `AppContext`。

#### `app/main_window.py`

- 主窗口和工作台导航。
- 负责候选人目录页与候选人工作台之间的切换。

#### `app/theme.py`

- 桌面主题和调色设置。

#### `app/dialogs/`

- 小范围、弹出式交互。
- 例如 AI 设置、手动添加岗位。
- `ai_settings.py`: AI 设置对话框门面，保留加载、保存与模型刷新编排。
- `ai_settings_api_key_source.py`: `ai_settings.py` 的 API key source / 环境变量切换与解析 helper。

#### `app/pages/`

- 工作台内的主页面。
- 每个文件应该只承载一个清晰的页面职责。

当前页面边界：

- `candidate_directory.py`: 候选人目录页
- `candidate_basics.py`: 候选人基础信息页
- `target_direction.py`: 目标岗位方向页
- `target_direction_bilingual.py`: `target_direction.py` 的页面内双语岗位名/说明补全 helper
- `target_direction_profile_completion.py`: `target_direction.py` 的页面内岗位名/岗位说明双语补全管线 helper
- `target_direction_profile_preview.py`: `target_direction.py` 的页面内只读双语预览补全 helper
- `target_direction_profile_records.py`: `target_direction.py` 的页面内岗位内容标准化与 SearchProfileRecord 组装 helper
- `target_direction_profile_ui.py`: `target_direction.py` 的页面内方向列表与表单同步 helper
- `target_direction_profile_sync.py`: `target_direction.py` 的页面内列表重建、选中恢复与表单回填编排 helper
- `target_direction_recommendations.py`: `target_direction.py` 的页面内 AI 推荐上下文构建与推荐结果落库 helper
- `target_direction_manual_add_flow.py`: `target_direction.py` 的手动新增岗位与 AI 补全过程 helper
- `target_direction_role_suggestion_flow.py`: `target_direction.py` 的 AI 推荐岗位异步流程 helper
- `target_direction_workspace_state.py`: `target_direction.py` 的候选人切换、列表重载和表单同步 helper
- `search_results.py`: 搜索结果页
- `search_results_controls_state.py`: `search_results.py` 的页面内搜索时长、倒计时与按钮文案状态推导 helper
- `search_results_status.py`: `search_results.py` 的页面内状态文案、倒计时与进度阶段格式化 helper，不单独对外暴露页面
- `search_results_live_state.py`: `search_results.py` 的页面内临时结果排序、签名与隐藏过滤 helper
- `search_results_live_runtime.py`: `search_results.py` 的实时结果轮询、阶段进度读取与临时结果刷新 helper
- `search_results_search_flow.py`: `search_results.py` 的开始/停止/重排队与后台搜索生命周期 helper
- `search_results_row_rendering.py`: `search_results.py` 的页面内单行结果表格控件装配 helper
- `search_results_prerequisites.py`: `search_results.py` 的页面内搜索前置条件与禁用原因 helper
- `search_results_links.py`: `search_results.py` 的页面内链接渲染 helper，不单独对外暴露页面
- `search_results_rendering.py`: `search_results.py` 的页面内岗位显示/评分格式化 helper
- `search_results_candidate_state.py`: `search_results.py` 的候选人切换、已有结果重载与统计刷新 helper
- `search_results_review_state.py`: `search_results.py` 的页面内评审状态变更、隐藏删除与持久化编排 helper
- `search_results_review_status.py`: `search_results.py` 的页面内状态下拉框样式与状态码归一化 helper
- `search_results_review_store.py`: `search_results.py` 的页面内状态/隐藏记录持久化 helper
- `search_results_runtime_state.py`: `search_results.py` 的倒计时、按钮状态、queued restart 字段和 notification toast helper
- `workspace.py`: 工作台容器页
- `settings.py`: 独立设置页

#### `app/widgets/`

- 被多个页面复用的小组件或通用 UI 机制。
- 当前主要是通用卡片/标题样式和异步 busy task 包装。

### `ai/`

- 与 AI 接口直接相关的模块。
- 这里包含 API 客户端、模型列表探测、岗位方向推荐与简历语义提取。

#### `ai/client.py`

- OpenAI Responses API 的通用请求/解析辅助层。
- 属于底层客户端，不关心具体 UI 页面。

#### `ai/model_catalog.py`

- 用于探测 API key 可见模型、过滤可用模型。

#### `ai/role_recommendations.py`

- AI 岗位定位能力的稳定门面。
- 真实实现已经按职责拆到以下 helper：
  `role_recommendations_models.py` 负责数据模型；
  `role_recommendations_text.py` 负责双语文本规范化与 scope 判断；
  `role_recommendations_resume.py` 负责简历读取与缺失背景判断；
  `role_recommendations_profile.py` 负责语义画像解析与缓存；
  `role_recommendations_prompts.py` 负责 prompt 入口与动态构建，稳定 prose 已迁到 `resources/prompts/ai/`；
  `role_recommendations_parse.py` 负责 AI 响应解析；
  `role_recommendations_service.py` 负责 OpenAI service 调用。

### `cli/`

- 命令行入口。
- 当前主要是 `jobflow-agent`，用于从源码/脚本侧调用岗位推荐能力。

### `common/`

- 不属于 UI、AI、数据库、搜索引擎任何一侧的通用小模块。
- 当前主要是地点结构编解码。

### `prompt_assets.py` + `resources/`

- 包内静态 prompt 资源层。
- `prompt_assets.py` 负责统一读取 `resources/prompts/` 下的稳定 prose prompt 资产。
- 这里存放“几乎不带运行时逻辑的大段说明文字”，而不是 schema、动态拼接或 query 规划代码。

### `db/`

- 本地持久化层。
- 这里负责 SQLite 连接、schema 初始化、repository 封装和 demo seed。

规则很简单：

- `repositories/` 负责数据读写
- `schema.sql` 定义数据库结构
- `bootstrap.py` 负责初始化
- `seeds/` 负责演示数据

### `search/`

- Python 原生搜索执行的核心模块层。
- 这里已经替代了旧的 `search_engine/` 路径。

#### `search/analysis/`

- 岗位分析、评分 contract、反馈调权、LLM prompt 构建。

#### `search/companies/`

- 公司发现、公司筛选、来源抓取、公司状态推进。
- `selection.py` 当前已经收成纯启发式公司选择：产品主入口收敛为 `select_companies_for_run()`，内部先经过 cooldown/eligibility gate，再按生命周期优先、pending 优先、支持信号排序和 rotation window 组织批次；其中支持信号当前主要收敛为 manual priority 和 region weight，并且会先做一次归一化，不再为每家公司重复解析配置，也不再保留默认 LLM company-fit 支路；`majorCompanyKeywords` 现在只作为 fallback override 保留，不再参与 selection 排序。
- `sources.py` 保留公司来源抓取门面与公司状态推进。
- `company_sources_ats.py` 负责 supported ATS 分流与 ATS 元信息解析。
- `company_sources_careers.py` 负责 careers discovery、fallback query 规划和 AI web-search fallback。
- `company_sources_enrichment.py` 负责岗位合并、JD enrichment 和 found-record 组装。
- `sources_fetchers.py` 负责 ATS / careers page fetchers、HTTP transport 和时间/超时 helper。
- `sources_helpers.py` 负责 careers page 解析、coverage 归一化和 URL/标题信号判断这类纯 helper。

#### `search/output/`

- 推荐结果整理、手工字段覆盖、恢复、导出。

#### `search/orchestration/`

- 搜索编排的规范入口。
- 这里承载桌面端实际调用的搜索 runner、运行时配置拼装和搜索轮次控制。
- 现在已经继续拆成更清晰的子模块：
  `job_search_runner.py` 保留桌面入口与更薄的 runner 门面；
  `job_search_runner_runtime_io.py` 负责 runner 的读侧结果加载、runtime mirror 写侧同步和推荐输出刷新；
  `job_search_runner_session.py` 负责单次搜索会话、进度写入与错误/取消结果封装，并开始直接对接 runtime config / candidate signal 的真实 owner；
  `job_search_runner_records.py` 负责岗位结果过滤、链接决议和 `JobSearchResult` 组装 helper；
  搜索统计现在直接由 `job_search_runner_runtime_io.py` 基于 SQLite 运行态汇总；
  `search_session_orchestrator.py` 负责 timed search session 的主循环门面与轮次推进，并通过 round-level helper/outcome 收敛 company round、finalize 和输出刷新；其中 `RoundProgress` 已经把推荐岗位增长算作真实进展，并在 discovery round 尚未开始前就超时时显式报错；
  `search_session_runtime.py` 负责 session runtime dataclass、阶段执行与终态 helper；
  `search_session_resume_gate.py` 负责 resume/finalize gate 状态机；
  `runtime_config_builder.py` 负责运行时配置组装，并通过 `RuntimeCandidateInputPrep` / `RuntimeCandidateConfigContext` / `RuntimeConfigSections` 这些 seam 把候选人输入准备、稳定 discovery 上下文与 section dict 明确分开；其中 company-discovery queries 已不再长期保存在 candidate context 里，而是按 rotation seed 显式生成，同时 runtime section 写入已经收成统一的 bulk-update 路径，`maxCompaniesPerRun` 的有效值解析也已经收成共享 helper；`runtime_defaults.py` 现在主要保留较稳定的 canonical 用户配置面，而像 `maxJobsPerQuery`、`maxCompaniesPerRun`、`maxJobsPerCompany`、`companyRotationIntervalDays`、`maxNewCompaniesPerRun` 和分析阶段 job caps 这类运行时派生字段，已经退到 builder/strategy 在具体阶段显式注入；`runtime_strategy.py` 侧的 adaptive search 也已经进一步压成 `passWorkBudgetSeconds`、`companyBatchSize`、`discoveryBreadth`、`cooldownBaseDays` 四个高层 knob，并由共享的 `analysis_work_cap` 派生分析阶段预算；
  `company_discovery_queries.py` 负责公司发现 query 规划，并已经明确分成 anchor 前置规划和纯 planner 两层：先从候选人输入、已解析 search signals 和最近 run feedback 生成 anchor plan / discovery profile，再把这些稳定输入通过共享 query 模板转成 query list；当前 discovery bucket 里仍保留 `phrase_limit`、`query_limit`、`minimum_anchor_count` 三个显式行为参数，因为它们分别控制 anchor 截断、query 预算/轮转分布和稀疏输入下默认 anchor 回填，不是同一件事的不同名字；
  `candidate_search_signals.py` 负责候选人搜索信号、关键词清洗，以及按 semantic / candidate / profile 三组组织目标导向 / 背景导向 / discovery 提示这些更薄的语义分组。

#### `search/stages/`

- 分阶段执行器。
- 把公司发现、筛选、抓取、续跑等阶段串成一条可控执行链。
- `executor.py` 保留四个公开 stage 入口与阶段上下文 dataclass。
- `executor_company_stages.py` 负责公司发现 / 公司筛选 / 公司来源抓取三段实现体。
- `executor_common.py` 负责跨阶段共享的 payload/path/timeout/OpenAI client helper。
- `resume_pending_support.py` 负责 resume-pending 阶段的候选人画像读取和队列 merge helper。

#### `search/state/`

- 运行状态和 SQLite 运行态访问层。
- `search_progress_state.py` 负责进度与评审状态簿记。
- `runtime_db_mirror.py` 负责运行态 façade 组装，并把 run-state、candidate-state、artifact 持久化和 run-feedback 访问协调到同一边界。
- `runtime_run_state.py` 负责 `search_runs` 进度、配置和候选人语义画像快照的读写。
- `runtime_candidate_state.py` 负责候选人长期公司池与相关 candidate-state 读写。
- `runtime_run_artifacts.py` 负责 run bucket / candidate company pool 的批量写入与计数刷新。
- `runtime_run_locator.py` 负责从 run-dir 定位 candidate / project-root / runtime DB。
- `runtime_run_feedback.py` 负责从最新 run bucket 采样反馈关键词与公司名，供 runtime config / discovery 规划复用。
- `runtime_job_sync.py` 负责 runtime job bucket 的 row 物化、job/analysis upsert 与 bucket 写入。
- 当前 `latest run` 语义按最新创建的 `search_runs.id` 解释，而不是按可变 `updated_at`；这是因为 `runtime/search_runs/` 是候选人级工作目录，不是每次 run 独立新目录。

- 搜索进度、评审状态快照和状态簿记的规范入口。
- 这是原先从旧的单体 runner 文件中抽出的纯状态逻辑的新归宿。

#### `search/run_state.py`

- 运行中间态的通用读写与 merge 逻辑。

#### `search/runtime_defaults.py` 和 `search/runtime_strategy.py`

- 搜索运行时默认参数和简单策略推导。

## 依赖方向 / Dependency Direction

推荐按下面的方向理解依赖：

```text
app/* -> app/context -> db/repositories
app/* -> ai/*
app/* -> search/orchestration
search/orchestration -> ai/* + search/*
search/* -> ai/client + common/* + search/*
db/* -> no app imports
```

更直白地说：

- UI 可以调用服务和 repository
- 搜索层不能反向依赖 UI
- 数据库层不要依赖 UI 或搜索页面
- AI 服务不要依赖页面控件

## 当前直接边界 / Current Direct Boundaries

当前源码内已经移除这批临时转发层：

- 页面导出统一直接从 `app/pages/`、`app/dialogs/`、`app/widgets/` 引用
- 搜索 runner 与搜索状态统一直接从 `search/orchestration/`、`search/state/` 引用
- 岗位方向推荐统一直接从 `ai/role_recommendations.py` 引用

这意味着模块边界现在更直接，新增代码不需要再绕过中间 re-export 层。

## 测试入口 / Test Package Bootstrap

- `desktop_app/tests/__init__.py` 现在负责把 `desktop_app/src` 统一加入 `sys.path`。
- 这样 `python -m unittest desktop_app.tests...` 不再依赖 `_helpers.py` 的偶然导入顺序。

## 维护建议 / Maintenance Rules

后续继续整理时，优先遵守下面几条：

1. 新 UI 页面放进 `app/pages/`，不要再塞回一个总文件。
2. 新 AI 相关能力放进 `ai/`，不要再回流到 `services/`。
3. 新搜索执行细节放进 `search/`，优先进入 `search/orchestration/`、`search/state/` 或其下游子模块。
4. 不要重新引入兼容壳或中间转发层；新增代码应直接依赖真实模块路径。
5. 如果一个文件开始同时承担“UI + 搜索 + AI + 持久化”四种职责，就应该重新拆边界。
