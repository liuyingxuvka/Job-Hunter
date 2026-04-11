# Contributing

感谢你愿意改进这个项目。

## 先看什么

开始之前，建议先读：

- [README.md](README.md)
- [CHANGELOG.md](CHANGELOG.md)
- [docs/PRODUCT_POSITIONING.md](docs/PRODUCT_POSITIONING.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/ROADMAP.md](docs/ROADMAP.md)

这个项目目前处于“产品定位和桌面工作台一起演进”的阶段，贡献时请优先保证叙事、界面和代码行为是一致的。

## 适合优先贡献的方向

- README、文档和 GitHub 展示信息补全
- 桌面应用的稳定性、可读性和交互清晰度
- 搜索结果管理、状态维护和导出流程
- 旧版搜索引擎到新架构的逐步迁移
- 错误处理、日志和运行反馈

## 提交改动前的建议

### 1. 大改动先开 Issue

如果你准备改动以下内容，建议先开 Issue 说明想法：

- 产品定位
- 搜索主流程
- 数据模型
- 大范围目录重构

### 2. 小改动可以直接提 PR

例如：

- 文档修正
- 文案优化
- 小范围 bug 修复
- 界面细节改进

## 本地开发

当前以 Windows 本地开发为主。

```powershell
cd .\desktop_app
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\jobflow-desktop
```

如果需要调用旧版岗位发现引擎，还需要可用的 Node.js，或者使用 `desktop_app/runtime/tools/` 下的便携 Node。

## PR 期望

提交 PR 时，请尽量说明：

- 为什么要改
- 改了什么
- 如何验证
- 是否影响产品定位、搜索逻辑或数据结构

如果改动涉及 UI，请附截图；如果涉及搜索结果或数据流程，请说明输入和输出变化。

## 文档与语言

当前仓库文档以中文为主，但欢迎在必要位置补充简短英文说明。

建议遵守：

- 文件名尽量用英文
- 文档内容可以中文优先
- 不要让 README、子目录 README 和实际代码状态互相矛盾

## 当前现实约束

请在贡献时注意：

- 项目还在演进中，不要把尚未稳定的规划写成已完成能力
- 旧版引擎仍在承担部分关键能力，不要忽略这层依赖
- 自动化测试目前并不完整，必要时请补充手工验证说明

## 版本与更新记录

当前采用轻量级版本管理：

- 小修复、说明文档更新、低风险维护，通常走 `patch`
- 新的用户可见能力或明显的流程扩展，通常走 `minor`
- 破坏兼容性或大的架构调整，再考虑 `major`

版本记录集中维护在 [CHANGELOG.md](CHANGELOG.md)。
