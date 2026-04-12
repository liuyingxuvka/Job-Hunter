# AI Integration Notes / AI 集成说明

## Purpose / 目的

This document explains how AI agents and developers should read this repository, what the current capability surface is, what must stay private, and which integration surfaces are planned next.

本文说明 AI agent 和开发者应该如何理解这个仓库、当前可用的能力范围、哪些内容必须保持私有，以及下一步规划中的接入面。

## Capabilities / 可用能力

The repository currently centers on a local, candidate-centric job discovery workflow. The core product direction is:

当前仓库的核心是一个以候选人为中心、本地运行的岗位发现工作流。主要方向包括：

- candidate profile management, including identity, contact details, location preferences, notes, and resume paths
- target-role setup with bilingual role names and descriptions
- AI settings and model validation for local desktop usage
- company-first job discovery and search execution through the current legacy engine layer
- search-result review, follow-up state, and local persistence

- 候选人档案管理，包括身份信息、联系方式、地区偏好、备注和简历路径
- 目标岗位设立，支持中英文岗位名称与说明
- 面向本地桌面使用的 AI 设置与模型校验
- 通过当前旧版引擎层进行公司优先的岗位发现和搜索执行
- 搜索结果审核、后续状态维护和本地持久化

For an AI assistant, the useful mental model is: this repo is a local workspace that turns candidate input into target-role definitions, search context, discovery output, and ongoing review state.

对 AI assistant 来说，最有用的理解方式是：这个仓库是一个本地工作台，把候选人输入转成目标岗位定义、搜索上下文、发现结果和持续维护的审核状态。

## Privacy Boundary / 隐私边界

Do not treat personal runtime data as public project material.

不要把个人运行数据当成公开项目材料。

Keep these local:

以下内容必须留在本地：

- real resumes and personal notes
- candidate-specific contact details and location data
- company pools built from private research
- search outputs, exports, backups, and SQLite databases
- API keys, environment secrets, and machine-specific runtime files

- 真实简历和个人备注
- 候选人专属联系方式和地区数据
- 由私有研究形成的公司池
- 搜索结果、导出文件、备份和 SQLite 数据库
- API Key、环境密钥和机器相关运行文件

If you need to test or document behavior, use synthetic or anonymized data only. If you need to share an example, strip personal identifiers and any data that could reveal a real job-search history.

如果你需要测试或写文档，只能使用合成数据或匿名数据。如果你需要分享示例，请去掉个人标识，以及任何可能暴露真实求职轨迹的数据。

See also: [Repository Boundary](./REPOSITORY_BOUNDARY.md)

## Current And Planned Integration Surfaces / 当前与规划中的接入面

The repository is moving toward machine-friendly entry points. One CLI surface now exists in experimental form, and broader integrations are planned next.

仓库正在逐步变成更适合机器消费的入口。当前已经有一个实验性的 CLI，后续还会继续扩展其他接入面。

### CLI / 命令行

Available today in experimental form through `jobflow-agent`:

当前已通过实验性的 `jobflow-agent` 提供：

- `overview`
- `list-candidates`
- `get-candidate --candidate-id <id>`
- `list-profiles --candidate-id <id>`
- `recommend-roles --candidate-id <id>` when OpenAI settings are available

- `overview`
- `list-candidates`
- `get-candidate --candidate-id <id>`
- `list-profiles --candidate-id <id>`
- 在已配置 OpenAI 设置时使用 `recommend-roles --candidate-id <id>`

Current behavior:

当前行为：

- returns JSON by default
- uses the local desktop bootstrap and SQLite workspace
- reuses existing repositories and AI services instead of going through the UI

- 默认返回 JSON
- 使用本地桌面应用 bootstrap 和 SQLite 工作区
- 复用已有 repository 和 AI service，而不是通过 UI 绕行

Example:

示例：

```powershell
cd .\desktop_app
.\.venv\Scripts\jobflow-agent overview
.\.venv\Scripts\jobflow-agent list-candidates
.\.venv\Scripts\jobflow-agent get-candidate --candidate-id <candidate-id>
```

A headless CLI is still the most practical first step for agents. The current implementation should be treated as an early compatibility surface that can grow over time.

无头 CLI 仍然是最现实的第一步。当前实现应被视为早期兼容层，后续会继续扩展。

Next useful commands:

