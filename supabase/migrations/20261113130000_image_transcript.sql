-- 图片作答转写：先把用户上传的作答图片转成文字，再交给既有 AI 判分。
-- 纯新增：不改题目、答案或既有判分记录；未执行时图片作答仍降级为待自检。
begin;

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

notify pgrst, 'reload schema';

commit;
