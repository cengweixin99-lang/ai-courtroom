# schemas/ 目录整理方案

## 背景

`schemas/` 目录当前有 14 个文件，混合了三类职责：API DTO、领域数据模型、评估数据集 Schema。用户希望整理目录结构，使其更清晰。

## 整理策略

**核心思路**：将 6 个评估相关文件拆到 `schemas/eval/` 子目录，`schemas/` 保留 8 个运行时文件。

### 新目录结构

```
schemas/
├── __init__.py                    # 保持为空
├── agents.py                      # Agent 输入/输出 Schema
├── case_admin.py                  # 案件导入/发布/组织管理 DTO
├── case_package.py                # 案卷 ZIP 完整数据模型
├── legal_search.py                # 法律检索请求/响应/法源文档
├── runtime.py                     # 庭审会话运行时 DTO
├── reviews.py                     # 教学复盘报告
├── health.py                      # 健康检查
├── delivery_acceptance.py         # 交付验收报告
└── eval/                          # 新增：评估数据集 Schema
    ├── __init__.py                # 新建
    ├── legal_eval.py              # 法律 RAG 评估
    ├── m5_eval.py                 # M5 最低集评估
    ├── qwen_agent_eval.py         # Qwen Agent 质量评估
    ├── qwen_turn_eval.py          # Qwen 发言质量评估
    ├── embedding_models.py        # 向量模型注册表
    └── provider_guard_load.py     # 并发保护验收
```

### 为什么这样分

- **eval/ 子目录**：6 个评估文件只在 CLI 脚本和测试中使用，不参与运行时 API，天然独立
- **runtime.py 保留**：虽然臃肿，但它是 API 路由的核心依赖，移动代价大
- **不拆 domain/**：`case_package.py` 和 `legal_search.py` 虽然包含领域模型，但它们同时被 API 路由和 CLI 引用，移到 `domain/` 会导致大量导入路径变更，收益不大

## 需要修改的导入路径

### eval/ 内部互相引用（3 处）

| 文件 | 旧导入 | 新导入 |
|------|--------|--------|
| `eval/legal_eval.py` | `from mootcourt.schemas.legal_search` | 不变（legal_search 留在 schemas/） |
| `eval/m5_eval.py` | `from mootcourt.schemas.agents` | 不变 |
| `eval/m5_eval.py` | `from mootcourt.schemas.legal_eval` | `from mootcourt.schemas.eval.legal_eval` |
| `eval/m5_eval.py` | `from mootcourt.schemas.runtime` | 不变 |
| `eval/qwen_agent_eval.py` | `from mootcourt.schemas.agents` | 不变 |
| `eval/qwen_agent_eval.py` | `from mootcourt.schemas.runtime` | 不变 |

### CLI 脚本（7 处）

| 文件 | 旧导入 | 新导入 |
|------|--------|--------|
| `cli/eval_legal.py` | `from mootcourt.schemas.legal_eval` | `from mootcourt.schemas.eval.legal_eval` |
| `cli/compare_legal_evals.py` | `from mootcourt.schemas.legal_eval` | `from mootcourt.schemas.eval.legal_eval` |
| `cli/eval_qwen_agent.py` | `from mootcourt.schemas.qwen_agent_eval` | `from mootcourt.schemas.eval.qwen_agent_eval` |
| `cli/eval_m5.py` | `from mootcourt.schemas.m5_eval` | `from mootcourt.schemas.eval.m5_eval` |
| `cli/eval_qwen_turn.py` | `from mootcourt.schemas.qwen_turn_eval` | `from mootcourt.schemas.eval.qwen_turn_eval` |

### services 层（2 处）

| 文件 | 旧导入 | 新导入 |
|------|--------|--------|
| `services/legal_eval.py` | `from mootcourt.schemas.legal_eval` | `from mootcourt.schemas.eval.legal_eval` |
| `services/legal_eval_comparison.py` | `from mootcourt.schemas.legal_eval` | `from mootcourt.schemas.eval.legal_eval` |
| `services/legal_embeddings.py` | `from mootcourt.schemas.embedding_models` | `from mootcourt.schemas.eval.embedding_models` |
| `services/m5_eval.py` | `from mootcourt.schemas.m5_eval` | `from mootcourt.schemas.eval.m5_eval` |
| `services/qwen_agent_eval.py` | `from mootcourt.schemas.qwen_agent_eval` | `from mootcourt.schemas.eval.qwen_agent_eval` |
| `services/qwen_turn_eval.py` | `from mootcourt.schemas.qwen_turn_eval` | `from mootcourt.schemas.eval.qwen_turn_eval` |
| `services/provider_guard_load.py` | `from mootcourt.schemas.provider_guard_load` | `from mootcourt.schemas.eval.provider_guard_load` |

### 测试文件（7 处）

| 文件 | 旧导入 | 新导入 |
|------|--------|--------|
| `tests/test_legal_eval.py` | `from mootcourt.schemas.legal_eval` | `from mootcourt.schemas.eval.legal_eval` |
| `tests/test_legal_eval_comparison.py` | `from mootcourt.schemas.legal_eval` | `from mootcourt.schemas.eval.legal_eval` |
| `tests/test_m5_eval.py` | `from mootcourt.schemas.m5_eval` | `from mootcourt.schemas.eval.m5_eval` |
| `tests/test_qwen_agent_eval.py` | `from mootcourt.schemas.qwen_agent_eval` | `from mootcourt.schemas.eval.qwen_agent_eval` |
| `tests/test_qwen_turn_eval.py` | `from mootcourt.schemas.qwen_turn_eval` | `from mootcourt.schemas.eval.qwen_turn_eval` |
| `tests/test_embedding_models.py` | `from mootcourt.schemas.embedding_models` | `from mootcourt.schemas.eval.embedding_models` |
| `tests/test_delivery_acceptance.py` | 不变 | 不变（delivery_acceptance 留在 schemas/） |

## 执行步骤

1. 创建 `schemas/eval/` 目录和 `__init__.py`
2. 移动 6 个评估文件到 `schemas/eval/`
3. 更新 eval/ 内部导入（1 处：m5_eval → legal_eval）
4. 更新 CLI 脚本导入（5 个文件）
5. 更新 services 层导入（7 个文件）
6. 更新测试文件导入（6 个文件）

## 验证

```powershell
cd backend
pytest
ruff check .
```
