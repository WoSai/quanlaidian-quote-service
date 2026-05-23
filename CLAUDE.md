# 编程原则(Karpathy Principles)

## 1. 先想后写
- 动手前先识别需求中的歧义和隐藏假设,不确定就**先问**,不要替用户瞎猜往下做。
- 列出关键决策点,等确认后再写代码。

## 2. 简单优先
- 用最少的代码解决问题:100 行能搞定就不要写 1000 行。
- 不加投机性功能、不做"以后可能用得上"的抽象、不过度设计。
- 不为不可能发生的情况加防御性校验,只在系统边界(用户输入、外部接口)做校验。

## 3. 外科手术式改动
- 只改完成任务**必需**的代码,不顺手"优化"或重构无关部分。
- bug 修复就只修 bug,不附带周边清理。
- 不引入向后兼容的临时 hack。

## 4. 目标驱动执行
- 把任务转成**可验证的成功标准**(测试、运行结果、明确的验收条件)。
- 持续迭代直到标准满足,而不是写完就算完。
- 报告结果时基于实际验证(跑过测试/运行过),不要凭空声称成功。

## 通用
- 默认不写注释;只在"为什么这么做"非显而易见时(隐藏约束、绕坑、反直觉行为)才写一行。
- 注释解释 WHY,不解释 WHAT(命名已经说明 what)。

# 本项目须知

- **项目性质**:这是全来店报价的**服务端**(FastAPI · uvicorn · SQLite),不是 skill 项目。独占定价算法、价格基线、PDF/XLSX 渲染、文件存储、审计日志都在这里;配套技能仓库 [`quanlaidian-quote-skills`](https://github.com/WoSai/quanlaidian-quote-skills) 是薄客户端,只通过本服务的 HTTP API 取报价。代码主目录在 `app/`(`api/` 路由、`domain/` 定价与渲染、`persistence/` SQLite、`storage.py` 文件存储、`cli.py` token 运维)。
- **报价唯一来源**:报价结果由服务端 `app/domain/`(`pricing.py` + `pricing_baseline`)独占计算,价格基线不进版本库明文。任何客户端都不得本地估算或拼装报价。
- **测试**:`python -m pytest`(集成测试 `tests/test_api.py`、持久化 `tests/test_persistence.py`、定价/渲染/存储等)。改定价或渲染规则要同步更新对应测试并跑全绿。注意 `pyproject.toml` 把 `oss2` 列为依赖但其在本地模式下惰性导入,且部分渲染测试依赖外部目录文件——在缺基线/目录的环境下这些用例会跳过或失败,属已知环境限制。
- **依赖**:依赖声明在 `pyproject.toml`,运行时 `fastapi`/`uvicorn`/`pydantic(-settings)`/`reportlab`/`openpyxl`/`fonttools`/`oss2`,dev 依赖 `pytest`/`httpx`/`ruff`。新增依赖必须进 `pyproject.toml`。
- **存储后端**:`QUOTE_STORAGE_BACKEND` 在 `local` 与 `oss` 间切换(`app/storage.py`);所有配置走 `app/config.py` 的 `QUOTE_` 前缀环境变量,不要硬编码 host/密钥。
- **鉴权与隔离**:所有请求带 `Authorization: Bearer <token>`,token 由 `python -m app.cli add-token` 发放、仅存 `sha256`;资源按 `org` 隔离,跨组织访问返回 404。
- **提交与发版**:用 Conventional Commits 前缀(`feat:`/`fix:`/`docs:`/`refactor:`/`perf:` …);用户可感知变更记进 `CHANGELOG.md`,版本号写入 `VERSION`,线上版本通过 `GET /healthz` 查询。
