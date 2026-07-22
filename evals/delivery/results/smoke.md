# MootCourt Lab 交付验收报告

- 模式：`smoke`
- 结果：`PASS`
- 生成时间：`2026-07-22T05:16:42.164306+00:00`
- 案件：`CASE-001@0.2.0-dev`
- 会话：`9f6995e6-fc88-4e80-9535-6361b69f82f8`

| 检查项 | 结果 | 耗时(ms) | 详情 |
| --- | --- | ---: | --- |
| API 健康 | 通过 | 50.7 | mootcourt-api is healthy |
| Web 健康 | 通过 | 10.0 | HTTP 200 |
| Elasticsearch 健康 | 通过 | 12.1 | cluster status green |
| 数据库迁移 | 通过 | 30.5 | database revision 20260721_0010 |
| 案卷与角色隔离 | 通过 | 205.1 | CASE-001@0.2.0-dev; role materials are isolated |
| 法律索引 | 通过 | 144.2 | 2 hits; trace 3e389f9f-8c51-4bd2-bbf8-b84b72db249f |
| 庭审会话创建 | 通过 | 62.9 | session 9f6995e6-fc88-4e80-9535-6361b69f82f8; initial phase COURT_OPENING |

失败项：无
