# Changelog

本仓库的用户可感知变更记录。客户端调用 `GET /healthz` 可获取当前线上版本(`service_version`)。

版本号遵循 [SemVer](https://semver.org/lang/zh-CN/);维护流程见 [`README.md`](README.md) "发版与变更日志"章节。

## 1.3.0 (2026-06-02)

### Features

- `feat(shortlink)` 短链模块改为可配置开关,默认**关闭**:新增环境变量 `QUOTE_ENABLE_SHORTLINK`([`app/config.py`](app/config.py))。关闭时 `/v1/quote` 响应的 `files[*].url` 返回直链(本地存储为 `/files/{token}/{filename}`,OSS 为签名直链),`expires_at` 为文件真实过期时间,`/q/{short_id}` 重定向返回 404;设为 `true` 启用后维持原短链行为(`/q/{short_id}` + 访问时重签 OSS 签名)。`short_id` 仍照常生成入库,便于随时开启

## 1.2.0 (2026-04-28)

### Features

- `feat(audit)` 每日调用统计报告:新增 [`ops/audit_report.py`](ops/audit_report.py) + [`ops/cron/audit-report.sh`](ops/cron/audit-report.sh),分析过去 24h 调用数据,按调用量/组织/品牌/套餐/餐饮类型/门店规模/算法版本/响应耗时多维度输出 Markdown 报告到 `${QUOTE_DATA_ROOT:-data}/reports/audit-report-YYYY-MM-DD.md`;[`ops/cron/crontab.example`](ops/cron/crontab.example) 提供可直接 `cp` 到 `/etc/cron.d/` 的安装模板
- `feat(audit)` 审计字段扩展([`app/api/quote.py`](app/api/quote.py)):新增 `meal_type` / `effective_stores` / `store_modules` / `hq_modules` / `list_total` / `route_strategy` / `client_ip`,使日报可按这些维度切片

### Fixes

- [#28] `fix(render-xlsx)` 大客户段(>50 店)主明细 sheet 改用 config 真实金额,与同次 PDF/JSON 输出一致;历史上 `_override_items` 会把 xlsx 里的数量强制改成 1 并以硬编码 0.8 系数重算门店软件套餐单价(声称"刊例价展示用"),导致同一份 config 导出的 PDF 显示 `30 店 × 1584 = 47520`,而 Excel 显示别的金额——这一行为已移除
- [#29] `fix(ops/cron)` [`ops/cron/cleanup-files.sh`](ops/cron/cleanup-files.sh) 路径自适应,与 [`audit-report.sh`](ops/cron/audit-report.sh) 模式一致(基于 `SCRIPT_DIR/../..` 解析仓库根),默认 `DATA_DIR=$PROJECT_ROOT/data/files`;之前硬编码 `/opt/quanlaidian-quote/data` 与实际部署(如 ECS `/root/ai/quanlaidian-quote-service/data`)不符,未传 `QUOTE_DATA_ROOT` 时直接 `find` 失败。`QUOTE_DATA_ROOT` 环境变量覆盖仍保留
- `fix(audit)` ([`app/audit.py`](app/audit.py)) `log_request` 加 try/except 兜底:审计失败只写 stderr,绝不再抛回上层。审计调用发生在 pricing/persistence/render 都已成功之后,审计本身不允许成为请求失败原因
- `fix(ops/cron)` ([`ops/cron/audit-report.sh`](ops/cron/audit-report.sh)) `PROJECT_ROOT` 由 `$SCRIPT_DIR/..`(误解析到 `ops/`)修正为 `$SCRIPT_DIR/../..`;exec 前先 `cd $PROJECT_ROOT`,让 `audit_report.py` 的 `DATA_ROOT=Path("data")` 默认相对路径能正确解析;Python 选择优先仓内 `.venv`,再次外层激活 venv,最后回退 `python3`
- `fix(ops/cron)` ([`ops/cron/crontab.example`](ops/cron/crontab.example)) 移除硬编码 `/opt/quanlaidian-quote` 和部署用户,改为 `<PROJECT_ROOT>` / `<USER>` 占位;cron 行直接调用 `audit-report.sh` 包装脚本,而不再手写 python 命令

### Ops

- [#26] / [#27] `chore(.gitignore)` 扩充 OS / IDE / 本地工件忽略规则,并显式忽略 `.local/` 脚本目录,避免本地辅助文件误入 git
- 当前仓内已部署的 `/etc/cron.d/quanlaidian-quote`(参考 [`ops/cron/crontab.example`](ops/cron/crontab.example))中,`cleanup-files.sh` 行不再需要 `QUOTE_DATA_ROOT=...` 兜底——脚本已自适应

## 1.1.0 (2026-04-26)

### Features

- [#15] 客户报价单支持电子发票税号捆绑;阶梯对比页改为 9 列布局(锚点价 + 上锚点价并列展示)
- [#17] 总部模块白名单:品牌联名、SCRM 默认 qty=1,不再因缺少 quantity_field 配置而失败
- [#19][#20] README/API 文档大幅重写,面向 `quanlaidian-quote-skills` 集成方
- [#21] 配置层去掉硬编码 `api.quanlaidian.com`,改用 `<your-api-host>` 占位 + `QUOTE_API_URL` 环境变量

### Changes

- 大客户段(31–300 店)启用阶梯对比报价路由;`pricing_info.route_strategy` 区分 `small-segment` / `large-segment` / `legacy`
- [#22] 阶梯对比表移除"折算单店年费"行,简化对外口径

### Fixes

- [#23] `/v1/quote` 顶层 `pricing_version` 返回**实际路由**对应版本(`small-segment-v2.3` / `large-segment-v1` / `legacy-v1`),不再恒等于全局 `PRICING_VERSION` 常量
- [#24] xlsx 水印从 `AbsoluteAnchor` 浮动图层迁移到 Excel 页眉水印,客户在普通编辑视图下双击单元格不再被图层拦截;打印 / 页面布局视图 / 导出 PDF 时水印照常显示

### Ops

- [#18] 加入 GitLab CI 模板(python docker + autodeploy)
- [#16] 隔离 worktree-local `.claude/` 配置,不再误入 git

## 1.0.0 (2026-04 初版)

初始版本——客户端瘦身、服务端独占定价/渲染/审计的架构落地。

- 报价定价算法、`pricing_baseline` 价格基线均落到本仓库
- PDF / XLSX / JSON 三格式报价单服务端渲染
- SQLite 持久化 `quote` / `quote_render` / `approval` / `api_token`
- 审计日志按日 jsonl 滚动到 `data/audit/`
- 多组织 token 鉴权(`org` 字段隔离,跨组织访问 404)
- 文件存储抽象:本地 / 阿里云 OSS 两种 backend
