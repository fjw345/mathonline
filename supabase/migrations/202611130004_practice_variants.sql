-- 一题多练：错题的 AI 变式题独立存放，答案在查看前不下发。纯新增表。
begin;

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

notify pgrst, 'reload schema';

commit;
