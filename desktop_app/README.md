# Jobflow Desktop App

> 本说明包含完整中文版本和完整英文版本。中文在前，英文在后。  
> This document includes a full Chinese version and a full English version. Chinese comes first, followed by English.

## 中文说明

### 这个子项目是什么

`desktop_app/` 是 Job Hunter 的桌面端子项目。它负责本地候选人工作台、AI 岗位方向设立、搜索结果查看，以及后续的状态维护。

如果你是第一次看这个仓库，建议先回到根目录阅读：

- [`README.md`](../README.md)
- [`docs/PRODUCT_POSITIONING.md`](../docs/PRODUCT_POSITIONING.md)
- [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)
- [`docs/DESKTOP_APP_MODULE_MAP.md`](../docs/DESKTOP_APP_MODULE_MAP.md)
- [`docs/ROADMAP.md`](../docs/ROADMAP.md)

### 当前子项目职责

这个子项目的目标，是把原本更偏脚本化的岗位发现流程，逐步沉淀成一个可长期维护的本地桌面工作台。

当前重点不在“全自动求职”，而在：

- 候选人信息管理
- 目标岗位方向设立
- AI 设置与模型验证
- 搜索结果查看与人工维护
- `search/` 下的 Python 原生搜索编排与结果维护入口

### 当前已落地的内容

基于当前代码，已经可以确认的能力包括：

- `pyproject.toml` 和可安装的桌面应用入口
- 本地 SQLite schema 与 repository 层
- PySide6 主窗口和候选人工作台
- 候选人目录与基础信息编辑
- 搜索 Profile 维护
- AI Settings 对话框，支持直接 Key、环境变量和模型检测
- 中英双语岗位方向设立与描述维护
- `search/` 下的 Python 原生搜索执行链路
- 搜索结果查看与状态维护

当前代码里，搜索编排的规范入口已经迁到 `src/jobflow_desktop_app/search/orchestration/`，并进一步拆成了桌面 runner、search session 编排、运行时配置组装、公司发现 query 规划、候选人搜索信号推导、session runtime helper 和 resume gate 几个子模块；进度/状态簿记的规范入口在 `src/jobflow_desktop_app/search/state/`。源码主线现在直接依赖真实模块路径，不再通过中间转发层。

### 当前边界

以下内容暂时不要把它理解成已经完成：

- 完整的商业化桌面产品
- 已完全定型、不会继续收敛的最终长期架构
- 已经补齐的全套设计、数据库和流程文档
- 完整的自动化测试体系

### 你应该怎么使用这个目录

#### 普通用户：不要从这里开始

如果你不是开发者，而是想直接用软件，请不要从 `desktop_app/` 目录开始，也不需要本地安装 Python。请直接去 GitHub Releases 下载 Windows 发布包。

