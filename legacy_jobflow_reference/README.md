# Legacy Jobflow Reference / 旧版 Jobflow 参考引擎

这个目录保留了当前桌面应用仍会调用的一套旧版岗位发现引擎参考实现。

This directory keeps the legacy job discovery engine that still powers part of the current desktop workflow.

## 公开仓库边界 / Public Repository Boundary

这个目录**只提交源码、说明文档和安全示例模板**。以下文件名是本地工作副本，默认必须留在个人电脑上，不能提交到 GitHub：

This folder **only commits source code, documentation, and safe example templates**. The following file names are local working copies and must stay on each contributor's machine instead of being committed to GitHub:

- `config.json`
- `config.adjacent.json`
- `companies.json`
- `companies_adjacent.json`
- `resume.md`
- `jobs*.json`
- `jobs*.xlsx`
- `config.generated*.json`
- `resume.generated.md`
- `companies.candidate.json`

这些规则已经写进 `legacy_jobflow_reference/.gitignore` 和根目录的隐私审计脚本里；如果有人误把这些文件加入版本控制，本地检查和 GitHub Actions 都会报错。

These rules are enforced by `legacy_jobflow_reference/.gitignore` and the repository privacy audit. If someone tries to commit these files, the local check and GitHub Actions will fail.

## 对外保留什么 / What Stays Public

仓库里保留的是可分享、可复用的模板：

The repository keeps only reusable public templates:

- `config.example.json`
- `config.adjacent.example.json`
- `companies.example.json`
- `companies_adjacent.example.json`
- `resume.example.md`

这些文件用于说明格式和字段，不代表任何真实候选人的个人求职数据，也不应被当作共享公司库来长期维护。

These files document the schema and expected fields. They are not real candidate data and should not become a shared production company library.

## 本地初始化 / Local Setup

在这个目录下，把示例文件复制成本地工作副本：

Inside this directory, copy the public examples into local working files:

```powershell
Copy-Item .\config.example.json .\config.json
Copy-Item .\config.adjacent.example.json .\config.adjacent.json
Copy-Item .\companies.example.json .\companies.json
Copy-Item .\companies_adjacent.example.json .\companies_adjacent.json
Copy-Item .\resume.example.md .\resume.md
```

然后只修改这些本地副本，不要直接把个人信息写回 `*.example.*` 模板。

After that, edit the local working copies only. Do not write personal data back into the `*.example.*` templates.

## 安装 / Install

在 `legacy_jobflow_reference/` 目录执行：

Run this from `legacy_jobflow_reference/`:

```powershell
npm install
```

需要可用的 `OPENAI_API_KEY`。如果你们走代理或网关，也可以设置 `OPENAI_BASE_URL`。

You will need a valid `OPENAI_API_KEY`. If you route through a proxy or gateway, set `OPENAI_BASE_URL` as well.

## 运行 / Run

主线搜索：

Mainline search:

```powershell
npm run run
```

副线搜索：

Adjacent-track search:

```powershell
node .\jobflow.mjs --config .\config.adjacent.json
```

只跑公司发现：

Company discovery only:

```powershell
node .\jobflow.mjs --discover-companies
```

## 输出文件 / Output Files

搜索结果、Excel、生成配置和临时文件都属于本地运行数据。它们默认被忽略，不应进入公开仓库。

Search outputs, Excel files, generated configs, and other runtime artifacts are local execution data. They are ignored by default and should never enter the public repository.

`companyFit` 只用于当前这一轮运行中的公司排序，不会把公司的匹配分永久写成一套固定标签。

`companyFit` is only used to rerank companies inside the current run. It is not meant to become a permanent company score.

如果你准备提交改动，建议先运行仓库根目录的隐私审计：

Before opening a commit or PR, run the repository privacy audit from the repo root:

```powershell
.\scripts\privacy_audit.ps1
```
