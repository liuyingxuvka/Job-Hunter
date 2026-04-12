# Repository Boundary

## Purpose / 目的

这个仓库采用“公开代码与文档 + 本地个人数据”分离原则。

This repository follows a strict separation between public code/documentation and local personal working data.

目标很明确：

The goal is straightforward:

- GitHub 上保留源码、文档、演示种子和安全示例模板
- 个人简历、个人配置、公司池、搜索结果、数据库和运行备份只保留在本地

- Keep source code, documentation, demo seeds, and safe example templates on GitHub
- Keep resumes, personal configs, company pools, search outputs, databases, and runtime backups on local machines only

## 允许提交的内容 / What May Be Committed

- 应用源码、脚本和构建配置
- 面向仓库访客的说明文档
- 中立的 demo 数据和 example 模板
- 不包含个人信息的测试夹具和截图

- Application source code, scripts, and build configuration
- Repository-facing documentation
- Neutral demo data and example templates
- Tests and fixtures that do not contain personal information

## 必须留在本地的内容 / What Must Stay Local

- `legacy_jobflow_reference/config.json`
- `legacy_jobflow_reference/config.adjacent.json`
- `legacy_jobflow_reference/companies.json`
- `legacy_jobflow_reference/companies_adjacent.json`
- `legacy_jobflow_reference/resume.md`
- `legacy_jobflow_reference/jobs*.json`
- `legacy_jobflow_reference/jobs*.xlsx`
- `desktop_app/runtime/data/jobflow_desktop.db*`
- `desktop_app/runtime/backups/**`
- `desktop_app/runtime/exports/**`（保留 `.gitkeep`）
- `desktop_app/runtime/logs/**`（保留 `.gitkeep`）
- `desktop_app/runtime/legacy_runs/**`（保留 `.gitkeep`）

- `legacy_jobflow_reference/config.json`
- `legacy_jobflow_reference/config.adjacent.json`
- `legacy_jobflow_reference/companies.json`
- `legacy_jobflow_reference/companies_adjacent.json`
- `legacy_jobflow_reference/resume.md`
- `legacy_jobflow_reference/jobs*.json`
- `legacy_jobflow_reference/jobs*.xlsx`
- `desktop_app/runtime/data/jobflow_desktop.db*`
- `desktop_app/runtime/backups/**`
- `desktop_app/runtime/exports/**` (except `.gitkeep`)
- `desktop_app/runtime/logs/**` (except `.gitkeep`)
- `desktop_app/runtime/legacy_runs/**` (except `.gitkeep`)

## 发布包边界 / Release Package Boundary

Windows 发布包允许携带的是“可以公开分发的运行必需品”，不是开发者机器上的工作现场。

The Windows release package may contain only distributable runtime essentials, not a snapshot of a developer's local workspace.

允许进入发布包的内容：

- `Jobflow Desktop.exe` 和对应运行时目录
- `desktop_app/assets/`
- `desktop_app/runtime/tools/` 下的便携 Node
- `desktop_app/runtime/data/demo_candidate_resume.md`
- `legacy_jobflow_reference/` 下的安全模板、公开脚本和预装依赖

Allowed in the release package:

- `Jobflow Desktop.exe` and its bundled runtime directory
- `desktop_app/assets/`
- the portable Node runtime under `desktop_app/runtime/tools/`
- `desktop_app/runtime/data/demo_candidate_resume.md`
- safe templates, public scripts, and preinstalled dependencies under `legacy_jobflow_reference/`

不得进入发布包的内容：

- `desktop_app/runtime/data/jobflow_desktop.db*`
- `desktop_app/runtime/backups/**`
- `desktop_app/runtime/exports/**`
- `desktop_app/runtime/logs/**`
- `desktop_app/runtime/legacy_runs/**`
- `legacy_jobflow_reference/config.json`
- `legacy_jobflow_reference/companies.json`
- `legacy_jobflow_reference/jobs*.json`
- `legacy_jobflow_reference/jobs*.xlsx`

Must never enter the release package:

- `desktop_app/runtime/data/jobflow_desktop.db*`
- `desktop_app/runtime/backups/**`
- `desktop_app/runtime/exports/**`
- `desktop_app/runtime/logs/**`
- `desktop_app/runtime/legacy_runs/**`
- `legacy_jobflow_reference/config.json`
- `legacy_jobflow_reference/companies.json`
- `legacy_jobflow_reference/jobs*.json`
- `legacy_jobflow_reference/jobs*.xlsx`

## 执行规则 / Operational Rules

1. 任何候选人的真实资料都先复制到本地工作副本，不直接改 `*.example.*`。
2. 任何公司池、搜索结果、导出表格、SQLite 数据库和备份文件都视为本地运行数据。
3. 如果一个文件能反映某个人真实求职过程，它就不应进入公开仓库。

1. Copy real candidate data into local working files instead of editing `*.example.*` directly.
2. Treat company pools, search outputs, export spreadsheets, SQLite data, and backups as local runtime data.
3. If a file reflects someone's actual job-search process, it does not belong in the public repository.

## 自动化保护 / Automated Enforcement

这个边界通过四层机制保护：

This boundary is enforced in four layers:

1. `.gitignore` 和 `legacy_jobflow_reference/.gitignore` 默认忽略本地工作副本与运行输出。
2. `scripts/privacy_audit.ps1` 会检查是否有禁止上传的路径或明显泄露迹象。
3. `.github/workflows/privacy-check.yml` 会在 push 和 pull request 时自动执行审计。
4. `scripts/build_windows_release.ps1` 只从白名单内容组装发布包，并在压缩前再次执行发布包隐私审计。

1. `.gitignore` and `legacy_jobflow_reference/.gitignore` ignore local working copies and runtime outputs by default.
2. `scripts/privacy_audit.ps1` checks for blocked upload paths and obvious leak patterns.
3. `.github/workflows/privacy-check.yml` runs the audit automatically on pushes and pull requests.
4. `scripts/build_windows_release.ps1` assembles release assets from a safe allowlist and reruns a package privacy audit before zipping.

## 提交前检查 / Before You Push

```powershell
.\scripts\privacy_audit.ps1
```

如果你准备发版本，也会在 `scripts/release.ps1` 里再次执行同一套检查。

If you are preparing a release, `scripts/release.ps1` runs the same audit again before updating the version.
