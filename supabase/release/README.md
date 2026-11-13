# 2026-11-13 线上数据库升级包

给部署人员用的三个 SQL 文件，按顺序在 Supabase Dashboard 的 SQL Editor 里整段执行：

| 文件 | 作用 | 是否改数据 |
| --- | --- | --- |
| `20261113_preflight.sql` | 执行前检查：前置条件、哪些对象已存在、本发布状态 | 否（只读） |
| `20261113_upgrade.sql` | 整段升级：知识点列/函数、学情缓存、模拟卷、变式题、共享题池、图片转写 | 是（纯新增结构） |
| `20261113_postcheck.sql` | 执行后校验 26 项，最后一行 PASS/FAIL | 否（只读） |

完整步骤、环境变量、知识点标注、冒烟清单与回滚见
[2026-11-13 线上发布手册](../../docs/release-2026-11-13-ops.md)。

## 注意

- `20261113_upgrade.sql` 是一个事务，成功才提交；失败整段回滚，不会留下半套结构。
- 整段脚本只适合“这 6 份迁移都还没执行”的项目。若 preflight 显示“部分执行”，
  请按它指出的缺失段，逐个执行 `supabase/migrations/` 下对应的迁移文件。
- 脚本由 `scripts/build_release_sql.py` 从迁移文件机械合并生成。修改迁移后运行
  `python -X utf8 scripts/build_release_sql.py` 重新生成，不要手改生成结果。
- 前置迁移 `20261110000000_ai_grading.sql` 必须已执行（preflight 会检查）。
- 项目线上只配置了 Data API 的 service_role 密钥，Data API 不能执行 DDL，所以这三个
  文件按 Supabase SQL Editor 设计；维护者也可用 psql 直连按顺序执行，效果相同。
