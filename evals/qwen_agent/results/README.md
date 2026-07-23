# Qwen Agent Eval 结果

当前发布准入基线是：

- 历史报告：`qwen3.7-plus_v5_full_admission.json`（使用旧模型，仅供对照）
- Prompt 协议：`agent-grounding-v5-full-output-injection-scan`
- 样例：16/16 通过
- Schema 首轮通过率：100%
- 必需证据引用率：100%
- 明确拒答准确率：100%
- 完整结构化输出提示注入泄露率：0%
- Token 低估率：0%

旧版 v4 报告仅用于历史对照。v4 的注入检测只扫描用户可见文本，未覆盖
`refused_reason` 等结构化字段，不得用于当前发布判断。
