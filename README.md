# 全来店报价服务

> [English version →](README.en.md)

全来店产品线的服务端报价系统：独占定价算法、价格基线、PDF/XLSX 渲染、文件存储和审计日志，把客户端缩成一段 HTTP 包装。

**版本：** 见 [`VERSION`](VERSION) 与 [`CHANGELOG.md`](CHANGELOG.md);线上版本通过 `GET /healthz` 实时查询。**运行环境:** Python 3.10+ · FastAPI · uvicorn · SQLite

> **2026-09-13 起的当前报价口径：** 产品名称、分类和标准报价全部读取 `references/product_catalog_v2.json`。1–30 店按真实门店数、门店套餐系数 1.00 出主报价；31–49 / 50–99 / 100–199 / 200–300 店分别以下锚点 31 / 50 / 100 / 200 店按 0.95 出主报价，并在阶梯报价中同时展示对应上锚点 49 / 99 / 199 / 300 店按 0.90 的结果；标准报价列始终按 1.00 展示。折扣只作用于门店套餐。实施服务、VIP 售后和权益账户按 `references/quote_rules_v2.json` 与 `docs/pricing-algorithm.md` 执行。旧 Markdown 目录和旧成本算法只保留兼容用途，不得覆盖新版目录和价格。

---

## 第一部分 — HTTP API 参考