- `run-search`
- `get-search-progress`
- `list-results`
- `update-review-state`

后续更有价值的命令包括：

- `run-search`
- `get-search-progress`
- `list-results`
- `update-review-state`

### MCP / Model Context Protocol

An MCP server would let external AI clients call the repository as a tool instead of reverse-engineering the UI.

MCP server 可以让外部 AI client 直接把仓库当工具调用，而不是去反向理解 UI。

Planned tool examples:

- `list_candidates`
- `get_candidate`
- `recommend_roles`
- `run_search`
- `get_search_progress`
- `list_recommended_jobs`
- `update_job_review_state`

规划中的 tool 示例：

- `list_candidates`
- `get_candidate`
- `recommend_roles`
- `run_search`
- `get_search_progress`
- `list_recommended_jobs`
- `update_job_review_state`

### JSON / 结构化输出

Whether the entry point is CLI, a local service, or a future MCP server, the default contract should be structured output that is easy to parse, diff, and test.

无论入口是 CLI、本地服务还是未来的 MCP server，默认契约都应该是结构化输出，便于解析、对比和测试。

Good JSON responses should include:

好的 JSON 返回建议包含：

- a stable schema
- explicit identifiers
- human-readable summary fields
- machine-readable status fields
- timestamps and trace references when relevant

- 稳定的 schema
- 明确的标识符
- 人类可读的摘要字段
- 机器可读的状态字段
- 必要时附带时间戳和追踪引用

The current `jobflow-agent` CLI already follows a simple wrapper format:

当前 `jobflow-agent` CLI 已采用一个简洁的包装格式：

```json
{
  "ok": true,
  "command": "list-candidates",
  "data": {}
}
```

Errors should remain explicit and machine-readable:

错误返回也应保持明确且机器可读：

```json
{
  "ok": false,
  "error": {
    "code": "candidate_not_found",
    "message": "Candidate 123 not found."
  }
}
```

## Demo And Safe-Data Guidance / Demo 与安全数据指引

Use demo seeds, safe templates, and synthetic records when demonstrating the system to humans or agents.

对人类或 agent 展示系统时，请使用 demo 种子、安全模板和合成记录。

Recommended practice:

建议做法：

- keep demo resumes and sample candidates clearly labeled as non-real
- avoid company names, contact data, and work history that could be mistaken for real personal information
- keep release-package data small and obviously synthetic
- make it obvious which files are examples and which files are runtime data

- 明确标注 demo 简历和样例候选人不是现实数据
- 避免使用可能被误认为真实个人信息的公司名、联系方式和工作经历
- 让发布包里的数据尽量小且明显是合成的
- 清楚区分哪些文件是示例，哪些文件是运行数据

If you are building a new agent workflow, start with demo candidates only. Move to local real data only after the privacy boundary is understood and respected.

如果你要构建新的 agent 工作流，请先只使用 demo 候选人。只有在理解并遵守隐私边界之后，才考虑本地真实数据。

## Example Workflows / 示例工作流

The following examples describe the kinds of tasks an AI agent should eventually be able to perform with minimal UI dependence.

下面这些示例描述了 AI agent 未来应该能够尽量少依赖 UI 完成的任务。

1. Read a candidate profile, summarize the likely target roles, and propose more precise search directions.
2. Trigger a search using the target-role context, then summarize the companies and roles that deserve attention.
3. Review result status, update focus or applied state, and prepare a short handoff note for the human user.
4. Draft repository documentation or code changes that improve discoverability, while preserving the local-data boundary.

1. 读取候选人档案，总结可能的目标岗位，并提出更精确的搜索方向。
2. 使用目标岗位上下文触发搜索，然后总结值得关注的公司和岗位。
3. 审核结果状态，更新关注或投递状态，并为人类用户准备简短交接说明。
4. 起草能提升 discoverability 的仓库文档或代码改动，同时保持本地数据边界不被破坏。

## Where To Read Next / 下一步阅读

- [Architecture Overview](./ARCHITECTURE.md)
- [Repository Boundary](./REPOSITORY_BOUNDARY.md)
- [GitHub Repo Setup Suggestions](./GITHUB_REPO_SETUP.md)

- [架构概览](./ARCHITECTURE.md)
- [仓库边界](./REPOSITORY_BOUNDARY.md)
- [GitHub 仓库设置建议](./GITHUB_REPO_SETUP.md)
