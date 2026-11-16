# dev_ai 合并修复说明

日期：2026-11-16。

## 修复范围

- 每日一题保留知识点筛选，已分配的当天题目仍固定；没有匹配题目时提示空范围。
- AI 判分的学习状态、判分来源、判分详情和每日打卡在同一事务内保存。判分返回时核对作答修订号，过期结果、重复判分和已完成自评的作答不会被覆盖；详情保存失败时整体回滚。
- 判分详情新增 `submission_revision`，只在题目版本及作答修订号匹配时展示。此前无法可靠对应修订号的快照仍保留在库中，但不会显示为当前作答的结论。
- 云端同时传入多标签、多知识点时保留两个筛选条件；关键词搜索包含知识点名称，与本地行为一致。
- 离线测试使用固定模型配置与替身，不依赖维护者的 AI 密钥。前端格式检查兼容 Windows 换行，并修正一处既有排版问题。

## 上线前操作

1. 按原发布手册完成前置迁移及 `supabase/release/` 的旧升级包。已完成的部分不要重跑。
2. 在 Supabase SQL Editor 中打开并执行 [202611160001_atomic_ai_grading.sql](../supabase/migrations/202611160001_atomic_ai_grading.sql) 的完整内容。这份迁移不在 2026-11-13 的合并升级包中；不要只粘贴文件路径。
3. 再部署包含本次修复的代码。无需重导题库或重新标注知识点；本地 SQLite 重启时自动补字段。

新增迁移不修改题干、答案或已有作答结果，仅增加快照修订号和服务端事务函数。未执行时 AI 判分无法保存，会返回失败提示，用户仍可自行核对答案并完成自评。

可在 SQL Editor 只读核对结构及权限：

```sql
select column_name
from information_schema.columns
where table_schema='public' and table_name='problem_grading'
  and column_name='submission_revision';

select
  has_function_privilege('service_role',
    'public.record_ai_grading(text,text,integer,integer,date,jsonb)', 'EXECUTE') as service_allowed,
  has_function_privilege('anon',
    'public.record_ai_grading(text,text,integer,integer,date,jsonb)', 'EXECUTE') as anon_allowed,
  has_function_privilege('authenticated',
    'public.record_ai_grading(text,text,integer,integer,date,jsonb)', 'EXECUTE') as authenticated_allowed;
```

应返回新增字段，以及 `true / false / false`。上线后用测试账号确认：提交后 AI 核对可保存、重做不显示上次判分、AI 核对后可自评修正，每日题遵循知识点范围。

若应用需要回滚，新增字段和函数可以保留，不需删除数据。回到修复前的应用也会重新带回旧并发风险；应优先修复或暂时关闭 AI 功能。

## 验证边界

本轮本地后端回归 171 项通过，前端生产构建、格式检查及旧发布包生成一致性检查通过。后端使用本机 Python 3.14.2 执行；项目生产版本声明仍为 Python 3.12，本轮未在 3.12 环境实跑，也未调用真实模型。

回归覆盖过期判分、用户自评、重复请求、事务失败回滚、快照隔离和筛选。SQL 场景见 [atomic_ai_grading.sql](../tests/sql/atomic_ai_grading.sql)，仅供已应用全部迁移的临时测试数据库执行，不能拿生产学习记录做测试。

本次 SQL 在本地 PGlite PostgreSQL 环境核对语法、事务回滚和执行权限；它不等于线上 Supabase/PostgREST 并发与真实模型验收。正式环境仍按上述步骤迁移、部署和核对。
