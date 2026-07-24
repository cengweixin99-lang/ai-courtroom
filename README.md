# MootCourt Lab

面向法学教学的多角色模拟庭审 MVP。项目以确定性庭审控制器约束阶段、权限和证据引用，LLM 仅承担受控角色表达与教学解释，不负责自由推进流程或生成法律依据。

> 本项目仅用于虚构案件的教学模拟，不构成现实裁判或法律意见。

## 功能概览

- **案件大厅与卡片式训练入口**：学习者以卡片流浏览已发布案件，一键开始或继续庭审训练。
- **确定性庭审控制器**：阶段推进、角色权限、证据引用和发言校验由控制器规则驱动，LLM 只负责受控角色表达，不自由推进流程。
- **结构化证据流程**：举证、质证（真实性 / 合法性 / 关联性 / 证明力）、无异议、证据台账与待回应清单。
- **程序请求与陈述审核**：问题制止请求、新增陈述审核由教学控制者批准 / 驳回 / 纳入记录，过程写入公开庭审记录。
- **教学复盘**：庭审结束后可手动生成教学复盘，基于已提交证据、公开庭审材料、冻结构成要件和法律检索 Trace 输出结构化评分与建议；必要法源不足时停止生成。
- **案件管理（组织管理员）**：导入 ZIP 案卷、发布到组织、更新授权范围、删除草稿，支持版本不可变和内容哈希校验。

## 技术栈

- Web：React、TypeScript、Vite、Vitest
- API：FastAPI、Pydantic、SQLAlchemy、LangGraph、pytest
- 数据：MySQL 8.0.46、Elasticsearch 8.19.10、Kibana 8.19.10
- 通信：REST + Server-Sent Events
- 本地编排：Docker Compose

## 快速开始

要求 Node.js 22+、npm 10+、Docker Desktop（含 Compose）。本机开发 API 时另需 Python 3.12+。

```powershell
Copy-Item .env.example .env
npm install
npm run infra:up
npm run dev
```

## Supabase 认证

案件、庭审会话、Agent 调用和法律检索都需要经过 Supabase 认证的用户的访问 API。启动
Compose 之前，先在 `.env` 中配置以下值：

```dotenv
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_JWT_ISSUER=https://YOUR_PROJECT.supabase.co/auth/v1
SUPABASE_JWT_AUDIENCE=authenticated
VITE_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
VITE_SUPABASE_ANON_KEY=YOUR_PUBLISHABLE_ANON_KEY
```

禁止将 Supabase `service_role` 密钥放到前端。在创建 Supabase 项目之前如需本地隔离开发，
可显式设置 `AUTH_DEV_BYPASS_ENABLED=true` 并保持 `APP_ENV=development`；生产环境会拒绝该
绕过，且未配置 issuer 时也会安全失败。首个认证主体在 MySQL 中创建后会自动获得公共训练
案件的访问授权；会话所有权与庭审中的公诉方 / 辩护方席位相互独立。

前端默认访问 `http://localhost:5173`。API 可通过容器启动：

```powershell
docker compose up --build api
```

API 文档位于 `http://localhost:8000/docs`，健康检查为 `http://localhost:8000/api/v1/health`。
Docker 前端通过同源 `/api/v1` 代理访问 API，浏览器无需直接跨域请求 `8000` 端口。

基础设施默认端口为 MySQL `3307`、Elasticsearch `9200`、Kibana `5601`。MySQL 使用
`3307` 是为了避免与宿主机常见的本地 MySQL `3306` 冲突；容器内连接仍使用 `3306`。

启动完整环境：

```powershell
docker compose up --build
```

如果 Docker 镜像源临时不可用，但本机已经存在可运行的 `mootcourt-lab-api:latest` 和
`mootcourt-lab-web:latest`，可使用离线更新入口。脚本会先构建前端，再复用已验证的本地运行时镜像更新
API/Web；不会拉取基础镜像，也不会停止 MySQL/Elasticsearch 或删除数据卷：

```powershell
.\scripts\rebuild_local.cmd
.\scripts\accept_delivery.cmd
```

