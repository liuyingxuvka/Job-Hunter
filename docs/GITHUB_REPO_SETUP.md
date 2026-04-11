# GitHub Repo Setup Suggestions

这个文件不是 GitHub 自动读取的配置文件，而是一份可直接照着填写的仓库展示建议清单。

## About 描述建议

### 中文版

面向有专业经验者的本地化岗位发现工作台：先从经验匹配公司，再发现更合适的公开岗位。

### 英文版

Local-first job discovery workspace for experienced professionals. Match companies from real experience before chasing generic job listings.

## README 首屏建议

GitHub 首屏最重要的是让访问者在 10 秒内看懂三件事：

1. 这是给谁用的
2. 它和通用招聘平台有什么不同
3. 当前主入口是 `desktop_app/`，`legacy_jobflow_reference/` 是旧版参考引擎

## Topics 建议

可以从下面按需挑选 6 到 10 个：

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

## Social Preview 建议

如果后面要补仓库社交预览图，建议文案尽量短，突出：

- 面向专业型人才
- 先找公司，再找岗位
- 本地工作台

## Homepage 建议

如果暂时没有官网，可以先留空。不要为了填满而放一个不维护的链接。

后面更适合使用的主页地址包括：

- 发布页
- 产品演示页
- 详细文档页

## Release 建议

如果后面开始做可下载版本，推荐：

- 用 GitHub Releases 发 Windows 包
- 在 Release Notes 里区分“本次已完成”和“仍在规划中”
- 把启动方式、依赖和已知限制写清楚

## 当前仍建议手动决定的内容

以下内容涉及项目治理或法律边界，不建议自动替你填：

- `LICENSE`
- 是否开启 Discussions
- 是否要求 PR Review
- 分支保护规则
