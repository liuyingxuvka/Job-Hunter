# Roadmap / 路线图

## 当前状态 / Current Status

当前仓库已经从“脚本参考实现”走到“桌面工作台雏形”阶段，但还没有到产品完成态。

The repository has moved from a script-oriented reference implementation to an early desktop-workspace stage, but it is not yet a finished product.

已经比较明确的方向是：

- 桌面应用会成为主入口
- 本地数据库会成为状态和结果的中心
- 旧版搜索引擎会在一段时间内继续作为执行层存在

The direction is already fairly clear:

- the desktop app becomes the main entry point
- the local database becomes the center of state and result management
- the legacy engine remains the execution layer for a while

## 阶段一：把桌面工作台做扎实 / Stage 1: Make The Desktop Workspace Solid

当前阶段的重点不是扩太多新功能，而是把已有主链路做稳定。

The near-term priority is not feature sprawl. It is to make the existing main workflow reliable.

目标包括：

- 候选人信息、简历路径、地点偏好和备注维护更顺畅
- 目标岗位设立与编辑流程更清晰
- AI 设置、模型检测和失败提示更可理解
- 搜索结果页的信息密度、筛选逻辑和状态维护更实用
- 运行日志、失败原因和重试入口更透明

Goals include:

- smoother management of candidate data, resume paths, location preferences, and notes
- clearer target-role setup and editing flows
- more understandable AI settings, model detection, and failure states
- a more usable results page for filtering and status maintenance
- more transparent logs, failure reasons, and retry entry points

## 阶段二：让搜索和结果管理真正可持续 / Stage 2: Make Search And Result Management Sustainable

在桌面壳子稳定之后，下一步应当让“长期维护求职 pipeline”更完整。

Once the desktop shell is stable, the next step is to make long-term job-pipeline maintenance genuinely sustainable.

重点方向包括：

- 更稳定的运行记录和历史结果回看
- 更好的公司池维护和候选公司解释
- 更清晰的推荐理由、匹配证据和结果排序
- 更实用的导出能力，如 Excel / JSON 的统一管理
- 对搜索结果做更强的去重、隐藏、状态同步和人工复核支持

Important directions include:

- more stable run history and result review over time
- better company-pool maintenance and candidate-company explanation
- clearer recommendation reasons, matching evidence, and result ranking
- more practical export handling such as unified Excel and JSON management
- stronger deduplication, hide-state handling, sync, and manual review support

## 阶段三：逐步减少对旧版引擎的依赖 / Stage 3: Reduce Dependence On The Legacy Engine

当前项目最现实的演进方式，不是一次性重写，而是渐进替换。

The most realistic evolution path is not a full rewrite. It is gradual replacement.

后续可以逐步迁移的模块包括：

- 配置生成逻辑
- 公司发现逻辑
- 岗位抓取与标准化逻辑
- 匹配分析与结果落库逻辑

Modules that can be migrated gradually include:

- config generation
- company discovery
- job retrieval and normalization
- match analysis and result persistence

目标是让桌面端不只是“启动器”，而是真正拥有自己的应用层和运行编排能力。

The goal is to make the desktop app more than a launcher by giving it its own application logic and orchestration capability.

## 暂不优先的方向 / Lower-Priority Directions For Now

以下内容现在不应排在最前：

- 自动投递
- 过早扩展成大众求职产品
- 过度承诺求职信、面试流程、Offer 管理等全链路功能
- 在基础链路还不稳定时，提前引入过多云端协作能力

The following are not top priorities right now:

- automatic applications
- expanding too early into a mass-market job product
- overcommitting to full-stack job-search features like cover letters, interviews, and offer management
- introducing too much cloud collaboration before the core workflow is stable

## 衡量进展的更好方式 / Better Measures Of Progress

这个项目是否在往对的方向走，不应只看“功能数量”，更应看：

- 候选人信息是否能长期维护而不混乱
- 目标岗位方向是否越来越具体、可执行
- 搜索结果是否比通用平台推荐更贴近专业背景
- 用户是否能围绕目标公司和岗位持续推进，而不是每天重新开始

Progress should not be measured only by feature count. Better signals are:

- whether candidate information stays maintainable over time
- whether target roles become more specific and actionable
- whether search results fit professional backgrounds better than generic platform recommendations
- whether users can move forward through company and role pipelines instead of starting from scratch every day

## 说明 / Note

这份路线图是当前仓库状态下的工程方向说明，不是对发布日期或商业能力的承诺。

This roadmap is an engineering-direction document for the current repository state, not a promise of release dates or commercial readiness.