## 本机 API 开发

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
alembic upgrade head
uvicorn mootcourt.main:app --reload
```

## 验证

```powershell
npm test
npm run build
cd backend
pytest
ruff check .
```

Docker 环境启动后，可执行不消耗模型额度的交付 smoke 验收：

```powershell
.\scripts\accept_delivery.cmd
```

发布前执行真实 Qwen 完整庭审验收：

```powershell
.\scripts\accept_delivery.cmd --full
```

两种模式都会在 `evals/delivery/results` 生成 JSON 和 Markdown 报告。完整模式会创建独立
庭审会话并产生真实模型 Token 和费用；脚本不会拉取镜像、停止容器或删除数据卷。

统一 Eval Runner 覆盖 PRD 的 50 条最低集：程序权限 15 条、参与人边界 10 条、法律 RAG
20 条、端到端庭审 5 条。报告包含逐条会话/检索 Trace、可靠性门槛、Token、成本、延迟和
修复比例，任一门槛失败时命令以非零状态退出。

## 目录

```text
backend/                 FastAPI 服务、业务服务、Repository、迁移和测试
frontend/                React 应用
knowledge/legal/         官方法源快照及开发阶段条款基线
evals/                   四类可复现 Eval 数据集入口
data/authoring/          待审核的 E0 案卷创作数据
PRD-MVP.md               产品需求
compose.yaml             MySQL、Elasticsearch、Kibana、API、Web 编排
```

校验首案数据包：

```powershell
python scripts/validate_case_package.py data/authoring/CASE-001
```

## 案件导入与发布

组织管理员登录后，可从庭审大厅进入“案件管理”。上传 ZIP 后，系统先执行压缩包安全检查、
`manifest.files` 文件清单校验、Pydantic Schema 校验、跨文件证据引用和角色材料边界校验。
通过的版本只会创建为草稿；管理员勾选目标组织并发布后，学习者才能看到并创建庭审。相同
`case_id + package_version` 的内容哈希不允许变化，已有庭审继续锁定原数据库版本。

管理员不由前端或邮箱判断。将 Supabase 用户不可变的 `sub` 配置到服务端环境变量：

```dotenv
AUTH_BOOTSTRAP_ADMIN_SUBJECTS=["你的-supabase-user-sub"]
```

默认值为 `[]`，普通用户不会看到案件管理入口。ZIP 默认限制为 20 MiB、200 个文件、解压后
100 MiB、单文件最大压缩比 100；可通过 `.env.example` 中的 `CASE_IMPORT_*` 参数调整。

创建数据库结构并导入首案：

```powershell
cd backend
..\.venv\Scripts\alembic.exe upgrade head
..\.venv\Scripts\mootcourt-import-case.exe ..\data\authoring\CASE-001
```

## 核心机制

### Agent 与 LLM 调用

系统通过受控 Agent 单回合调用 OpenAI-compatible Provider 生成角色发言。角色上下文按
白名单构造，案卷及用户输入作为不可信数据封装；输出经过严格 Schema、证据权限和陈述
可追溯性校验。运行环境未配置模型或密钥时返回 `503`，不会静默使用 Fake Provider；
Fake 只允许通过 `LLM_PROVIDER=fake` 在测试环境中显式启用。详细契约见 `backend/API.md`。

### 法律检索

法律检索基于 Elasticsearch 实现，默认使用条款级 BM25，并可选启用 `dense_vector`/kNN
向量检索与应用层 RRF 混合检索。检索受案件法源白名单、法域、生效日期、效力状态和审核
状态过滤约束；必要法源不足时以 `INSUFFICIENT_LEGAL_AUTHORITY` 安全失败。

首次使用法源前需要建立索引：

```powershell
mootcourt-index-legal knowledge/legal/source_manifest.json
```

每次检索返回唯一 `trace_id`，数据库保存案件包、LegalProfile、强制过滤条件、候选快照、
两路分数与耗时；后续引用必须逐字段匹配该 Trace。伪造法源、错误条款号、篡改原文、来源
或版本哈希都会被程序拦截。

### 法律检索评估

`mootcourt-eval-legal` 门禁命令使用真实案件 LegalProfile 与 Elasticsearch 检索链计算
Recall@5、Precision@5、MRR、有效期过滤准确率和拒答准确率，并保存逐案失败 trace。当前
基线报告位于 `evals/legal_rag/results/bm25_baseline_report.json`。

向量模型需要单独评估并登记运行时准入状态。首个工程候选模型登记为 Ollama `bge-m3`
（模型 ID `790764642607`，1024 维），只有人工检查逐案 Trace 后显式修改模型档案的运行时
准入状态，才能在生产环境启用。

### 证据与庭审程序

系统维护证据状态台账，支持举证、无异议和结构化质证。质证明确真实性、合法性、关联性或
证明力维度；举证校验证据存在性、角色权限和重复提交。

庭审程序支持三类问题制止请求：无关问题、重复问题和不当问题。重复问题由程序确定性识别，
其余请求写入公开庭审记录并等待教学控制者复核。本庭新增陈述可纳入或排除庭审记录，但不
会自动关联事实。

### 教学评分与复盘

教学评分按优先证据提交、对方证据回应、必要法源覆盖和争点闭合四个维度计算综合分，并将
遗漏定位到具体证据、事实和构成要件。评分使用确定性规则，不调用 LLM 主观判分；没有对应
评价样本的维度按不适用处理，不会人为扣分。

结构化教学复盘只使用公开庭审材料、已提交证据、冻结构成要件和当前案件版本的法律检索
Trace。庭审流程到达法律分析阶段后，由用户手动点击“生成教学复盘”触发；必要法源不足时
停止生成，开发案件不会输出真实法律结论。已生成复盘的会话再次进入时会自动加载。

## 数据与法律审核边界

CASE-001 已标记为 `DEVELOPMENT_READY`：法域固定为上海市，刑法、盗窃司法解释、刑诉法证明责任和证明标准已形成可追溯的开发基线，可进入 E1。开发状态只允许带免责声明的教学模拟分析，不代表现实法律结论。

任何法律文本仍须来自可核验的官方来源，模型输出不得直接写入法源库。版本哈希和独立法律专业复核属于生产发布门槛，不再阻塞本地开发和内部演示。
