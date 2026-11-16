-- 学情分析报告缓存：统计由服务端每次现算，模型报告只在练习数据指纹变化时
-- 重新生成。纯新增表，不改动既有数据与策略。
begin;

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

notify pgrst, 'reload schema';

commit;
