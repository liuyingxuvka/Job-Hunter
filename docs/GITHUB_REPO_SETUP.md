# GitHub Repo Setup Suggestions / GitHub 仓库设置建议

这个文件不是 GitHub 自动读取的配置文件，而是一份可直接照着填写的仓库展示建议清单。

This file is not an automatically consumed GitHub configuration file. It is a practical checklist for how the repository should be presented on GitHub.

## About 描述建议 / Suggested About Description

中文：

面向有专业经验者的本地化岗位发现工作台：持续沉淀公司、岗位和判断，而不是一次性搜职位。

English:

Local-first Windows job discovery workspace for experienced professionals. Build reusable company memory instead of doing one-shot job search.

## README 首屏建议 / README Above-The-Fold Guidance

GitHub 首屏最重要的是让访问者在 10 秒内看懂三件事：

1. 这是给谁用的
2. 它和通用招聘平台有什么不同
3. 当前主入口是 `desktop_app/`，并且桌面端已经承载 Python 原生搜索主线

The first screen of the repository should let a visitor understand three things within ten seconds:

1. who the product is for
2. how it differs from generic job platforms
3. that `desktop_app/` is the main entry point and now owns the Python-native search path

## Topics 建议 / Suggested Topics

推荐优先选下面这组，不需要全部都上，通常 8 到 12 个就够了：

Recommended priority set:

- `local-first`
- `job-search`
- `career-tools`
- `windows-app`
- `desktop-app`
- `ai-agents`
- `automation`
- `pyside6`
- `sqlite`
- `openai`
- `command-line-interface`
- `career-discovery`

当前不建议默认加上的 topic：

- `mcp`，除非仓库里已经有可用的 MCP server
- 过窄的行业词，例如 `systems-engineering`、`mbse`、`digital-twin`

Topics that are not recommended by default yet:

- `mcp`, unless the repository already ships a usable MCP server
- narrow industry tags such as `systems-engineering`, `mbse`, or `digital-twin`

## Social Preview 建议 / Social Preview Guidance

如果后面要补仓库社交预览图，建议文案尽量短，突出：

- 面向专业型人才
- 长期沉淀公司和岗位判断
- 本地工作台

If you later add a social preview image, keep the message short and emphasize:

- specialist-oriented job discovery
- reusable company and role memory
- local workspace

## Homepage 建议 / Homepage Guidance

如果暂时没有官网，可以先留空。不要为了填满而放一个不维护的链接。

If there is no maintained homepage yet, leaving the field empty is better than linking to something stale.

后面更适合使用的主页地址包括：

- 发布页
- 产品演示页
- 详细文档页

Better homepage options later may include:

- a release page
- a product demo page
- a more complete documentation landing page

## Release 建议 / Release Guidance

如果使用 GitHub Releases 作为公开下载入口，推荐：

- 用 GitHub Releases 发 `Job-Hunter-<version>-win64.zip`
- 同时附带 `.sha256` 校验文件
- 不要只推源码版本号或 tag；对普通用户可见的版本应同时挂上对应 Windows 包
- 在 Release Notes 里区分“本次已完成”和“仍在规划中”
- 把启动方式、依赖和已知限制写清楚

If GitHub Releases is the public download channel, it is recommended to:

- publish `Job-Hunter-<version>-win64.zip` through GitHub Releases
- attach a `.sha256` checksum file alongside the archive
- do not stop at a source-only version bump or tag; end-user-visible releases should include the matching Windows package
- keep release notes explicit about what is shipped now versus what is still planned
- document startup steps, dependencies, and known limitations clearly

## 当前仍建议手动决定的内容 / Items Still Best Decided Manually

以下内容涉及项目治理或法律边界，不建议自动替你填：

- `LICENSE`
- 是否开启 Discussions
- 是否要求 PR Review
- 分支保护规则

The following involve governance or legal choices and should be decided manually:

- `LICENSE`
- whether GitHub Discussions should be enabled
- whether PR review should be mandatory
- branch protection rules
