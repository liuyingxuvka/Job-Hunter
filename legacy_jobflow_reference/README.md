# jobflow

每天自动搜集岗位（自动发现公司 + 公司官网/ATS + 可选 Web search）→ 抓取 JD → 用 OpenAI 结构化匹配（中文输出）→ 仅更新一个主表 `jobs_recommended.xlsx`（保留你手动标注的列，默认“追加模式”不丢历史记录）。

## 1) 安装

在 `jobflow/` 目录执行：

```powershell
npm install
```

要求环境变量已设置：`OPENAI_API_KEY`。匹配评估默认使用 `gpt-5.2`，并启用 `web_search` 以访问岗位页面。

也可以在 `jobflow/.env` 里配置（脚本会自动读取）：

```text
OPENAI_API_KEY=sk-...
# 可选（公司代理/网关）
OPENAI_BASE_URL=https://...
```

## 2) 配置

复制配置模板：

```powershell
Copy-Item .\\config.example.json .\\config.json
```

把你的简历（可脱敏）放到 `jobflow/resume.md`（或在 `config.json` 里改 `resumePath`）。
`candidate.scopeProfile` 用来切换匹配语义：主线配置用 `hydrogen_mainline`，副线配置用 `adjacent_mbse`。

准备公司清单（公司优先搜）：

```powershell
Copy-Item .\\companies.example.json .\\companies.json
```

你可以在 `companies.json` 里维护“电解槽/燃料电池/材料/诊断/测试/新能源车相关公司列表”，脚本会从每家公司的 careers 页面抓岗位。
支持的 ATS（自动识别/抓取）：Greenhouse、Lever、SmartRecruiters；其他类型会尝试从 careers 页面解析岗位链接或 JobPosting JSON-LD。
`companies.json` 支持 `tags`（数组），用于标注公司类别与地区（例如：`electrolyzer`, `fuel_cell`, `system`, `controls`, `materials`, `BOP`, `testing`, 以及地区标签如 `region:EU`, `region:US`, `region:CN`, `region:JP`, `region:KR`, `region:CA`, `region:AU`, `region:IL`），会写入 Excel 的 `Company Tags` 列。
可在 `sources.preferMajorCompanies` 与 `sources.majorCompanyKeywords` 中配置“大公司优先队列”，在 `maxCompaniesPerRun` 有上限时优先处理这些公司。
如需按国家/地区战略排序，可配置 `sources.priorityRegionWeights`；如需把少数重点公司长期固定到更靠前的位置，可直接在 `companies.json` 里给公司加 `priority` 数值。
可开启 `sources.rotateCompanyWindow: true`（默认开启），让每次运行采用“头部固定 + 后半段轮转”的公司处理策略，避免永远只扫前一批公司；`sources.majorCompanyPinnedCount` 控制固定保留的大公司数量，`sources.companyRotationIntervalDays` 控制轮转步进天数。
可开启 `sources.enableCompanySearchFallback: true`（默认开启），当大公司或指定地区公司的 careers 页面抓不到岗位时，自动补一轮公司定向 web search；预算由 `sources.maxCompanySearchFallbacksPerRun` 控制，地区列表由 `sources.fallbackSearchRegions` 控制。
可开启 `search.allowPlatformListings: true`，让 web search 在找不到官网/ATS 结果时，允许保留少量职业平台职位页线索（默认配置为 `linkedin.com`）。这类岗位不会自动登录平台，也不会抓完整 JD，而是只根据搜索结果里的标题/公司/地点/摘要做保守筛选；如果匹配度足够高，会以“LinkedIn线索”标签写入主表，供你手动点开查看。
可用 `analysis.platformListingRecommendScoreThreshold` 单独提高这类“平台线索”的入表阈值，默认高于普通岗位，避免噪声过多。
当前可用性判定采用“双层逻辑”：明确出现 `closed / expired / filled / 404 / 410` 等信号的岗位会被强制剔除；但对某些 JS 动态岗位页或反爬拦截页，不再要求必须抓到完整 JD 正文，只要没有明确失效信号、链接仍像真实岗位页、且在时间窗口内，就允许进入候选表或复核流程。最终写入主表时会再加一道更严格的“链接质量”过滤，优先只保留 employer/ATS 的具体岗位详情/投递页，尽量剔除 careers 首页、地区筛选页、镜像聚合页和域名停放页。
可在 `sources.cnHydrogenCompanyKeywords` 中配置“中资氢能公司关键词”（用于生成“中资赴欧岗位”独立清单）。

主表默认使用简化后的中文列，并显示 `入表日期`：`入表日期` 表示该岗位第一次进入主表 `jobs_recommended.xlsx` 的日期，便于你按入表日期筛选当天需要查看的新岗位，而不用重新翻旧岗位。
如需强制只接受 GPT 高质量结果（禁止自动降级 fallback），可设置 `analysis.strictScoring: true` 或运行时加 `--strict-scoring`。
可开启 `analysis.preFilterEnabled: true`（默认开启），先用本地启发式规则对标题/摘要/公司标签做预筛；只有预筛分数高于 `analysis.preFilterScoreThreshold` 的岗位才进入 GPT 正式评分。被预筛刷掉的岗位会保留一个本地低成本分析结果，但不会消耗 GPT 评分 token。
如需节省 token，可开启 `analysis.lowTokenMode: true`：评分仅返回 `Match Score + Recommend`（及最小必要字段），并可关闭 `analysis.scoringUseWebSearch`。
折中方案（推荐）：开启 `analysis.postVerifyEnabled: true`，仅对“候选推荐岗位”做二次 ChatGPT 复核（默认 `gpt-4o-mini + web_search`），通过复核才进推荐表。

