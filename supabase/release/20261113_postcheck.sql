-- ============================================================================
-- 2026-11-13 线上数据库升级：执行后校验（只读，不改任何数据）
--
-- 用法：Supabase Dashboard → SQL Editor → 整段粘贴执行（在 upgrade 脚本成功之后）。
-- 判读：最后一行「总体结果」为 PASS 才能继续发布；FAIL 时按「说明」逐项排查。
-- 本脚本只读系统目录，不会因缺表而报错；升级未执行时会直接给出 FAIL。
-- ============================================================================

with checks(ord, item, ok, hint) as (
  values
    (1, '表 learning_analyses 存在',
     to_regclass('public.learning_analyses') is not null,
     '001、002 迁移是否执行'),
    (2, '表 mock_papers 存在',
     to_regclass('public.mock_papers') is not null,
     '003 迁移是否执行'),
    (3, '表 practice_variants 存在',
     to_regclass('public.practice_variants') is not null,
     '004 迁移是否执行'),
    (4, '表 variant_cache 存在',
     to_regclass('public.variant_cache') is not null,
     '120000 迁移是否执行'),
    (5, '表 submission_transcripts 存在',
     to_regclass('public.submission_transcripts') is not null,
     '130000 迁移是否执行'),

    (6, '列 problems.knowledge_points 存在',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'problems'
               and column_name = 'knowledge_points'),
     '001 迁移是否执行'),
    (7, '列 problems.knowledge_source 存在',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'problems'
               and column_name = 'knowledge_source'),
     '001 迁移是否执行'),
    (8, '列 practice_variants.cache_id 存在',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'practice_variants'
               and column_name = 'cache_id'),
     '120000 迁移是否执行'),
    (9, '列 practice_variants.source_hash 存在',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'practice_variants'
               and column_name = 'source_hash'),
     '120000 迁移是否执行'),
    (10, '列 practice_variants.reused 存在',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'practice_variants'
               and column_name = 'reused'),
     '120000 迁移是否执行'),
    (11, '列 problem_grading.transcript 存在',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'problem_grading'
               and column_name = 'transcript'),
     '130000 迁移是否执行'),

    (12, '索引 problems_knowledge 存在',
     exists (select 1 from pg_indexes
             where schemaname = 'public' and indexname = 'problems_knowledge'),
     '按知识点筛选依赖该 GIN 索引'),
    (13, '索引 mock_papers_owner 存在',
     exists (select 1 from pg_indexes
             where schemaname = 'public' and indexname = 'mock_papers_owner'),
     '003 迁移是否执行'),
    (14, '索引 variants_owner 存在',
     exists (select 1 from pg_indexes
             where schemaname = 'public' and indexname = 'variants_owner'),
     '004 迁移是否执行'),
    (15, '索引 variant_cache_lookup 存在',
     exists (select 1 from pg_indexes
             where schemaname = 'public' and indexname = 'variant_cache_lookup'),
     '题池按题查找依赖该索引'),
    (16, '索引 transcripts_owner 存在',
     exists (select 1 from pg_indexes
             where schemaname = 'public' and indexname = 'transcripts_owner'),
     '转写缓存依赖该索引'),

    (17, '函数 apply_problem_knowledge(jsonb) 存在',
     to_regprocedure('public.apply_problem_knowledge(jsonb)') is not null,
     '批量标注脚本通过该函数写库'),
    (18, '函数 save_problem 已包含知识点保护',
     exists (select 1 from pg_proc p
             join pg_namespace n on n.oid = p.pronamespace
             where n.nspname = 'public' and p.proname = 'save_problem'
               and p.prosrc like '%knowledge_points%'),
     '旧客户端保存题目时不能清空已有标签'),

    (19, '6 张新表均已启用 RLS',
     (select count(*) from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public'
        and c.relname in ('learning_analyses', 'mock_papers', 'practice_variants',
                          'variant_cache', 'submission_transcripts', 'user_ai_keys')
        and c.relrowsecurity) = 6,
     '未启用 RLS 会导致浏览器角色越权访问'),

    (20, 'anon / authenticated 对新表无任何授权',
     not exists (
       select 1
       from pg_class c
       join pg_namespace n on n.oid = c.relnamespace
       cross join lateral aclexplode(coalesce(c.relacl, '{}'::aclitem[])) a
       left join pg_roles r on r.oid = a.grantee
       where n.nspname = 'public'
         and c.relname in ('learning_analyses', 'mock_papers', 'practice_variants',
                           'variant_cache', 'submission_transcripts', 'user_ai_keys')
         and coalesce(r.rolname, 'public') in ('anon', 'authenticated')
     ),
     '迁移应 revoke all from public, anon, authenticated'),

    (21, 'service_role 对 6 张新表有读写授权',
     (select count(*) from (
        select c.relname
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        cross join lateral aclexplode(coalesce(c.relacl, '{}'::aclitem[])) a
        join pg_roles r on r.oid = a.grantee
        where n.nspname = 'public'
          and c.relname in ('learning_analyses', 'mock_papers', 'practice_variants',
                            'variant_cache', 'submission_transcripts', 'user_ai_keys')
          and r.rolname = 'service_role'
          and a.privilege_type in ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
        group by c.relname
        having count(distinct a.privilege_type) = 4
      ) t) = 6,
     '后端以 service_role 读写业务表'),

    (22, 'anon 不能执行 apply_problem_knowledge',
     case
       when to_regprocedure('public.apply_problem_knowledge(jsonb)') is null then false
       else not exists (
         select 1
         from pg_proc p
         join pg_namespace n on n.oid = p.pronamespace
         cross join lateral aclexplode(coalesce(p.proacl, '{}'::aclitem[])) a
         left join pg_roles r on r.oid = a.grantee
         where n.nspname = 'public' and p.proname = 'apply_problem_knowledge'
           and coalesce(r.rolname, 'public') in ('anon', 'authenticated')
       )
     end,
     '只允许服务端批量标注'),

    (23, 'service_role 可以执行 apply_problem_knowledge',
     case
       when to_regprocedure('public.apply_problem_knowledge(jsonb)') is null then false
       else exists (
         select 1
         from pg_proc p
         join pg_namespace n on n.oid = p.pronamespace
         cross join lateral aclexplode(coalesce(p.proacl, '{}'::aclitem[])) a
         join pg_roles r on r.oid = a.grantee
         where n.nspname = 'public' and p.proname = 'apply_problem_knowledge'
           and r.rolname = 'service_role'
           and a.privilege_type = 'EXECUTE'
       )
     end,
     '标注脚本以 service_role 调用'),

    (24, 'problems.knowledge_points 为 NOT NULL',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'problems'
               and column_name = 'knowledge_points' and is_nullable = 'NO'),
     '默认值应为 []'),
    (25, 'problems.knowledge_source 为 NOT NULL',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'problems'
               and column_name = 'knowledge_source' and is_nullable = 'NO'),
     '默认值应为空串'),

    (26, 'practice_variants 的 cache_id/source_hash/reused 列类型正确',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'practice_variants'
               and column_name = 'cache_id' and data_type = 'text')
     and exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'practice_variants'
               and column_name = 'source_hash' and data_type = 'text')
     and exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'practice_variants'
               and column_name = 'reused' and data_type = 'boolean'),
     '共享题池依赖这三列'),

    (27, '表 user_ai_keys 存在',
     to_regclass('public.user_ai_keys') is not null,
     '140000 迁移是否执行'),
    (28, 'user_ai_keys 的密文与掩码列存在',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'user_ai_keys'
               and column_name = 'ciphertext')
     and exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'user_ai_keys'
               and column_name = 'hint'),
     '只保存 AES-GCM 密文与末四位掩码')
),
summary as (
  select
    count(*) filter (where not ok) as failed,
    bool_and(ok) as passed
  from checks
)
select
  ord as "序号",
  item as "检查项",
  case when ok then 'OK' else '未通过' end as "结果",
  hint as "说明"
from checks
union all
select
  100,
  '总体结果',
  case when passed then 'PASS' else 'FAIL' end,
  case when passed then '结构检查全部通过，继续部署代码与知识点标注'
       else '共 ' || failed || ' 项未通过：先不要发布，按上面的说明排查'
  end
from summary
order by "序号";
