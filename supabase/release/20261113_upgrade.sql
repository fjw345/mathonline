-- ============================================================================
-- 数学刷题平台：2026-11-13 线上数据库升级
-- 知识点标签 / AI 学情分析 / 考试预测模拟卷 / 错题 AI 变式 / 共享题池 / 图片作答转写
--
-- 目标：Supabase PostgreSQL（public schema）
--
-- 用法：
--   1. 先执行 20261113_preflight.sql，确认状态为「未执行」、前置条件全部为 true。
--   2. 在 SQL Editor 中整段粘贴执行本文件，只需执行一次。
--   3. 再执行 20261113_postcheck.sql，确认总体结果为 PASS。
--
-- 本文件是一个事务：全部语句成功才提交，任何一步失败整段回滚，不会留下半套结构。
-- 请勿分段执行，请勿重复执行（开头的保护段会检测并报错）。
-- 前置迁移：20261110000000_ai_grading.sql（problem_grading 表）必须已执行。
--
-- 本文件由 scripts/build_release_sql.py 从以下迁移机械合并生成，请勿手工编辑：
--   supabase/migrations/202611130001_knowledge_points.sql
--   supabase/migrations/202611130002_learning_analysis.sql
--   supabase/migrations/202611130003_mock_exams.sql
--   supabase/migrations/202611130004_practice_variants.sql
--   supabase/migrations/20261113120000_shared_variants.sql
--   supabase/migrations/20261113130000_image_transcript.sql
-- 重新生成：python -X utf8 scripts/build_release_sql.py
-- ============================================================================

begin;

-- 保护：本发布只要有任何对象已存在，就停止执行，避免“重复建表”报错扰乱判断。
-- 若确实只完成了部分迁移，请按 preflight 的提示逐个执行缺失的迁移文件。
do $$
begin
  if to_regclass('public.learning_analyses') is not null
     or to_regclass('public.mock_papers') is not null
     or to_regclass('public.practice_variants') is not null
     or to_regclass('public.variant_cache') is not null
     or to_regclass('public.submission_transcripts') is not null
     or exists (
          select 1
          from information_schema.columns
          where table_schema = 'public'
            and table_name = 'problems'
            and column_name = 'knowledge_points'
        )
  then
    raise exception using
      message = '检测到 2026-11-13 发布的对象已存在，已停止执行，数据库未改动。',
      hint = '若上次只完成了部分迁移，请先执行 supabase/release/20261113_preflight.sql 查看缺失段，再逐个执行 supabase/migrations/ 中缺失的文件；不要重复执行本脚本。';
  end if;
end
$$;

-- ==========================================================================
-- 第 1/6 段：知识点列、索引、save_problem 与批量标注函数
-- 源文件：supabase/migrations/202611130001_knowledge_points.sql
-- ==========================================================================
-- Knowledge points (public filter + weak-point analysis) and the internal
-- difficulty used by mock-exam assembly. Additive only: question text, answer
-- text, images, versions and learning records are untouched.

alter table public.problems
  add column if not exists knowledge_points jsonb not null default '[]',
  add column if not exists knowledge_source text not null default '';

-- Contains queries for the knowledge filter; jsonb_path_ops supports @>.
create index if not exists problems_knowledge
  on public.problems using gin (knowledge_points jsonb_path_ops);

