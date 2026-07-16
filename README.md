# MootCourt Lab

面向法学教学的多角色模拟庭审 MVP。项目以确定性庭审控制器约束阶段、权限和证据引用，LLM 仅承担受控角色表达与教学解释，不负责自由推进流程或生成法律依据。

> 本项目仅用于虚构案件的教学模拟，不构成现实裁判或法律意见。

## 技术栈

- Web：React、TypeScript、Vite、Vitest
- API：FastAPI、Pydantic、SQLAlchemy、LangGraph、pytest
- 数据：MySQL、OpenSearch
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

前端默认访问 `http://localhost:5173`。API 可通过容器启动：

```powershell
docker compose up --build api
```

API 文档位于 `http://localhost:8000/docs`，健康检查为 `http://localhost:8000/api/v1/health`。

启动完整环境：

```powershell
docker compose up --build
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

## 目录

```text
backend/                 FastAPI 服务、业务服务、Repository、迁移和测试
frontend/                React 应用
knowledge/legal/         官方法源快照及开发阶段条款基线
evals/                   四类可复现 Eval 数据集入口
data/authoring/          待审核的 E0 案卷创作数据
PRD-MVP.md               产品需求
compose.yaml             MySQL、OpenSearch、API、Web 编排
```

校验首案数据包：

```powershell
python scripts/validate_case_package.py data/authoring/CASE-001
```

创建数据库结构并导入首案：

```powershell
cd backend
..\.venv\Scripts\alembic.exe upgrade head
..\.venv\Scripts\mootcourt-import-case.exe ..\data\authoring\CASE-001
```

导入目录由命令参数指定，并未写死。运行时从数据库读取案卷，`author_only` 内容不会进入数据库。

当前 E2.1 已提供受控 Agent 单回合接口：角色上下文按白名单构造，输出经过严格 Schema、
证据权限和陈述可追溯性校验。未配置 `LLM_MODEL` 时使用本地 Fake Provider，不会向外部
发送案卷内容。详细契约见 `backend/API.md`。

## 数据与法律审核边界

CASE-001 已标记为 `DEVELOPMENT_READY`：法域固定为上海市，刑法、盗窃司法解释、刑诉法证明责任和证明标准已形成可追溯的开发基线，可进入 E1。开发状态只允许带免责声明的教学模拟分析，不代表现实法律结论。

任何法律文本仍须来自可核验的官方来源，模型输出不得直接写入法源库。版本哈希和独立法律专业复核属于生产发布门槛，不再阻塞本地开发和内部演示。
