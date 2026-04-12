# GitHub Repo Setup Suggestions / GitHub 仓库设置建议

这个文件不是 GitHub 自动读取的配置文件，而是一份可直接照着填写的仓库展示建议清单。

This file is not an automatically consumed GitHub configuration file. It is a practical checklist for how the repository should be presented on GitHub.

## About 描述建议 / Suggested About Description

中文：

面向有专业经验者的本地化岗位发现工作台：先从经验匹配公司，再发现更合适的公开岗位。

English:

Local-first job discovery workspace for experienced professionals. Match companies from real experience before chasing generic job listings.

## README 首屏建议 / README Above-The-Fold Guidance

GitHub 首屏最重要的是让访问者在 10 秒内看懂三件事：

1. 这是给谁用的
2. 它和通用招聘平台有什么不同
3. 当前主入口是 `desktop_app/`，`legacy_jobflow_reference/` 是旧版参考引擎

The first screen of the repository should let a visitor understand three things within ten seconds:

1. who the product is for
2. how it differs from generic job platforms
3. that `desktop_app/` is the main entry point and `legacy_jobflow_reference/` is the older engine layer

## Topics 建议 / Suggested Topics

可以从下面按需挑选 6 到 10 个：

You can choose around 6 to 10 of the following:

- `job-search`
- `career-tools`
- `desktop-app`
- `pyside6`
- `sqlite`
- `openai`
- `windows`
- `career-discovery`
- `systems-engineering`
- `mbse`
- `verification-and-validation`
- `digital-twin`

## Social Preview 建议 / Social Preview Guidance

如果后面要补仓库社交预览图，建议文案尽量短，突出：

- 面向专业型人才
- 先找公司，再找岗位
- 本地工作台

If you later add a social preview image, keep the message short and emphasize:

- specialist-oriented job discovery
- find the right companies before the right roles
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
- 在 Release Notes 里区分“本次已完成”和“仍在规划中”
- 把启动方式、依赖和已知限制写清楚

If GitHub Releases is the public download channel, it is recommended to:

- publish `Job-Hunter-<version>-win64.zip` through GitHub Releases
- attach a `.sha256` checksum file alongside the archive
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