-- save_problem: an older client that omits the knowledge fields must not erase
-- existing tags, and a fresh insert must not violate the NOT NULL default.
-- Difficulty is preserved for the same reason: it is internal metadata used by
-- mock-exam assembly, not a rating shown to students.
create or replace function public.save_problem(p_problem jsonb,p_answer jsonb,p_expected integer) returns jsonb language plpgsql security definer set search_path=public as $$
declare current_version integer; p problems; a problem_answers;
begin
 perform pg_advisory_xact_lock(hashtextextended(p_problem->>'id',0));
 select version into current_version from problems where id=p_problem->>'id' for update;
 if coalesce(current_version,0)<>p_expected then raise exception 'stale problem'; end if;
 p:=jsonb_populate_record(null::problems,p_problem);a:=jsonb_populate_record(null::problem_answers,p_answer);
 -- An older client that does not know the text/knowledge fields must not
 -- erase them.
 if not (p_problem ? 'question_text') then
  select question_text into p.question_text from problems where id=p.id;
 end if;
 if not (p_problem ? 'knowledge_points') then
  select knowledge_points into p.knowledge_points from problems where id=p.id;
 end if;
 if not (p_problem ? 'knowledge_source') then
  select knowledge_source into p.knowledge_source from problems where id=p.id;
 end if;
 if not (p_answer ? 'text') then
  select text into a.text from problem_answers where problem_id=a.problem_id;
 end if;
 p.question_text:=coalesce(p.question_text,'');
 p.knowledge_points:=coalesce(p.knowledge_points,'[]'::jsonb);
 p.knowledge_source:=coalesce(p.knowledge_source,'');
 a.text:=coalesce(a.text,'');
 if a.problem_id<>p.id then raise exception 'answer mismatch'; end if;
 if p.version<>p_expected+1 then raise exception 'invalid version'; end if;
 if p.status='published' and jsonb_array_length(a.images)=0 then raise exception 'answer images required'; end if;
 insert into problems select p.* on conflict(id) do update set
   source_id=excluded.source_id,title=excluded.title,type=excluded.type,year=excluded.year,group_name=excluded.group_name,subject=excluded.subject,tags=excluded.tags,
   difficulty=coalesce(excluded.difficulty,problems.difficulty),status=excluded.status,version=excluded.version,
   grading_mode=excluded.grading_mode,question_images=excluded.question_images,question_text=excluded.question_text,
   knowledge_points=excluded.knowledge_points,knowledge_source=excluded.knowledge_source,
   choice_options=excluded.choice_options,multiple=excluded.multiple,verified=excluded.verified,answer_verified=excluded.answer_verified,manual_revision=excluded.manual_revision,updated_at=excluded.updated_at;
 insert into problem_answers select a.* on conflict(problem_id) do update set images=excluded.images,text=excluded.text,correct_options=excluded.correct_options,provenance=excluded.provenance;
 return to_jsonb(p);
end $$;

-- Bulk knowledge backfill used by scripts/tag_knowledge.py. Only the three
-- metadata columns change; no version bump, no updated_at change.
create or replace function public.apply_problem_knowledge(p_items jsonb)
returns integer language plpgsql security definer set search_path=public as $$
declare item jsonb; codes jsonb; updated integer:=0;
begin
 if jsonb_typeof(p_items)<>'array' then raise exception 'items must be an array'; end if;
 for item in select value from jsonb_array_elements(p_items) as t(value) loop
  codes:=coalesce(item->'knowledge_points','[]'::jsonb);
  if jsonb_typeof(codes)<>'array' then raise exception 'knowledge_points must be an array'; end if;
  if jsonb_array_length(codes)>4 then raise exception 'too many knowledge points'; end if;
  if length(coalesce(item->>'knowledge_source',''))>40 then raise exception 'knowledge source too long'; end if;
  perform pg_advisory_xact_lock(hashtextextended(item->>'id',0));
  update problems set
    knowledge_points=case when item ? 'knowledge_points' then codes else knowledge_points end,
    knowledge_source=case when item ? 'knowledge_source' then coalesce(item->>'knowledge_source','') else knowledge_source end,
    difficulty=case when item ? 'difficulty' then nullif(item->>'difficulty','')::integer else difficulty end
  where id=item->>'id';
  if found then updated:=updated+1; end if;
 end loop;
 return updated;
end $$;

revoke all on function public.apply_problem_knowledge(jsonb) from public,anon,authenticated;
grant execute on function public.apply_problem_knowledge(jsonb) to service_role;

-- Make the new columns and function visible to PostgREST immediately.

-- ==========================================================================
-- 第 2/6 段：学情分析报告缓存表
-- 源文件：supabase/migrations/202611130002_learning_analysis.sql
-- ==========================================================================
-- 学情分析报告缓存：统计由服务端每次现算，模型报告只在练习数据指纹变化时
-- 重新生成。纯新增表，不改动既有数据与策略。

create table public.learning_analyses (
 id text primary key references public.profiles(id) on delete cascade,
 fingerprint text not null,
 stats jsonb not null default '{}',
 report jsonb not null default '{}',
 model text not null default '',
 created_at timestamptz not null
);

alter table public.learning_analyses enable row level security;
revoke all on public.learning_analyses from public, anon, authenticated;
grant select, insert, update, delete on public.learning_analyses to service_role;

-- ==========================================================================
-- 第 3/6 段：考试预测模拟卷表
-- 源文件：supabase/migrations/202611130003_mock_exams.sql
-- ==========================================================================
-- 北交期末考试预测与模拟卷。一卷一行：蓝图、往年分析、题目和判分结果都放在
-- JSON 列里，创建与交卷各一次写入，避免半张卷子的中间状态。纯新增表。

