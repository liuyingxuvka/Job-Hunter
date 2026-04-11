# Jobflow Desktop App

`desktop_app/` 是 Job Hunter 的桌面端子项目。它负责本地候选人工作台、AI 岗位方向设立、搜索结果查看，以及后续的状态维护。

`desktop_app/` is the desktop subproject of Job Hunter. It provides the local candidate workspace, AI-assisted role setup, result review, and follow-up state management.

如果你是第一次看这个仓库，建议先回到根目录阅读：

If you are new to the repository, start from:

- [`README.md`](../README.md)
- [`docs/PRODUCT_POSITIONING.md`](../docs/PRODUCT_POSITIONING.md)
- [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)
- [`docs/ROADMAP.md`](../docs/ROADMAP.md)

## 当前子项目职责 / Current Responsibility Of This Subproject

这个子项目的目标，是把原本更偏脚本化的岗位发现流程，逐步沉淀成一个可长期维护的本地桌面工作台。

The goal of this subproject is to turn a previously script-heavy job discovery flow into a maintainable local desktop workspace.

当前重点不在“全自动求职”，而在：

- 候选人信息管理
- 目标岗位方向设立
- AI 设置与模型验证
- 搜索结果查看与人工维护
- 旧版岗位发现引擎的桌面化入口

The current focus is not full job-search automation. It is on:

- candidate information management
- target-role setup
- AI settings and model validation
- result review and manual state maintenance
- a desktop entry point into the legacy discovery engine

## 当前已落地的内容 / What Is Implemented Today

基于当前代码，已经可以确认的能力包括：

- `pyproject.toml` 和可安装的桌面应用入口
- 本地 SQLite schema 与 repository 层
- PySide6 主窗口和候选人工作台
- 候选人目录与基础信息编辑
- 搜索 Profile 维护
- AI Settings 对话框，支持直接 Key、环境变量和模型检测
- 中英双语岗位方向设立与描述维护
- 旧版搜索引擎桥接运行
- 搜索结果查看与状态维护

Based on the current codebase, confirmed capabilities include:

- `pyproject.toml` and an installable desktop entry point
- a local SQLite schema and repository layer
- a PySide6 main window and candidate workspace
- candidate directory and basics editing
- search-profile management
- an AI settings dialog with direct key input, environment-variable support, and model detection
- bilingual target-role setup and description management
- legacy search engine bridging
- result review and state maintenance

## 当前边界 / Current Boundaries

以下内容暂时不要把它理解成已经完成：

- 完整的商业化桌面产品
- 完全独立于旧版引擎的新搜索架构
- 已经补齐的全套设计、数据库和流程文档
- 完整的自动化测试体系

The following should not be interpreted as fully completed yet:

- a polished commercial desktop product
- a fully independent search architecture without the legacy engine
- a fully finished set of design, database, and flow documents
- complete automated test coverage

## 本地运行 / Local Run

建议在这个目录下使用独立虚拟环境：

Recommended development setup in this directory:

```powershell
cd .\desktop_app
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\jobflow-desktop
```

如果暂时不想安装为可执行脚本，也可以直接启动模块：

If you do not want to install the console script yet, you can run the module directly:

```powershell
cd .\desktop_app
$env:PYTHONPATH = ".\src;.\.deps"
python -m jobflow_desktop_app.main
```

## Windows 启动脚本 / Windows Launch Script

项目里还提供了面向 Windows 的启动脚本：

The project also includes a Windows launch script:

```powershell
cd .\desktop_app
.\run_release.ps1
```

这个脚本会自动寻找本地 Python，并在可用时补齐 `PYTHONPATH` 和 Node 路径。

This script locates a usable Python runtime and fills in `PYTHONPATH` and Node-related paths when available.

## 目录说明 / Directory Notes

| Path | 中文说明 | English Description |
| --- | --- | --- |
| `src/jobflow_desktop_app/` | 桌面应用源码 | Desktop application source code |
| `runtime/` | 本地运行数据、日志、导出和旧版搜索运行结果 | Local runtime data, logs, exports, and legacy run outputs |
| `assets/` | 图标等静态资源 | Static assets such as icons |
| `run_release.ps1` | Windows 启动脚本 | Windows launch script |

## 依赖说明 / Dependencies

中文：

- Python 3.10+
- `PySide6`
- OpenAI 或兼容接口配置
- Node.js（运行旧版搜索引擎时需要；也可以使用 `runtime/tools/` 下的便携版本）

English:

- Python 3.10+
- `PySide6`
- OpenAI or a compatible API endpoint configuration
- Node.js for the legacy engine, or the portable runtime under `runtime/tools/`
