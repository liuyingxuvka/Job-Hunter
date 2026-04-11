# Product Positioning / 产品定位

## 核心判断 / Core View

Job Hunter 的价值，不在于给用户推荐“更多职位”，而在于帮有专业经验的人更快识别“更值得追踪的公司和岗位”。

The value of Job Hunter is not that it recommends more jobs. Its value is that it helps experienced professionals identify the companies and roles that are actually worth tracking.

对于很多中高级候选人来说，求职难点并不是看不到岗位，而是：

- 主流平台推荐过于泛化，噪声大
- 真正匹配的岗位标题并不稳定，常常不会直接写成自己熟悉的岗位名
- 很多公司对专业能力有真实需求，但未必会放出一个“完全对应简历关键词”的 JD
- 单靠关键词搜职位，容易错过相邻领域或组织结构不同但需求相近的机会

For many mid-career and senior candidates, the problem is not the absence of openings. The problem is that:

- mainstream platform recommendations are too generic and noisy
- truly relevant roles often use inconsistent titles
- many companies need the capability without publishing a perfectly matching JD
- keyword-only search can miss adjacent domains or differently structured teams

所以这个项目的产品逻辑不是“从职位池里找热门岗位”，而是：

1. 从候选人的既有经验出发  
   Start from the candidate's real experience.
2. 识别可能真正需要这种能力的公司  
   Identify companies that are more likely to need that capability.
3. 再进入这些公司的公开岗位和招聘入口  
   Explore those companies' public openings and hiring entry points.
4. 最后在本地工作台里持续筛选、跟踪和维护结果  
   Track, filter, and maintain the results inside a local workspace.

## 目标用户 / Target Users

更适合以下用户：

- 已经工作一段时间、拥有明确专业能力的求职者
- 在细分行业里寻找更匹配岗位的人
- 想从当前行业迁移到相邻方向，但又不想丢掉核心能力的人
- 岗位名称变化大、职责边界模糊、需要靠经验迁移来匹配职位的人

This product is a better fit for:

- candidates with established professional strengths
- people searching within specialized industries or job families
- people moving into adjacent domains without giving up their core skills
- people whose target roles require interpretation beyond exact title matching

典型方向包括但不限于：

- Systems Engineering
- Verification & Validation
- MBSE / SysML
- Integration / Interface Management
- Reliability / Diagnostics
- Digital Twin / Condition Monitoring
- Energy Equipment / Hydrogen / Electrochemical Systems

Typical directions include, but are not limited to, the areas above.

## 为什么和通用招聘工具不同 / Why It Differs From Generic Job Tools

通用招聘平台通常更擅长：

- 基于热门职位标题和平台行为做推荐
- 面向大规模用户做标准化岗位分发
- 强化“搜岗位”和“投岗位”的即时效率

Generic recruitment platforms are usually better at:

- recommending openings based on broad job titles and platform behavior
- serving standardized roles to very large user groups
- optimizing short-term speed for search and application

而 Job Hunter 更强调：

- 从经验和能力出发，而不是从大众职位池出发
- 从“公司是否需要这类能力”反推岗位，而不是只盯岗位标题
- 适合长期维护的个人求职 pipeline，而不是一次性搜索
- 本地优先，适合沉淀候选人信息、方向设定、搜索结果和投递状态

Job Hunter instead emphasizes:

- starting from real experience and capabilities instead of a generic job pool
- inferring roles from company need rather than relying only on titles
- supporting a long-lived personal job-search pipeline rather than one-off search sessions
- keeping candidate state, search direction, results, and review workflow local-first

## 典型使用场景 / Typical Usage Scenarios

### 场景一：细分行业继续深挖 / Scenario 1: Going Deeper In A Specialized Field

用户已经在某个专业领域工作过数年，希望继续沿着相近方向找更合适的平台，但不希望被大量泛岗位淹没。

A user already has years of experience in a specialized area and wants better-fit opportunities without being buried under generic openings.

### 场景二：向相邻方向迁移 / Scenario 2: Moving Into An Adjacent Direction

用户并不想彻底转行，而是希望把现有的系统、测试、可靠性、建模或装备经验迁移到更合适的行业和公司里。

A user is not trying to change careers completely, but wants to move systems, testing, reliability, modeling, or equipment experience into a better-fit industry or company.

### 场景三：岗位标题不稳定 / Scenario 3: Unstable Or Inconsistent Role Titles

同一种能力，在不同公司里可能对应完全不同的岗位标题。这个场景下，“先找公司，再找岗位”通常比“先定关键词，再搜职位”更有效。

The same capability may map to very different role titles across companies. In that situation, finding the right companies first is often more effective than starting with static search keywords.

### 场景四：长期维护求职管道 / Scenario 4: Maintaining A Long-Term Pipeline

用户不是一天刷完岗位，而是需要长期维护目标公司池、岗位池和状态进展。桌面工作台更适合这种持续管理方式。

The user is not trying to finish the search in one sitting. They need to maintain a company pool, a role pool, and state over time. A desktop workspace supports that better than repeated ad hoc searching.

## 当前产品范围 / Current Product Scope

当前版本重点聚焦：

- 候选人信息管理
- 目标岗位方向设立
- 公司优先的岗位发现
- 搜索结果查看与状态维护
- 本地数据沉淀和长期跟踪

The current version is focused on:

- candidate information management
- target-role setup
- company-first job discovery
- result review and status maintenance
- local persistence and long-term tracking

## 当前非目标 / Current Non-Goals

当前不应把项目表述成以下类型：

- 全自动投递系统
- 面向所有人群的通用求职平台
- 已经完成商业化打磨的成熟 SaaS
- 覆盖简历优化、求职信、面试管理、Offer 管理等所有求职环节的一体化产品

The project should not currently be described as:

- a fully automated application system
- a general-purpose platform for every kind of job seeker
- a polished mature SaaS product
- an all-in-one job suite covering resumes, cover letters, interviews, and offers

## 建议对外表述 / Recommended External Messaging

适合写进 GitHub 或项目介绍页的表述包括：

- 为专业型人才服务的岗位发现工具
- 从经验和能力出发，而不是从大众职位池出发
- 先找对公司，再找对岗位
- 适合长期维护个人求职 pipeline 的本地工作台

Useful phrasing for GitHub or public project pages includes:

- a job discovery tool for professionals with domain expertise
- start from experience and capability, not from a generic job pool
- find the right companies before the right roles
- a local workspace for maintaining a long-term personal job-search pipeline