自动公司发现（可配置 `companyDiscovery`）会在每次运行前补充新的公司名称到 `companies.json`，再由脚本自动补齐官网与招聘入口。
单表输出路径由 `output.trackerXlsxPath` 控制，默认 `./jobs_recommended.xlsx`。
如需扩一条完全独立的“副线岗位库”，可直接使用 `config.adjacent.json`：它会单独维护 `companies_adjacent.json`、`jobs_adjacent_all.json`、`jobs_adjacent_found.json`、`jobs_adjacent_recommended.json`、`jobs_adjacent.xlsx`、`jobs_adjacent_cn_europe.json`，不会和主线 Excel 混表。
副线匹配按“角色形状”筛选，重点是 MBSE / Systems Engineering / V&V / Integration / Reliability / Digital Twin / PHM / Technical Interface，不强制要求氢能或电化学行业背景；副线 Excel 会额外显示 `副线方向`、`行业簇` 两列。

## 3) 运行

```powershell
npm run run
```

运行副线独立库：

```powershell
node .\\jobflow.mjs --config .\\config.adjacent.json
```

只跑 web search（不抓 JD、不打分、不生成 Excel，只更新 `jobs.json`）：

```powershell
npm run dry-run
```

只在本地工作（不调用 OpenAI；仅把已有 `jobs.json` 导出/刷新到主表 `jobs_recommended.xlsx`）：

```powershell
node .\\jobflow.mjs --offline
```

仅公司清单，不跑 web search（公司发现是必选步骤，会自动补齐官网/招聘入口）：

```powershell
node .\\jobflow.mjs --companies-only
```

自动补齐公司官网/招聘页（会写回 `companies.json`，默认每次都会做）：

```powershell
node .\\jobflow.mjs --discover-companies
```

关闭自动公司发现（仅用现有公司清单）：

```powershell
node .\\jobflow.mjs --no-company-discovery
```

限制每次处理公司数量：

```powershell
node .\\jobflow.mjs --max-companies 30
```

从零重建（忽略旧 `jobs.json` / Excel 手工列）：

```powershell
node .\\jobflow.mjs --reset
```

强制高质量打分（任一 OpenAI 配额/限流错误即中止，不降级 fallback）：

```powershell
node .\\jobflow.mjs --reanalyze --strict-scoring
```

低 token 打分（建议与 `--reanalyze` 配合）：

```powershell
node .\\jobflow.mjs --reanalyze --low-token
```

低 token + 二次复核（推荐，质量更稳）：

```powershell
node .\\jobflow.mjs --reanalyze --low-token
```

强制把已有岗位补上中文列（不重算匹配也可）：

```powershell
node .\\jobflow.mjs --retranslate --companies-only
```

输出文件默认在：
- `jobflow/jobs.json`
- `jobflow/jobs_found.json`（本次发现的岗位清单，含是否已分析）
- `jobflow/jobs_recommended.xlsx`（唯一主表：推荐岗位 + 中资氢能赴欧岗位，见 `List Tags` 列）
- `jobflow/jobs_recommended.json`
- `jobflow/jobs_cn_europe.json`

副线配置 `config.adjacent.json` 的输出文件默认在：
- `jobflow/jobs_adjacent_all.json`
- `jobflow/jobs_adjacent_found.json`
- `jobflow/jobs_adjacent.xlsx`
- `jobflow/jobs_adjacent_recommended.json`
- `jobflow/jobs_adjacent_cn_europe.json`

## 4) Excel 手动列（不会被覆盖）

你可以在 `jobs_recommended.xlsx` 里手动维护：
- `Interest`：感兴趣/一般/不感兴趣
- `Applied`：未投/已投/面试/拒/Offer
- `Applied Date`：日期
- `Status`：正常/已失效
- `Notes`：备注

主表默认采用“追加模式”（`output.recommendedMode: "append"`）以便长期共同维护。

## 5) 过滤旧岗位

在 `config.json` 里可设置：

```json
"filters": {
  "maxPostAgeDays": 180,
  "excludeUnavailableLinks": true,
  "outputLinkRecheckHours": 72,
  "excludeAggregatorLinks": true
}
```

如果岗位发布日期早于这个天数，会自动从输出里移除，避免“过期岗位”堆积。
并且在输出推荐表前，会对候选链接做周期性复检（默认 72 小时）；`404/410/已关闭/已招满/已过期` 等失效岗位会被自动剔除。
聚合站/中转页（如 `rejobs.org`、`jobboardly` 等）也会按域名规则自动剔除，优先保留官网或 ATS 的可申请链接。

## 6) Windows 任务计划（建议）

用“任务计划程序”每天运行（工作目录设为 `jobflow/`）：

```powershell
npm run run
```

## 7) 常见问题

- 报错 `401 invalid_api_key`：你的 `OPENAI_API_KEY` 不正确/已失效。请在 OpenAI 平台创建新 key，并重新设置环境变量（注意不要带引号或多余空格）。
- 如果你们用的是公司代理网关：设置 `OPENAI_BASE_URL`（或 `OPENAI_API_BASE`）到你们的网关地址。
