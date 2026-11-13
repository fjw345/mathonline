-- AI 仿题共享题池：同一道题的生成结果跨用户复用，节省模型调用。
-- 纯新增：不改变已有题目、作答或学习记录；practice_variants 只加两列。
begin;

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

notify pgrst, 'reload schema';

commit;