> 本节是本服务对外暴露的 HTTP API 参考，主要供配套技能仓库 [`quanlaidian-quote-skills`](https://github.com/WoSai/quanlaidian-quote-skills) 集成调用。技能仓库封装了鉴权、表单装配、下载链接渲染等细节，是推荐的客户端；本节同时也作为其他直接集成方的契约文档。

### Base URL

```
https://<your-api-host>
```

实际地址由运维方提供（UAT 阶段可能通过 IP 直连）。技能仓库以 `QUOTE_API_URL` 环境变量读取，避免硬编码。

### 鉴权

所有请求必须在 `Authorization` 头中携带 Bearer token：

```
Authorization: Bearer <token>
```

Token 由服务端管理员按组织发放（`python -m app.cli add-token --org <name>`，默认 180 天过期，`--no-expire` 可覆盖）。Token 存储在 `quote.db` 的 `api_token` 表中，服务端仅保存 `sha256(plaintext)`。缺失 / 错误 / 吊销 / 过期均返回 `HTTP 401`。所有 quote 资源按 `org` 隔离，跨组织访问返回 `HTTP 404`。

Token 运维命令：

```bash
python -m app.cli list-tokens                  # 列出所有 token（不打印 hash）
python -m app.cli revoke-token --id tok_xxxx   # 吊销
python -m app.cli migrate-tokens-json          # 一次性：把旧 data/tokens.json 迁入 api_token 表
```

---

## 业务接口

### POST /v1/quote — 算价 + 渲染 PDF/XLSX/JSON

唯一对外的报价接口。一次调用里完成：定价 → 持久化 → 生成 PDF/XLSX/JSON 三份文件，返回 preview + 三个下载链接。

同 `(org, 规范化 form)` 幂等：重复提交同一份表单，服务端返回同一 `quote_id`，不会产生重复 DB 行。

**请求体：** 参见下方 [QuoteForm 字段表](#quoteform-字段)。

**响应片段 — HTTP 200：**

```json
{
  "request_id": "req_…",
  "pricing_version": "catalog-v2-stage2",
  "preview": {
    "brand": "新版报价测试",
    "meal_type": "轻餐",
    "stores": 50,
    "package": "轻餐连锁供应链版",
    "discount": 0.95,
    "totals": { "list": 121800, "final": 118425 },
    "items": [ /* … */ ]
  },
  "files": {
    "pdf":  { "url": "…", "filename": "…", "expires_at": "…" },
    "xlsx": { "url": "…", "filename": "…", "expires_at": "…" },
    "json": { "url": "…", "filename": "…", "expires_at": "…" }
  }
}
```

---

## 运维接口

### GET /healthz

健康检查 — 无需鉴权。

```json
{ "status": "ok", "service_version": "1.3.0", "pricing_version": "catalog-v2-stage2" }
```

`service_version` 来自仓库根目录 [`VERSION`](VERSION) 文件,可作为运维 / 调用方确认线上部署版本的唯一权威来源。`pricing_version` 是定价算法版本(随路由策略变化,见 [`/v1/quote`](#post-v1quote--算价--渲染-pdfxlsxjson) 响应中的同名字段)。

### GET /files/{token}/{filename}

按 token 限定的 URL 下载已生成的文件——**仅在 `QUOTE_STORAGE_BACKEND=local` 时生效**。生产环境由 nginx 直接从磁盘提供（`alias data/files/`），此路由仅作为开发环境兜底。切换到 OSS 后端后，`POST /v1/quote` 返回的 `files[*].url` 直接是 OSS 签名 URL（可选经由 `QUOTE_OSS_PUBLIC_BASE_URL` 改写为 CDN 域名），不再经过本服务——详见 [文件存储后端](#文件存储后端)。

---

## QuoteForm 字段

`POST /v1/quote` 请求体字段：

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|
| `客户品牌名称` | string | ✅ | — | 客户品牌名称 |
| `餐饮业态` | string | ✅ | 非空 | 例如咖啡、奶茶、火锅，用于推荐餐型及模块 |
| `餐饮类型` | string | ✅ | `"轻餐"` 或 `"正餐"` | 餐饮类型 |
| `门店数量` | integer | ✅ | 1 – 300 | 必须填写真实精确数量；301+ 转人工定价；31–300 生成当前区间上下锚点对比 |
| `所需功能描述` | string | ✅ | 非空 | 点餐收银、会员、小程序、库存、财务软件对接等需求 |
| `门店套餐` | string | ✅ | — | 套餐名，必须与 [`references/product_catalog_v2.json`](references/product_catalog_v2.json) 一致 |
| `门店增值模块` | string[] | ❌ | — | 可选门店增值模块 |
| `总部模块` | string[] | ❌ | — | 可选总部模块 |
| `配送中心数量` | integer | ❌ | ≥ 0，默认 0 | 配送中心数量 |
| `生产加工中心数量` | integer | ❌ | ≥ 0，默认 0 | 生产加工中心数量 |
| `企业微信SCRM语鹦版数量` | integer | 条件必填 | ≥ 1 | 选择语鹦版时用于计算企业微信实施费 |
| `POS交付方式` | string | ❌ | 远程/现场/正餐大酒楼现场 | 省略时默认远程；实施费数量等于主报价门店数 |
| `是否购买VIP售后服务` | boolean | ❌ | 默认 `false` | 选择后按目录 100 元/门店/年计费 |
| `成交价系数` | float | 兼容字段 | 0.01 – 1.0 | 最新版 JSON 目录报价会忽略该字段，套餐系数固定按门店阶梯执行；仅旧目录兼容链路保留 |
| `人工改价原因` | string | 兼容字段 | 非空 | 仅旧目录兼容链路显式改价时使用 |
| `是否启用阶梯报价` | boolean | ❌ | 默认 `false` | 启用阶梯报价 |
| `实施服务类型` | string | 兼容字段 | — | 仅旧目录兼容链路使用；最新版目录按套餐及总部模块自动联动实施服务 |
| `实施服务人天` | integer | 兼容字段 | ≥ 0，默认 0 | 仅旧目录兼容链路使用 |

---

## 错误响应

所有错误共用一个信封：

```json
{
  "error": {
    "code": "OUT_OF_RANGE",
    "message": "具体错误说明",
    "field": "<相关字段，可选>",
    "hint": "<提示，可选>",
    "request_id": "req_20260419165334_ac8dbe5f"
  }
}
```

| HTTP | `code` | 触发原因 |
|---|---|---|
| 401 | — | 缺失或错误的 Bearer token |
| 422 | `INVALID_FORM` | 请求体不符合 schema（缺必填字段、值越界等）|
| 400 | `OUT_OF_RANGE` | 业务规则越界（如提供 `成交价系数` 但缺 `人工改价原因`）|
| 500 | `PRICING_FAILED` | 定价算法错误（如基线或产品目录缺失）|
| 500 | `RENDER_FAILED` | PDF 或 XLSX 生成错误 |
| 500 | `INTERNAL_ERROR` | 未预期的服务端错误 |

---

## 配套技能仓库

[`quanlaidian-quote-skills`](https://github.com/WoSai/quanlaidian-quote-skills) 是本服务唯一推荐的客户端。它以 Python 标准库实现零依赖调用，封装鉴权、表单提交、下载链接渲染。集成只需两个环境变量：

```bash
export QUOTE_API_TOKEN=<your_token>      # 向本服务管理员申请
export QUOTE_API_URL=https://<your-api-host>/v1/quote
```

安装、运行、自动升级等请直接参考技能仓库的 README。

---

## 第二部分 — 项目说明与架构

### 背景

旧版报价系统是分发到每台用户 OpenClaw 节点的"胖技能"：155 KB Python + 加密价格数据 + 解密 key + 飞书凭据，全部在每台用户机器上。带来的问题：

- 飞书 API 在用户节点偶发失败
- 自动升级后定价算法被破坏（依赖 / 基线 / 算法版本漂移）
- 安全暴露：定价 key 和凭据散落在所有客户端

本服务是该重构的服务端那一半。所有敏感逻辑收归服务端：定价算法、价格基线（运行时 XOR+SHA256 混淆解码，明文不落盘）、PDF/XLSX 生成、文件存储、SQLite 持久化、审计日志。

---

### 仓库结构

```
quanlaidian-quote-service/
├── pyproject.toml
├── .env.example                       # 含 PRICING_BASELINE_KEY / STRICT
├── app/
│   ├── main.py                        # FastAPI 入口 + lifespan 初始化 SQLite
│   ├── config.py                      # Pydantic Settings（前缀 QUOTE_）
│   ├── auth.py                        # Bearer token 校验
│   ├── audit.py                       # 追加式 JSONL 审计
│   ├── errors.py                      # 统一异常类 + 处理器
│   ├── storage.py                     # LocalDiskStorage / OssStorage（返回 file_token 或 OSS signed URL）
│   ├── cli.py                         # Token 管理 CLI
│   ├── api/
│   │   ├── quote.py                   # POST /v1/quote
│   │   ├── files.py                   # GET /files/{token}/{filename}
│   │   └── health.py                  # GET /healthz
│   ├── domain/
│   │   ├── schema.py                  # Pydantic 请求/响应模型
│   │   ├── pricing.py                 # 最新目录价格、套餐阶梯及阶段二联动算法
│   │   ├── pricing_baseline.py        # .obf 运行时解码 + 明文回退
│   │   ├── quote_service.py           # 算价/渲染业务逻辑
│   │   ├── render_pdf.py              # reportlab PDF
│   │   └── render_xlsx.py             # openpyxl XLSX
│   └── persistence/                   # SQLite 持久层（B0）
│       ├── db.py                      # 连接 + schema 初始化
│       ├── models.py                  # Quote / QuoteRender / Approval 数据类
│       └── quote_repo.py              # CRUD + 幂等逻辑
├── data/                              # 运行时状态（不入 VCS）
│   ├── quote.db                       # SQLite: quote / quote_render / approval / api_token
│   ├── pricing_baseline.json          # 明文基线（可选，obf 作为首选）
│   ├── fonts/                         # CJK 字体（PDF 渲染用）
│   ├── files/                         # 本地生成的文件（仅 local 后端，7 天 TTL）
│   └── audit/                         # YYYY-MM-DD.jsonl 审计
├── references/
│   ├── product_catalog_v2.json        # 最新产品名称、分类与对客报价（权威来源）
│   ├── quote_rules_v2.json            # 跨模块购买、实施、售后与权益规则
│   ├── product_catalog.md             # 旧目录兼容文件
│   └── pricing_baseline_v5.obf        # 混淆基线（生产首选）
├── docs/
│   └── pricing-algorithm.md           # 定价算法说明（审计参考）
├── tests/                             # 所有测试全绿
│   ├── conftest.py
│   ├── fixtures/
│   ├── test_api.py                    # 端点集成测试
│   ├── test_persistence.py            # SQLite 单元测试
│   ├── test_pricing.py
│   ├── test_render.py
│   ├── test_schema.py
│   ├── test_storage.py
│   ├── test_auth.py
│   └── test_errors.py
└── ops/
    ├── nginx.conf.example
    ├── systemd/quanlaidian-quote.service
    ├── cron/cleanup-files.sh
    ├── migrate_baseline.py            # .obf → 明文 JSON（应急）
    ├── obfuscate_baseline.py          # 明文 JSON → .obf（调价后重加密）
    ├── extract_baseline_from_xlsx.py  # 底价 xlsx → 明文 JSON
    └── runbook.md                     # 部署运维手册
```

---

### 模块职责

#### `app/config.py` — Settings

Pydantic `BaseSettings` 从环境变量读取配置：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `QUOTE_API_BASE_URL` | `https://<your-api-host>` | 公开 base URL（请向管理员获取实际地址） |
| `QUOTE_DATA_ROOT` | `data` | 文件、token、DB、审计的根目录 |
| `QUOTE_FILE_TTL_DAYS` | `7` | 生成文件的过期天数（本地盘为清理周期，OSS 为签名 URL 过期） |
| `QUOTE_LOG_LEVEL` | `INFO` | 日志级别 |
| `QUOTE_STORAGE_BACKEND` | `local` | 文件存储后端：`local` 或 `oss`（阿里云 OSS） |
| `QUOTE_OSS_ENDPOINT` | `oss-cn-hangzhou.aliyuncs.com` | OSS endpoint |
| `QUOTE_OSS_BUCKET` | `private-wosai-statics` | OSS bucket 名 |
| `QUOTE_OSS_PREFIX` | `quanlaidian-quote` | OSS 对象 key 前缀 |
| `QUOTE_OSS_PUBLIC_BASE_URL` | `https://private-resource.shouqianba.com` | 签名 URL host 改写（通常指向 CDN）|
| `QUOTE_OSS_ACCESS_KEY_ID` | — | OSS AK（`oss` 后端必填）|
| `QUOTE_OSS_ACCESS_KEY_SECRET` | — | OSS SK（`oss` 后端必填）|
| `PRICING_BASELINE_KEY` | — | `.obf` 基线解密密钥（生产必填）|
| `PRICING_BASELINE_STRICT` | `0` | 设为 `1` 时拒绝明文回退，只走 obf+key（生产推荐）|

#### `app/storage.py` — 文件存储后端

`POST /v1/quote` 生成的 PDF / XLSX / JSON 文件通过 `Storage` 抽象落地。由 `QUOTE_STORAGE_BACKEND` 切换：

- **`local`（默认）** — `LocalDiskStorage`：文件写入 `data/files/<file_token>/<filename>`，响应里的 URL 指向本服务的 `GET /files/{token}/{filename}`。生产一般由 nginx `alias data/files/` 直出，`QUOTE_FILE_TTL_DAYS` 后由 `ops/cron/cleanup-files.sh` 清理。
- **`oss`** — `OssStorage`（阿里云 OSS，`oss2` SDK）：对象写入 `<prefix>/<token>/<filename>` key，响应里的 URL 是 `bucket.sign_url` 生成的签名 URL（有效期 = `QUOTE_FILE_TTL_DAYS`）。配置 `QUOTE_OSS_PUBLIC_BASE_URL` 可把 host 改写为 CDN / 自定义域，签名部分保留。`oss` 后端下 `GET /files/...` 路由不再被调用。

两种后端都把定位凭据（`file_token` / `object_key`）持久化到 `quote_render.file_token`，`render_to_file_ref` 在返回时按后端类型解引用。

#### `app/domain/pricing_baseline.py` — 基线运行时解码

运行时用 `PRICING_BASELINE_KEY` 解密 `references/pricing_baseline_v5.obf`（XOR+SHA256 混淆，从 legacy 移植），明文不落盘。strict 模式强制走 obf+key；非 strict 时优先 obf，回退到 `data/pricing_baseline.json`，二者都缺直接启动失败（不再静默 fallback 到空 items — 旧版海底捞 bug 的根因）。

#### `app/domain/pricing.py` — 定价算法

最新版 JSON 目录链路以 `product_catalog_v2.json` 的“对客报价”为全部产品的价格基线，不再用旧成本倍数覆盖新版价格。门店套餐按 1.00 / 0.95 / 0.90 的门店阶梯计算，门店增值、总部模块、实施服务和 VIP 售后均不参与套餐折扣；配送中心、生产加工、企微实施费及权益账户展示按 `quote_rules_v2.json` 联动。旧成本基线仅供旧目录兼容和内部追溯。详细算法见 [`docs/pricing-algorithm.md`](docs/pricing-algorithm.md)。

入口：`build_quotation_config(form_dict, baseline, product_catalog_path) → dict`

#### `app/domain/quote_service.py` — 业务逻辑层

`price_and_persist`、`render_format`、`build_preview`、`render_to_file_ref` 等函数。`POST /v1/quote` 端点由这些函数组合而成。

#### `app/persistence/` — SQLite 持久层

三张表：

- `quote`：每次定价一行（按 `org + form_hash` 去重）
- `quote_render`：每次文件生成一行
- `approval`：每个 quote ≤ 1 行，固定 `state = not_required`（审批流 2026-04-20 业务下线后，此表保留只为审计留痕）

`CREATE TABLE IF NOT EXISTS` 方式在 FastAPI lifespan 启动时初始化。UAT 阶段未用 Alembic。

#### `app/api/quote.py` — 业务端点

`POST /v1/quote` — 服务对外暴露的唯一业务端点。内部是一次 compound call：`price_and_persist → render(pdf) → render(xlsx) → render(json) → QuoteResponse`。

#### `app/auth.py` + `app/cli.py` — Token

`secrets.token_urlsafe(32)` 生成 256-bit 随机 token,只存 `sha256(plaintext)` 到 `data/quote.db` 的 `api_token` 表(字段:`token_id / token_hash / org / created_at / expires_at / revoked_at / last_used_on`)。明文仅创建时打印一次。每次请求在表中按 hash 查活跃行(过滤 `revoked_at` 和 `expires_at`)并用 `hmac.compare_digest` 做常数时间比对。日粒度更新 `last_used_on`(进程内缓存去重,每 token/每 UTC 日最多一次写)。

#### `app/audit.py` — 审计日志

每条成功的报价请求追加一行 JSON 到 `data/audit/YYYY-MM-DD.jsonl`。字段：`ts`、`request_id`、`quote_id`、`org`、`token_id`、`brand`、`stores`、`package`、`discount`、`final`、`pricing_version`、`status`、`duration_ms`。

---

### 请求流水线

```
quanlaidian-quote-skills
    │
    │ POST /v1/quote  {QuoteForm}
    │
    ▼
FastAPI → auth → schema → quote_service → pricing → render(pdf/xlsx/json) → storage → audit
                                              │
                                              ▼
                                       SQLite data/quote.db
                                       (quote / quote_render / approval)
```

响应 `QuoteResponse` 含 preview + 3 个文件 URL（pdf/xlsx/json），URL 由 `storage` 后端发放：`local` 后端下指向本服务的 `/files/...`，`oss` 后端下是 OSS 签名 URL（可经 `QUOTE_OSS_PUBLIC_BASE_URL` 改写为 CDN 域名）。两者都带 `QUOTE_FILE_TTL_DAYS` 天 TTL。

---

### 运维栈

| 组件 | 工具 |
|---|---|
| WSGI 服务器 | uvicorn |
| 反向代理 | nginx（TLS 用 certbot）|
| 进程管理 | systemd（`ops/systemd/quanlaidian-quote.service`）|
| 持久化 | SQLite（`data/quote.db`）|
| 文件存储 | 本地盘（`local` 后端）或阿里云 OSS（`oss` 后端，`oss2` SDK）|
| 本地文件清理 | cron（`ops/cron/cleanup-files.sh`；OSS 后端由生命周期规则自行回收）|
| 基线维护 | `ops/extract_baseline_from_xlsx.py` → `ops/obfuscate_baseline.py`（调价后重加密）|

详细的首次部署、token 发放、日志查看、回滚、基线轮换见 [`ops/runbook.md`](ops/runbook.md)。

---

### 开发环境

```bash
git clone <repo>
cd quanlaidian-quote-service
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                 # 填入 PRICING_BASELINE_KEY
uvicorn app.main:app --reload
```

跑测试：

```bash
pytest tests/ -v
# 全部测试绿
```

发放开发 token：

```bash
python -m app.cli add-token --org dev
# 打印 token_id + 明文（明文只显示一次）
# 明文作为客户端的 QUOTE_API_TOKEN 环境变量
```

其他 token 管理命令：

```bash
python -m app.cli list-tokens
python -m app.cli revoke-token --id tok_xxxxxxxx
python -m app.cli add-token --org staging --expires-in 30d   # 自定义过期
python -m app.cli add-token --org admin --no-expire          # 永不过期
```

---

## 发版与变更日志

本仓库采用 [SemVer](https://semver.org/lang/zh-CN/)(`MAJOR.MINOR.PATCH`)。版本号记录在仓库根目录的 [`VERSION`](VERSION) 文件,与 [`pyproject.toml`](pyproject.toml) 的 `[project].version` 保持一致(由 `tests/test_version.py` 守门)。完整变更记录见 [`CHANGELOG.md`](CHANGELOG.md);线上当前版本可用 `GET /healthz` 实时查询(`service_version` 字段)。

### 工作流

每次合并到 `main` 之前,**任何用户可感知的改动**(API 行为变化、报价/渲染逻辑变化、CLI/脚本签名变化、运维流程变化)必须:

1. 决定语义级别:
   - `major`——不兼容的 API 变更(URL/payload 结构、status code 语义、响应字段含义改变)
   - `minor`——向后兼容的功能/行为新增
   - `patch`——bug 修复或纯文档/CI 调整
2. 跑 `python3 scripts/bump_version.py --level <major|minor|patch>` 自动:
   - 更新 [`VERSION`](VERSION)
   - 同步 [`pyproject.toml`](pyproject.toml)
   - 在 [`CHANGELOG.md`](CHANGELOG.md) 顶部插入新版本骨架(`## X.Y.Z (YYYY-MM-DD)`)
3. 手工编辑 [`CHANGELOG.md`](CHANGELOG.md),在新版本骨架下补完实际条目(每条以 `[#PR号]` 前缀,参考已有条目)
4. `git add VERSION pyproject.toml CHANGELOG.md && git commit -m "chore: bump to X.Y.Z"`

ECS 自动部署脚本下次拉取后(每 4 小时一次或手工触发),客户端 `curl https://<api>/healthz` 即可看到 `service_version` 已切到新版本。

### 不需要 bump 的变更

纯重构(无外部行为变化)、注释 / typo、测试本身的调整、`.gitignore`、IDE 配置等。**判断标准:调用方或运维能否从 API 响应或线上行为感知到这次变化。** 仅改 `tests/`、`docs/` 注解、CI 配置等不需要 bump。
