# MootCourt Lab 交付验收报告

- 模式：`smoke`
- 结果：`PASS`
- 生成时间：`2026-07-23T03:33:51.706757+00:00`
- 案件：`CASE-001@0.2.0-dev`
- 会话：`9ac68a43-22ea-4722-a021-7c3ea7695a13`

| 检查项 | 结果 | 耗时(ms) | 详情 |
| --- | --- | ---: | --- |
| API 健康 | 通过 | 37.2 | mootcourt-api is healthy |
| Web 健康 | 通过 | 9.2 | HTTP 200 |
| Elasticsearch 健康 | 通过 | 11.9 | cluster status green |
| 数据库迁移 | 通过 | 32.0 | database revision 20260721_0010 |
| 案卷与角色隔离 | 通过 | 72.0 | CASE-001@0.2.0-dev; role materials are isolated |
| 法律索引 | 通过 | 100.2 | 2 hits; trace e9ddc984-eaa7-4256-b55c-8edb49326b6d |
| 庭审会话创建 | 通过 | 68.7 | session 9ac68a43-22ea-4722-a021-7c3ea7695a13; initial phase COURT_OPENING |

失败项：无
