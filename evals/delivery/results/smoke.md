# MootCourt Lab 交付验收报告

- 模式：`smoke`
- 结果：`PASS`
- 生成时间：`2026-07-23T04:18:29.803461+00:00`
- 案件：`CASE-001@0.2.0-dev`
- 会话：`4b41ed03-cd53-4abe-ba5e-a77051ea0d5f`

| 检查项 | 结果 | 耗时(ms) | 详情 |
| --- | --- | ---: | --- |
| API 健康 | 通过 | 31.4 | mootcourt-api is healthy |
| Web 健康 | 通过 | 8.4 | HTTP 200 |
| Elasticsearch 健康 | 通过 | 12.2 | cluster status green |
| 数据库迁移 | 通过 | 20.5 | database revision 20260721_0010 |
| 案卷与角色隔离 | 通过 | 96.3 | CASE-001@0.2.0-dev; role materials are isolated |
| 法律索引 | 通过 | 93.1 | 2 hits; trace ad13c941-5cf6-4e5d-a07a-b8b900c3b199 |
| 庭审会话创建 | 通过 | 48.0 | session 4b41ed03-cd53-4abe-ba5e-a77051ea0d5f; initial phase COURT_OPENING |

失败项：无
