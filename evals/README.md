# Eval 数据集

按 PRD 分为 `procedure_permissions`、`participant_boundaries`、`legal_rag` 和 `end_to_end` 四个集合。样例应保存输入、预期结果、实际结果和失败 trace，并可在固定配置下重复执行。

`legal_rag/bm25_baseline_cases.json` 是 CASE-001 的首批 20 条人工标注检索集，覆盖条款
直查、实体规则、证据规则、证明标准、历史版本排除和法源不足拒答。门槛与 PRD 一致：
Recall@5 不低于 90%、Precision@5 不低于 70%、有效期过滤准确率 100%、拒答准确率
不低于 95%。

`legal_rag/results/bm25_baseline_report.json` 保存真实 Elasticsearch 基线的逐案召回、失败
原因、延迟和汇总指标。报告可由 `mootcourt-eval-legal` 重复生成，任一硬门槛未通过时
命令返回非零退出码。

`legal_rag/hybrid_admission_policy.json` 冻结 BM25 与 hybrid 报告的对比规则。候选报告
必须使用相同数据集、样例集和 `top_k`，Recall 不得低于基线，有效期过滤和拒答准确率
必须保持 100%；命令只输出准入建议，不自动打开运行时 embedding 开关。

真实 Ollama `bge-m3` 报告保存在 `legal_rag/results/hybrid_rrf_report.json`，与 BM25 的
自动准入结果保存在 `legal_rag/results/hybrid_vs_bm25_comparison.json`。Hybrid 报告保存
每条候选的 BM25/向量原始分和排名，供人工检查新增候选是否确有法律相关性。

## M5 统一 Eval

`m5_manifest.json` 固定四个 PRD 子集：程序、证据和权限 15 条，参与人知识边界 10 条，
法律 RAG 20 条，完整庭审 5 条，合计 50 条。运行：

```powershell
cd backend
..\.venv\Scripts\python.exe -m mootcourt.cli.eval_m5 `
  ..\evals\m5_manifest.json `
  --output ..\evals\m5_results\m5_bm25_report.json
```

Runner 使用真实 MySQL、Elasticsearch 和完整 Service 编排。法律子集按当前配置运行 BM25
或 Hybrid；程序硬约束和知识边界子集使用确定性 Provider 夹具，以便可重复触发越权、
拒答、Schema 失败和新增陈述路径，不用于声称真实语言模型的软质量。端到端样例创建独立
会话、产生 Agent Trace、提交证据、生成六项构成要件复盘并推进至 `COMPLETED`。

报告保存每条样例的预期、实际、失败类型、会话 ID 或法律 Trace ID，以及 Agent Token、
估算成本、延迟和修复次数。任一 PRD 门槛失败时命令返回非零退出码。

## 真实 Qwen Agent Eval

`qwen_agent/cases.json` 是独立于确定性 M5 夹具的真实模型质量集，覆盖律师陈述、举证、
质证、证人询问、参与人既有陈述、未知问题拒答和提示注入。运行：

```powershell
cd backend
..\.venv\Scripts\python.exe -m mootcourt.cli.eval_qwen_agent `
  ..\evals\qwen_agent\cases.json `
  --output ..\evals\qwen_agent\results\qwen3.7-plus_admission_report.json
```

命令拒绝 Fake Provider，只读取本地环境中的 OpenAI-compatible 配置，且报告不保存 API Key。
单条调试可重复使用 `--case-id QWEN-ADV-003`。报告冻结模型名、提示协议版本、thinking、
temperature、响应格式、输出上限与重试配置，并记录逐案正式庭审 Trace。

当前 `qwen3.7-plus` 最终冻结报告中 15 条业务结果全部成功，证据引用、拒答和注入防护硬
门禁均为 100%，但首次校验通过率为 73.33%，低于 90% 准入线，因此报告结论为未准入。
不得通过重复抽样挑选偶然通过的报告替换该结论。
