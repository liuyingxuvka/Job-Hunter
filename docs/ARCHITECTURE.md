# Architecture Overview

## 目标

当前仓库的架构目标，是把一套偏脚本化的岗位发现流程，逐步沉淀成一个可长期维护的本地桌面工作台。

这个架构不是一次性推倒重来，而是分层演进：

- 用新的桌面端承载候选人管理、AI 设置和结果维护
- 保留旧版搜索引擎作为当前的执行层
- 通过本地数据库把用户状态、搜索方向和运行记录沉淀下来

## 当前系统组成

### 1. 桌面应用层

路径：`desktop_app/src/jobflow_desktop_app/`

当前桌面端基于 PySide6，负责：

- 候选人目录和候选人工作台
- 基础信息管理，如所在地、目标方向、简历路径和备注
- 目标岗位方向设立与维护
- AI 设置、模型检测与可用性验证
- 搜索结果查看、删减和状态维护

它是项目未来的主入口。

### 2. 本地存储层

主要文件：

- `desktop_app/src/jobflow_desktop_app/db/schema.sql`
- `desktop_app/src/jobflow_desktop_app/db/repositories/`

当前使用 SQLite 保存本地数据，核心表包括：

- `candidates`
- `resumes`
- `search_profiles`
- `search_profile_queries`
- `companies`
- `jobs`
- `search_runs`
- `job_analyses`
- `job_review_states`
- `app_settings`

这意味着项目已经从“一次性脚本输出文件”转向“本地持续维护的数据工作台”。

### 3. AI 辅助层

主要文件：

- `desktop_app/src/jobflow_desktop_app/services/model_catalog.py`
- `desktop_app/src/jobflow_desktop_app/services/role_recommendations.py`

AI 在当前架构里主要负责两类工作：

- 校验 API Key 与模型可用性
- 帮助用户设立更具体、更可执行的目标岗位方向，并补全中英文岗位名称与描述

当前 AI 不是整个系统的唯一核心，而是增强候选人定位和搜索上下文质量的辅助层。

### 4. 搜索执行层

主要文件：

- `desktop_app/src/jobflow_desktop_app/services/legacy_runner.py`
- `legacy_jobflow_reference/jobflow.mjs`

桌面应用当前并不直接实现完整的岗位抓取与分析引擎，而是：

1. 根据候选人信息和搜索配置生成运行时配置
2. 调用 `legacy_jobflow_reference/` 中的 Node.js 搜索引擎
3. 读取该引擎输出的 JSON 结果
4. 再把结果带回桌面工作台做查看和维护

这是一种现实可用的过渡架构：先把用户工作台做好，再逐步替换旧版执行层。

### 5. 运行时目录

路径：`desktop_app/runtime/`

当前用于存放：

- `data/`：本地数据库等运行数据
- `exports/`：导出文件
- `legacy_runs/`：旧版搜索引擎的按候选人运行结果
- `logs/`：运行日志
- `tools/`：可选的本地工具依赖，例如便携 Node

## 当前数据流

一个典型运行链路如下：

1. 用户在桌面端创建候选人档案。
2. 用户补充简历、目标地区、岗位方向和搜索配置。
3. 桌面端把这些信息写入 SQLite。
4. 当触发搜索时，桌面端生成面向旧版引擎的运行时配置。
5. `legacy_jobflow_reference/` 执行公司发现、岗位抓取和匹配分析。
6. 桌面端读取 JSON 结果并展示给用户。
7. 用户在结果页继续维护关注、投递和去留状态。

## 目录角色划分

| 路径 | 角色 |
| --- | --- |
| `desktop_app/` | 新版产品主线，承载桌面工作台和本地数据层 |
| `legacy_jobflow_reference/` | 旧版搜索与分析引擎参考实现 |
| `docs/` | 项目对外说明文档 |

## 当前架构边界

当前架构仍处于演进阶段，主要边界包括：

- 搜索执行仍依赖旧版 Node.js 引擎
- 运行方式以 Windows 本地使用为主
- 自动化测试和工程化保护还不完整
- GitHub 层面的产品说明刚开始补齐，代码能力与外部叙事仍在对齐中

## 后续演进方向

更合理的下一步不是继续堆脚本，而是：

- 补强桌面端对运行状态、结果解释和导出的控制力
- 逐步把旧版引擎里的关键能力抽离并迁移到新架构
- 让“按候选人维护求职 pipeline”成为系统的中心，而不是附属功能
