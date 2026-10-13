-- Clicking "不会" unlocks reference answers without changing progress or check-ins.
create table public.answer_access (
 id text primary key,
 user_id text not null references public.profiles(id),
 problem_id text not null references public.problems(id),
 created_at timestamptz not null,
 unique(user_id,problem_id)
);
alter table public.answer_access enable row level security;
revoke all on public.answer_access from anon,authenticated;
grant all on public.answer_access to service_role;
