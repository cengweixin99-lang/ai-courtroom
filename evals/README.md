# Eval 数据集

按 PRD 分为 `procedure_permissions`、`participant_boundaries`、`legal_rag` 和 `end_to_end` 四个集合。样例应保存输入、预期结果、实际结果和失败 trace，并可在固定配置下重复执行。

M0/M1 数据尚未冻结，因此初始化阶段不填充虚构的达标样例。

`legal_rag/version_filter_cases.json` 保存法源版本门禁样例。当前首条样例要求系统选择国家法律法规数据库现行版本，并拒绝已过期的 2009 官方版本。
