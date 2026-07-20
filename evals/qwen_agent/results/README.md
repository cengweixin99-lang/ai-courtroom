# Qwen Agent Eval 结果

本目录保存真实 OpenAI-compatible Qwen 运行生成的逐案 JSON 报告。报告不包含 API Key，
但包含模型名称、模型输出、庭审会话与 Trace 标识、Token、延迟、修复次数和质量门禁结果。

`qwen3.7-plus_v3_admission_summary.json` 是当前冻结数据集与配置的正式准入结论，逐案原始
输出和 Trace 保存在运行数据库中。`qwen3.7-plus_admission_report.json` 是 v2 协议的未准入
基线；其他带版本或 `run` 后缀的文件是协议迭代期间保留的探索性运行，不得替代当前结论。
