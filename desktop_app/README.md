# Jobflow Desktop App

这个文件夹用于规划下一代桌面求职软件，不直接放现有脚本运行数据。

当前目标：
- 把现有 `jobflow` 脚本沉淀成可给同事使用的桌面软件
- 第一阶段只做：用户配置、岗位发现、过滤、匹配评分、结果查看、Excel 导出
- 第二阶段再做：求职信最小/中等/最大修改

建议文档组织：
- `PRODUCT_PLAN.md`：产品目标、范围、用户流程、版本边界
- 后续可继续增加：
  - `ARCHITECTURE.md`
  - `DATABASE.md`
  - `UI_FLOW.md`
  - `TASKS.md`

当前已完成文档：
- `PRODUCT_PLAN.md`
- `ARCHITECTURE.md`
- `DATABASE.md`
- `UI_FLOW.md`
- `TASKS.md`

为什么先用 Markdown：
- 适合和代码一起版本管理
- 改动历史清楚
- 结构化程度高，便于拆任务
- 后续可以很容易导出成 PDF / Word

当前约定：
- 规划文档优先用中文写
- 文件名尽量用英文
- 每个文档只负责一个主题，避免把所有内容堆到一个文件里

## 当前代码骨架

当前已经开始进入第一阶段代码实现，主要目录：
- `src/jobflow_desktop_app/`
- `tests/`
- `runtime/`

已落地内容：
- `pyproject.toml`
- SQLite schema
- 数据库初始化
- 最小桌面主窗口
- 候选人可编辑界面
- 搜索 Profile 可编辑界面
- 运行记录 / 岗位结果占位页面

## 本地运行

建议在这个目录下使用独立虚拟环境：

```powershell
cd .\jobflow_desktop_app
python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\jobflow-desktop
```

如果暂时只想做数据库层测试：

```powershell
cd .\jobflow_desktop_app
$env:PYTHONPATH = ".\src"
python -m unittest discover -s tests
```

如果当前机器的 Python 没带 `venv`，也可以直接使用本地依赖目录方式开发：

```powershell
cd .\jobflow_desktop_app
$env:PYTHONPATH = ".\src;.\.deps"
python -m jobflow_desktop_app.main
```

项目里也提供了启动脚本：

```powershell
cd .\jobflow_desktop_app
.\run_local.ps1
```