- 发布入口：
  [https://github.com/liuyingxuvka/Job-Hunter/releases/latest](https://github.com/liuyingxuvka/Job-Hunter/releases/latest)
- 下载文件：
  `Job-Hunter-<version>-win64.zip`
- 解压后启动：
  `Jobflow Desktop.exe`

#### 开发者：从源码运行

下面这部分才是源码工作树运行方式。

开发依赖：

- Python 3.10+
- `PySide6`
- `pypdf`（用于读取 PDF 简历文本）
- OpenAI 或兼容接口配置

下载好的 Windows 发布包已经内置桌面运行时，不需要额外安装本地 Python。

推荐方式：安装后运行

推荐在这个目录下使用独立虚拟环境：

```powershell
cd .\desktop_app
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\jobflow-desktop
```

如果你希望 AI 直接读取 PDF 简历内容，请确保 PDF 本身包含可提取文本。
如果是扫描版或图片版 PDF，需要先做 OCR，或者先转成 `.docx`、`.md`、`.txt`。

备选方式：直接启动模块

如果暂时不想安装为可执行脚本，也可以直接启动模块：

```powershell
cd .\desktop_app
$env:PYTHONPATH = ".\src;.\.deps"
python -m jobflow_desktop_app.main
```

Windows 源码启动脚本：

项目里还提供了面向 Windows 的启动脚本：

```powershell
cd .\desktop_app
.\run_release.ps1
```

这个脚本会自动寻找本地 Python，并在可用时补齐 `PYTHONPATH`。

它更适合源码工作树；GitHub Release 里的发布包会直接提供 `Jobflow Desktop.exe`。

### 目录说明

| Path | 说明 |
| --- | --- |
| `src/jobflow_desktop_app/` | 桌面应用源码 |
| `src/jobflow_desktop_app/search/` | Python 原生搜索模块，包含 orchestration、state、analysis、companies、output、stages |
| `runtime/` | 本地运行数据、日志、导出和搜索运行结果 |
| `assets/` | 图标等静态资源 |
| `run_release.ps1` | Windows 启动脚本 |

### 支持

如果这个项目对你有帮助，欢迎通过下面的链接请开发者喝杯咖啡：

[通过 PayPal 请开发者喝杯咖啡](https://paypal.me/Yingxuliu)

这只是自愿支持项目维护，不代表购买技术支持、质保、优先服务、商业授权或功能定制。

## English

### What This Subproject Is

`desktop_app/` is the desktop subproject of Job Hunter. It provides the local candidate workspace, AI-assisted role setup, result review, and follow-up state management.

If you are new to the repository, start from:

- [`README.md`](../README.md)
- [`docs/PRODUCT_POSITIONING.md`](../docs/PRODUCT_POSITIONING.md)
- [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)
- [`docs/DESKTOP_APP_MODULE_MAP.md`](../docs/DESKTOP_APP_MODULE_MAP.md)
- [`docs/ROADMAP.md`](../docs/ROADMAP.md)

### Current Responsibility

The goal of this subproject is to turn a previously script-heavy job discovery flow into a maintainable local desktop workspace.

The current focus is on:

- candidate information management
- target-role setup
- AI settings and model validation
- result review and manual state maintenance
- a desktop entry point into the Python-native search orchestration under `search/`

### What Is Implemented Today

Based on the current codebase, confirmed capabilities include:

- `pyproject.toml` and an installable desktop entry point
- a local SQLite schema and repository layer
- a PySide6 main window and candidate workspace
- candidate directory and basics editing
- search-profile management
- an AI settings dialog with direct key input, environment-variable support, and model detection
- bilingual target-role setup and description management
- a Python-native search execution pipeline under `search/`
- result review and state maintenance

In the current codebase, the canonical search orchestration entrypoint lives under `src/jobflow_desktop_app/search/orchestration/`, and is now split into focused helper modules for the desktop runner, search-session orchestration, runtime-config assembly, company-discovery query planning, candidate search signals, session runtime helpers, and the resume gate. The canonical progress/state bookkeeping entrypoint lives under `src/jobflow_desktop_app/search/state/`. The active source tree now imports real modules directly instead of relying on forwarding shims.

### Current Boundaries

The following should not be interpreted as fully completed yet:

- a polished commercial desktop product
- a fully finalized long-term architecture with no further cleanup work remaining
- a fully finished set of design, database, and flow documents
- complete automated test coverage

### How To Use This Directory

#### End Users: Do Not Start Here

If you are not a developer and just want to use the app, do not start from the `desktop_app/` folder and do not install Python locally. Go directly to GitHub Releases and download the Windows build.

- Release entry:
  [https://github.com/liuyingxuvka/Job-Hunter/releases/latest](https://github.com/liuyingxuvka/Job-Hunter/releases/latest)
- Download file:
  `Job-Hunter-<version>-win64.zip`
- Launch after extraction:
  `Jobflow Desktop.exe`

#### Developers: Run From Source

The instructions below are the source-checkout workflow.

Development dependencies:

- Python 3.10+
- `PySide6`
- `pypdf` (used to extract text from PDF resumes)
- OpenAI or a compatible API endpoint configuration

The packaged Windows release already bundles the desktop runtime and does not require a separate local Python installation.

Recommended path: install and run

Recommended setup in this directory:

```powershell
cd .\desktop_app
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\jobflow-desktop
```

If you want AI role recommendations to read a PDF resume directly, the PDF must contain extractable text.
Scanned or image-only PDFs need OCR first, or should be converted to `.docx`, `.md`, or `.txt`.

Fallback path: run the module directly

If you do not want to install the console script yet, you can run the module directly:

```powershell
cd .\desktop_app
$env:PYTHONPATH = ".\src;.\.deps"
python -m jobflow_desktop_app.main
```

Windows source-checkout launch script:

The project also includes a Windows launch script:

```powershell
cd .\desktop_app
.\run_release.ps1
```

This script locates a usable Python runtime and fills in `PYTHONPATH` when available.

It is intended for the source checkout; the GitHub Release package provides `Jobflow Desktop.exe` directly.

### Directory Notes

| Path | Description |
| --- | --- |
| `src/jobflow_desktop_app/` | Desktop application source code |
| `src/jobflow_desktop_app/search/` | Python-native search modules, including orchestration, state, analysis, companies, output, and stages |
| `runtime/` | Local runtime data, logs, exports, and per-candidate search run outputs |
| `assets/` | Static assets such as icons |
| `run_release.ps1` | Windows launch script |

### Support

If this project is useful to you, you're welcome to buy the developer a coffee here:

[Buy me a coffee via PayPal](https://paypal.me/Yingxuliu)

This is voluntary support for project maintenance. It does not purchase technical support, warranty, priority service, commercial rights, or feature requests.
