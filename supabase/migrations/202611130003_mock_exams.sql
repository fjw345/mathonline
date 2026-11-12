-- 北交期末考试预测与模拟卷。一卷一行：蓝图、往年分析、题目和判分结果都放在
-- JSON 列里，创建与交卷各一次写入，避免半张卷子的中间状态。纯新增表。
begin;

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

notify pgrst, 'reload schema';

commit;
