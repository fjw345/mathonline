-- ============================================================================
-- 2026-11-13 线上数据库升级：执行前检查（只读，不改任何数据）
--
-- 用法：Supabase Dashboard → SQL Editor → 整段粘贴执行，只跑这一个脚本。
-- 判读：
--   * 「类别 = 前置」出现「缺失」→ 不要继续，先补前置迁移或确认连对项目；
--   * 「本发布状态 = 未执行」→ 接着整段执行 20261113_upgrade.sql；
--   * 「本发布状态 = 部分执行」→ 按「迁移段」列出的缺失段，逐个执行对应迁移文件；
--   * 「本发布状态 = 已全部执行」→ 无需再执行升级脚本。
-- ============================================================================

with checks(ord, kind, section, item, ok, hint) as (
  values
    (1, '前置', '—', 'profiles 表（账号）',
     to_regclass('public.profiles') is not null,
     '缺少时停止，先按首次部署指南执行初始迁移'),
    (2, '前置', '—', 'problems 表（题库）',
     to_regclass('public.problems') is not null,
     '缺少时停止，先按首次部署指南执行初始迁移'),
    (3, '前置', '—', 'problem_answers 表（答案）',
     to_regclass('public.problem_answers') is not null,
     '缺少时停止，先按首次部署指南执行初始迁移'),
    (4, '前置', '—', 'problem_grading 表（AI 判分）',
     to_regclass('public.problem_grading') is not null,
     '缺少时先执行 supabase/migrations/20261110000000_ai_grading.sql'),
    (5, '前置', '—', 'service_role 角色',
     to_regrole('service_role') is not null,
     'Supabase 项目自带；缺失通常说明连错项目'),

    (10, '对象', '001 知识点', '列 problems.knowledge_points',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'problems'
               and column_name = 'knowledge_points'),
     '缺失时执行 202611130001_knowledge_points.sql'),
    (11, '对象', '001 知识点', '列 problems.knowledge_source',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'problems'
               and column_name = 'knowledge_source'),
     '缺失时执行 202611130001_knowledge_points.sql'),
    (12, '对象', '001 知识点', '索引 problems_knowledge',
     exists (select 1 from pg_indexes
             where schemaname = 'public' and indexname = 'problems_knowledge'),
     '缺失时执行 202611130001_knowledge_points.sql'),
    (13, '对象', '001 知识点', '函数 apply_problem_knowledge(jsonb)',
     to_regprocedure('public.apply_problem_knowledge(jsonb)') is not null,
     '缺失时执行 202611130001_knowledge_points.sql'),

    (14, '对象', '002 学情分析', '表 learning_analyses',
     to_regclass('public.learning_analyses') is not null,
     '缺失时执行 202611130002_learning_analysis.sql'),

    (15, '对象', '003 模拟卷', '表 mock_papers',
     to_regclass('public.mock_papers') is not null,
     '缺失时执行 202611130003_mock_exams.sql'),
    (16, '对象', '003 模拟卷', '索引 mock_papers_owner',
     exists (select 1 from pg_indexes
             where schemaname = 'public' and indexname = 'mock_papers_owner'),
     '缺失时执行 202611130003_mock_exams.sql'),

    (17, '对象', '004 变式题', '表 practice_variants',
     to_regclass('public.practice_variants') is not null,
     '缺失时执行 202611130004_practice_variants.sql'),
    (18, '对象', '004 变式题', '索引 variants_owner',
     exists (select 1 from pg_indexes
             where schemaname = 'public' and indexname = 'variants_owner'),
     '缺失时执行 202611130004_practice_variants.sql'),

    (19, '对象', '120000 共享题池', '表 variant_cache',
     to_regclass('public.variant_cache') is not null,
     '缺失时执行 20261113120000_shared_variants.sql（须先完成 004）'),
    (20, '对象', '120000 共享题池', '索引 variant_cache_lookup',
     exists (select 1 from pg_indexes
             where schemaname = 'public' and indexname = 'variant_cache_lookup'),
     '缺失时执行 20261113120000_shared_variants.sql'),
    (21, '对象', '120000 共享题池', '列 practice_variants.cache_id',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'practice_variants'
               and column_name = 'cache_id'),
     '缺失时执行 20261113120000_shared_variants.sql'),
    (22, '对象', '120000 共享题池', '列 practice_variants.source_hash',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'practice_variants'
               and column_name = 'source_hash'),
     '缺失时执行 20261113120000_shared_variants.sql'),
    (23, '对象', '120000 共享题池', '列 practice_variants.reused',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'practice_variants'
               and column_name = 'reused'),
     '缺失时执行 20261113120000_shared_variants.sql'),

    (24, '对象', '130000 图片转写', '表 submission_transcripts',
     to_regclass('public.submission_transcripts') is not null,
     '缺失时执行 20261113130000_image_transcript.sql（须先完成 AI 判分迁移）'),
    (25, '对象', '130000 图片转写', '索引 transcripts_owner',
     exists (select 1 from pg_indexes
             where schemaname = 'public' and indexname = 'transcripts_owner'),
     '缺失时执行 20261113130000_image_transcript.sql'),
    (26, '对象', '130000 图片转写', '列 problem_grading.transcript',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'problem_grading'
               and column_name = 'transcript'),
     '缺失时执行 20261113130000_image_transcript.sql'),

    (27, '对象', '140000 用户密钥', '表 user_ai_keys',
     to_regclass('public.user_ai_keys') is not null,
     '缺失时执行 20261113140000_user_ai_keys.sql'),
    (28, '对象', '140000 用户密钥', '列 user_ai_keys.ciphertext/hint',
     exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'user_ai_keys'
               and column_name = 'ciphertext')
     and exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'user_ai_keys'
               and column_name = 'hint'),
     '缺失时执行 20261113140000_user_ai_keys.sql')
),
summary as (
  select
    count(*) filter (where kind = '前置' and not ok) as blocked,
    count(*) filter (where kind = '对象' and not ok) as missing,
    count(*) filter (where kind = '对象' and ok) as present,
    string_agg(distinct section, '、') filter (where kind = '对象' and not ok)
      as missing_sections
  from checks
)
select
  ord as "序号",
  kind as "类别",
  section as "迁移段",
  item as "检查项",
  case when ok then '通过' else '缺失' end as "结果",
  hint as "说明"
from checks
union all
select
  100,
  '总体',
  '',
  '本发布状态',
  case
    when blocked > 0 then '阻断'
    when missing = 0 then '已全部执行'
    when present = 0 then '未执行'
    else '部分执行'
  end,
  case
    when blocked > 0 then '前置条件不满足：先补齐前置迁移或确认项目，再重新检查'
    when missing = 0 then '无需再执行升级脚本，直接进入部署与冒烟'
    when present = 0 then '可整段执行 supabase/release/20261113_upgrade.sql'
    else '请逐个执行缺失段的迁移文件（' || coalesce(missing_sections, '') ||
         '），不要整段执行升级脚本'
  end
from summary
order by "序号";