create table public.mock_papers (
 id text primary key,
 user_id text not null references public.profiles(id) on delete cascade,
 subject text not null,
 title text not null,
 status text not null default 'ready' check(status in ('ready','submitted')),
 blueprint jsonb not null default '{}',
 profile jsonb not null default '{}',
 duration_minutes integer not null default 120,
 max_score integer not null default 0,
 score integer not null default 0,
 items jsonb not null default '[]',
 knowledge_summary jsonb not null default '[]',
 submitted_at timestamptz,
 model text not null default '',
 created_at timestamptz not null,
 updated_at timestamptz not null
);

create index mock_papers_owner on public.mock_papers(user_id, created_at desc);

alter table public.mock_papers enable row level security;
revoke all on public.mock_papers from public, anon, authenticated;
grant select, insert, update, delete on public.mock_papers to service_role;

-- ==========================================================================
-- 第 4/6 段：一题多练变式题表
-- 源文件：supabase/migrations/202611130004_practice_variants.sql
-- ==========================================================================
-- 一题多练：错题的 AI 变式题独立存放，答案在查看前不下发。纯新增表。

create table public.practice_variants (
 id text primary key,
 user_id text not null references public.profiles(id) on delete cascade,
 problem_id text not null references public.problems(id),
 subject text not null,
 type text not null check(type in ('choice','blank','solution')),
 knowledge_points jsonb not null default '[]',
 question_text text not null,
 choice_options jsonb not null default '[]',
 multiple boolean not null default false,
 answer_text text not null default '',
 correct_options jsonb not null default '[]',
 explanation text not null default '',
 model text not null default '',
 result text not null default '' check(result in ('','correct','incorrect')),
 created_at timestamptz not null,
 answered_at timestamptz
);

create index variants_owner
 on public.practice_variants(user_id, problem_id, created_at desc);

alter table public.practice_variants enable row level security;
revoke all on public.practice_variants from public, anon, authenticated;
grant select, insert, update, delete on public.practice_variants to service_role;

-- ==========================================================================
-- 第 5/6 段：AI 仿题共享题池与变式题新增列
-- 源文件：supabase/migrations/20261113120000_shared_variants.sql
-- ==========================================================================
-- AI 仿题共享题池：同一道题的生成结果跨用户复用，节省模型调用。
-- 纯新增：不改变已有题目、作答或学习记录；practice_variants 只加两列。

create table public.variant_cache (
 id text primary key,
 problem_id text not null references public.problems(id) on delete cascade,
 source_hash text not null,
 type text not null check(type in ('choice','blank','solution')),
 knowledge_points jsonb not null default '[]',
 question_text text not null,
 choice_options jsonb not null default '[]',
 multiple boolean not null default false,
 answer_text text not null default '',
 correct_options jsonb not null default '[]',
 explanation text not null default '',
 model text not null default '',
 use_count integer not null default 0,
 created_at timestamptz not null,
 last_used_at timestamptz
);

create index variant_cache_lookup
 on public.variant_cache(problem_id, source_hash, type);

-- 迁移顺序在 004 之后；若 004 未执行，本迁移会整体回滚，先补 004 再重跑即可。
alter table public.practice_variants add column if not exists cache_id text;
alter table public.practice_variants add column if not exists source_hash text not null default '';
alter table public.practice_variants add column if not exists reused boolean not null default false;

alter table public.variant_cache enable row level security;
revoke all on public.variant_cache from public, anon, authenticated;
grant select, insert, update, delete on public.variant_cache to service_role;

-- ==========================================================================
-- 第 6/6 段：图片作答转写表与判分记录新增列
-- 源文件：supabase/migrations/20261113130000_image_transcript.sql
-- ==========================================================================
-- 图片作答转写：先把用户上传的作答图片转成文字，再交给既有 AI 判分。
-- 纯新增：不改题目、答案或既有判分记录；未执行时图片作答仍降级为待自检。

create table public.submission_transcripts (
 id text primary key,
 user_id text not null references public.profiles(id) on delete cascade,
 problem_id text not null references public.problems(id) on delete cascade,
 revision integer not null,
 image_ids jsonb not null default '[]',
 text text not null,
 model text not null default '',
 created_at timestamptz not null
);
create index transcripts_owner
 on public.submission_transcripts(user_id, problem_id);

alter table public.problem_grading
 add column if not exists transcript text not null default '';

alter table public.submission_transcripts enable row level security;
revoke all on public.submission_transcripts from public, anon, authenticated;
grant select, insert, update, delete on public.submission_transcripts to service_role;

-- 全部结构创建完成；NOTIFY 会在事务提交后送达 PostgREST，使新表立即可用。

notify pgrst, 'reload schema';

commit;
